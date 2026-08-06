"""Companies House lookup — the friendly front door to the SIC class route.

Feedback (Jonathan, 15 Jul): typing SIC codes is a poor UI. Instead the client
(or the team) searches for a company by name or number, we pull its SIC codes
from Companies House automatically, and map those to Nice classes. Nobody ever
types a SIC code.

Companies House is used directly (not via Temmy) so it works for ANY UK
company, including ones with no trademarks. Free API; key in
`COMPANIES_HOUSE_API_KEY` (HTTP Basic, key as username, blank password).
"""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
import urllib.error

CH_BASE = 'https://api.company-information.service.gov.uk'


def _auth_header(key: str) -> str:
    return 'Basic ' + base64.b64encode((key + ':').encode()).decode()


def _ch_get(path: str, *, key: str, timeout: int = 20) -> dict | None:
    req = urllib.request.Request(CH_BASE + path,
                                 headers={'Authorization': _auth_header(key)})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def _api_key(key: str | None = None) -> str:
    return (key or os.environ.get('COMPANIES_HOUSE_API_KEY', '')).strip()


def _one_line(addr: dict) -> str:
    if not isinstance(addr, dict):
        return ''
    bits = [addr.get('address_line_1'), addr.get('locality'),
            addr.get('postal_code')]
    return ', '.join(str(b) for b in bits if b)


def search_companies(query: str, *, key: str | None = None,
                     limit: int = 8) -> dict:
    """Search Companies House by name or number.

    Returns {results: [{number, name, status, address}]}. A pure-digit query is
    treated as a company-number lookup (exact), otherwise a name search.
    """
    k = _api_key(key)
    q = (query or '').strip()
    if not k or not q:
        return {'results': []}

    # Company-number lookup when the query is (mostly) digits.
    if q.replace(' ', '').isalnum() and any(ch.isdigit() for ch in q) \
            and len(q.replace(' ', '')) >= 6 and q.replace(' ', '').isdigit():
        p = _ch_get('/company/' + urllib.parse.quote(q.replace(' ', '')), key=k)
        if p:
            return {'results': [{
                'number': p.get('company_number'),
                'name': p.get('company_name'),
                'status': p.get('company_status'),
                'address': _one_line(p.get('registered_office_address') or {}),
            }]}
        return {'results': []}

    body = _ch_get('/search/companies?' + urllib.parse.urlencode(
        {'q': q, 'items_per_page': limit}), key=k) or {}
    out = []
    for it in body.get('items', [])[:limit]:
        out.append({
            'number': it.get('company_number'),
            'name': it.get('title'),
            'status': it.get('company_status'),
            'address': it.get('address_snippet') or _one_line(it.get('address') or {}),
        })
    return {'results': out}


def company_sic(number: str, *, key: str | None = None) -> dict | None:
    """Full company profile with its SIC codes."""
    k = _api_key(key)
    if not k or not number:
        return None
    p = _ch_get('/company/' + urllib.parse.quote(str(number).strip()), key=k)
    if not p:
        return None
    return {
        'number': p.get('company_number'),
        'name': p.get('company_name'),
        'status': p.get('company_status'),
        'sic_codes': list(p.get('sic_codes') or []),
        'address': _one_line(p.get('registered_office_address') or {}),
    }


def company_classes(number: str, *, key: str | None = None) -> dict | None:
    """Company number -> its SIC codes -> Nice classes (+ term_basket).

    The whole point of the redesign: the client gives us a company, we do the
    SIC lookup and the class mapping. Uses the empirical seed where available,
    the concordance otherwise (see sic_engine).
    """
    from . import sic_engine
    prof = company_sic(number, key=key)
    if prof is None:
        return None
    mapping = sic_engine.map_sic_codes(prof['sic_codes'])
    return {
        'company': {k: prof[k] for k in ('number', 'name', 'status', 'address')},
        'sic_codes': prof['sic_codes'],
        **mapping,
        'basket': sic_engine.to_basket(mapping).to_dict(),
    }
