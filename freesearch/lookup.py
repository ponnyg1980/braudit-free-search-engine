"""Trademark / owner lookup resolver — the reusable search-bar backend.

One small resolver powering a standalone search bar used in several places:
route 2d (competitor classes & terms), online renewals, "find my applicant /
trademark ID for the portal", and a quick "check your trademark status".

Two modes, mirroring the UI:

  TRADEMARK  — by name (word-mark text) or by application number
  OWNER      — by name (applicant) or by IPO identifier

All of it runs on the standard Temmy API (`X-API-Key`), which is live and fast.
Results are shaped for direct rendering in the widget: the pre-selection list
columns the UI shows, then a detail fetch once the user picks one.

Closest-match ordering: Temmy's search is prefix-based; we additionally sort by
fuzzy similarity to the query so the nearest name floats to the top, which is
what "standard matching, closest at the top" means to a client.
"""
from __future__ import annotations

import difflib
import re

# A UK trademark application number: 'UK' + digits, or a bare digit string,
# optionally with the leading zeros. We treat a query as a NUMBER search when
# it is mostly digits (optionally prefixed by a 2-letter office code).
_NUMBER_RE = re.compile(r'^\s*([A-Z]{2})?0*\d{3,}\s*$', re.I)


def looks_like_number(q: str) -> bool:
    return bool(_NUMBER_RE.match(q or ''))


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or '').lower(),
                                   (b or '').lower()).ratio()


def _s(v) -> str:
    return '' if v is None else str(v).strip()


def _mark_name(rec: dict) -> str:
    mark = rec.get('mark') or {}
    return (_s(rec.get('verbal_element_text'))
            or _s(mark.get('verbal_element_text'))
            or _s(rec.get('mark_text')))


def _applicant_names(rec: dict) -> str:
    apps = rec.get('applicants') or []
    names = [_s(a.get('name')) for a in apps
             if isinstance(a, dict) and _s(a.get('name'))]
    return '; '.join(names)


def _address_oneline(addr: dict) -> str:
    if not isinstance(addr, dict):
        return ''
    bits = [addr.get('line_1'), addr.get('line_2'), addr.get('locality'),
            addr.get('region')]
    return ', '.join(_s(b) for b in bits if _s(b))


# ---------------------------------------------------------------------------
# TRADEMARK mode
# ---------------------------------------------------------------------------

def search_marks(client, query: str, *, limit: int = 10) -> dict:
    """Mode 1: resolve a trademark by name (1a) or number (1b).

    Returns a list shaped for the pre-selection UI columns:
        number · name · status · applicant
    Number queries do an exact lookup; name queries a fuzzy-ranked prefix
    search.
    """
    query = (query or '').strip()
    if not query:
        return {'mode': 'trademark', 'query': query, 'results': []}

    results: list[dict] = []

    if looks_like_number(query):
        detail = None
        try:
            detail = client.get_trademark(query)
        except Exception:
            detail = None
        if detail:
            results.append(_mark_row(detail))
    else:
        try:
            body = client.search_trademarks(text=query, limit=max(limit, 25))
        except Exception:
            body = {}
        items = (body or {}).get('items', [])
        rows = [_mark_row(it) for it in items]
        rows.sort(key=lambda r: _sim(query, r['name']), reverse=True)
        results = rows[:limit]

    return {'mode': 'trademark', 'query': query, 'results': results}


def _mark_row(rec: dict) -> dict:
    return {
        'number': _s(rec.get('application_number')),
        'name': _mark_name(rec),
        'status': _s(rec.get('status')),
        'applicant': _applicant_names(rec),
    }


def get_mark(client, number: str) -> dict | None:
    """Full detail for a chosen trademark: classes + per-class terms.

    This is the payload route 2d hands to `term_basket.from_trademark_detail`.
    """
    try:
        detail = client.get_trademark(number)
    except Exception:
        detail = None
    if not detail:
        return None
    classes = []
    for c in (detail.get('nice_class_trademarks') or []):
        if not isinstance(c, dict):
            continue
        num = c.get('number') or c.get('nice_class')
        if num is None:
            continue
        classes.append({
            'nice_class': int(num),
            'specification': _s(c.get('goods_services_description')),
        })
    if not classes:
        classes = [{'nice_class': int(n), 'specification': ''}
                   for n in (detail.get('classes') or [])]
    return {
        'number': _s(detail.get('application_number')),
        'name': _mark_name(detail),
        'status': _s(detail.get('status')),
        'applicant': _applicant_names(detail),
        'classes': classes,
        '_detail': detail,          # kept so the caller can build a basket
    }


# ---------------------------------------------------------------------------
# OWNER mode
# ---------------------------------------------------------------------------

def search_owners(client, query: str, *, limit: int = 10) -> dict:
    """Mode 2: resolve an owner by name (2a) or IPO identifier (2b).

    Pre-selection columns: applicant name · address · postcode.
    """
    query = (query or '').strip()
    if not query:
        return {'mode': 'owner', 'query': query, 'results': []}

    try:
        if looks_like_number(query):
            body = client.search_applicants(
                ipo_identifier=int(re.sub(r'\D', '', query)), limit=limit)
        else:
            body = client.search_applicants(name=query, limit=max(limit, 25))
    except Exception:
        body = {}

    rows = []
    for it in (body or {}).get('items', []):
        a = it.get('applicant') or {}
        addr = a.get('address') or {}
        rows.append({
            'ipo_identifier': a.get('ipo_identifier'),
            'name': _s(a.get('name')),
            'address': _address_oneline(addr),
            'postcode': _s(addr.get('postcode')),
            'trademark_count': len(it.get('trademarks') or []),
        })
    if not looks_like_number(query):
        rows.sort(key=lambda r: _sim(query, r['name']), reverse=True)
    return {'mode': 'owner', 'query': query, 'results': rows[:limit]}


def get_owner(client, ipo_identifier) -> dict | None:
    """A chosen owner's trademark list: number · name · status (+ classes).

    The applicant response already carries each trademark's classes, so the
    list is renderable without a per-trademark detail fetch. Terms still come
    from `get_mark` once the client picks the specific trademark (2d).
    """
    try:
        body = client.search_applicants(ipo_identifier=int(ipo_identifier),
                                        limit=1)
    except Exception:
        body = {}
    items = (body or {}).get('items', [])
    if not items:
        return None
    a = items[0].get('applicant') or {}
    addr = a.get('address') or {}
    tms = []
    for t in (items[0].get('trademarks') or []):
        if not isinstance(t, dict):
            continue
        tms.append({
            'number': _s(t.get('application_number')),
            'name': _mark_name(t),
            'status': _s(t.get('status')),
            'classes': [int(n) for n in (t.get('classes') or [])
                        if str(n).isdigit()],
        })
    return {
        'owner': {
            'ipo_identifier': a.get('ipo_identifier'),
            'name': _s(a.get('name')),
            'address': _address_oneline(addr),
            'postcode': _s(addr.get('postcode')),
            'company_number': _s(a.get('company_number')),
        },
        'trademarks': tms,
    }
