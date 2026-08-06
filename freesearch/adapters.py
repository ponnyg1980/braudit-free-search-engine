"""Source adapters: xlsx row tuple / Temmy API record -> MarkRecord.

`from_xlsx_row` exists purely so the parity test can prove the new scoring
path reproduces production audit scores exactly. It encodes the positional
contract that filters.py relies on today:

    r[2] = status      r[3] = mark type
    r[5] = mark text   r[7] = class string

`from_temmy_item` is the new capability — the thing that was missing. Field
names are taken from `temmy.verify_uk_record_via_temmy`, which is the only
place in the codebase that has read a live Temmy record end to end.

Temmy's search envelope is documented as "additional database fields may
appear", and the search endpoint may return a leaner item than the detail
endpoint. Every accessor below is therefore defensive: try the known names,
fall back, never raise on a missing field. A record we can't read the mark
text from is dropped by the service rather than scored as a blank string.
"""
from __future__ import annotations

import re

from .models import CompanyInfo, MarkRecord

UKIPO_CASE_URL = 'https://trademarks.ipo.gov.uk/ipo-tmcase/page/Results/1/{}'


def _s(v) -> str:
    if v is None:
        return ''
    return str(v).strip()


def _first(d: dict, *keys) -> str:
    for k in keys:
        v = _s(d.get(k))
        if v:
            return v
    return ''


def parse_class_string(cls_str) -> list[int]:
    """'9, 25' / '9 25' / 9 -> [9, 25]. Mirrors filters' regex split."""
    if cls_str is None:
        return []
    parts = re.split(r'[,\s]+', str(cls_str))
    return sorted({int(p) for p in parts if p.strip().isdigit()})


# ---------------------------------------------------------------------------
# xlsx (legacy audit path) — parity anchor only
# ---------------------------------------------------------------------------

def from_xlsx_row(r: tuple) -> MarkRecord:
    """Build a MarkRecord from a Trademarks-sheet row tuple.

    Positional indices are the existing, undocumented contract in filters.py.
    Naming them here is half the value of this module.
    """
    def cell(i):
        return _s(r[i]) if len(r) > i else ''

    return MarkRecord(
        status=cell(2),
        mark_type=cell(3),
        mark_text=cell(5),
        classes=parse_class_string(cell(7)),
        source='xlsx',
    )


# ---------------------------------------------------------------------------
# Temmy API (free search path) — the new bit
# ---------------------------------------------------------------------------

# Live Temmy status vocabulary -> the canonical set filters.py/scoring.py grade.
# Confirmed against live data (09 Jul 2026): Registered, Application Published,
# Opposed are live; Dead, Refused, Withdrawn, Expired, Cancelled, Surrendered,
# Removed are ended. Normalising here (not in the scorer) keeps scoring
# parity-locked while letting the free tier read real Temmy statuses.
_STATUS_MAP = {
    'registered': 'Registered',
    'application published': 'Pending',
    'published': 'Pending',
    'opposed': 'Pending',
    'filed': 'Pending',
    'pending': 'Pending',
    'examination': 'Pending',
    'dead': 'Ended', 'refused': 'Ended', 'withdrawn': 'Ended',
    'expired': 'Ended', 'cancelled': 'Ended', 'surrendered': 'Ended',
    'removed': 'Ended',
}


def canon_status(raw: str) -> str:
    """Map a raw Temmy status to Registered | Pending | Ended.

    Unknown statuses fall back to 'Ended' (score 0 / Negligible) — the safe
    direction: never inflate an unrecognised status into a live threat.
    """
    return _STATUS_MAP.get(_s(raw).lower(), 'Ended')


def _temmy_classes(rec: dict) -> list[int]:
    """Prefer the flat `classes` list (detail records expose `[41, 43]`);
    fall back to `nice_class_trademarks[].number` (the Nice class number —
    NOT `nice_class_id`, which is an internal PK)."""
    out: list[int] = []
    flat = rec.get('classes') or []
    if isinstance(flat, list):
        for n in flat:
            try:
                out.append(int(n))
            except (TypeError, ValueError):
                continue
    if not out:
        for c in (rec.get('nice_class_trademarks') or []):
            if not isinstance(c, dict):
                continue
            n = c.get('number') or c.get('nice_class') or c.get('class')
            try:
                out.append(int(n))
            except (TypeError, ValueError):
                continue
    return sorted(set(out))


def _temmy_goods_services(rec: dict) -> list[str]:
    """Recital per Nice class from `nice_class_trademarks`. Live fields are
    `goods_services_description` and `number`."""
    out: list[str] = []
    for c in (rec.get('nice_class_trademarks') or []):
        if not isinstance(c, dict):
            continue
        text = _first(c, 'goods_services_description', 'goods_services_text',
                      'goods_services', 'description', 'text')
        if not text:
            continue
        cls = c.get('number') or c.get('nice_class') or c.get('class')
        out.append(f'Class {cls} — {text}' if cls else text)
    return out


def _temmy_owner(rec: dict) -> str:
    applicants = rec.get('applicants') or []
    if not isinstance(applicants, list):
        return ''
    names = [_s(a.get('name')) for a in applicants
             if isinstance(a, dict) and _s(a.get('name'))]
    return '; '.join(names)


def _sic_list(v) -> list[str]:
    """`sic_codes` is a Postgres varchar[] — may arrive as list or string."""
    if isinstance(v, list):
        return [_s(x) for x in v if _s(x)]
    s = _s(v).strip('{}')
    return [p.strip().strip('"') for p in s.split(',') if p.strip()] if s else []


def _company_from_ch(ch: dict, addr: dict) -> CompanyInfo | None:
    """Build CompanyInfo from a Companies House data block.

    Live shape (confirmed 09 Jul 2026): each applicant carries
    `json_attributes.companies_house_data[]` with name, number, sic_codes,
    business_type, company_status, incorporation_date, match_on_company_number.
    Locality/postcode come from the applicant `address` object.
    """
    if not isinstance(ch, dict):
        return None
    if not isinstance(addr, dict):
        addr = {}
    ci = CompanyInfo(
        name=_first(ch, 'name', 'company_name'),
        number=_first(ch, 'number', 'company_number'),
        status=_first(ch, 'company_status', 'status'),
        business_type=_first(ch, 'business_type', 'company_type', 'type'),
        incorporation_date=_first(ch, 'incorporation_date',
                                  'date_of_creation').split('T', 1)[0],
        sic_codes=_sic_list(ch.get('sic_codes') or ch.get('sic')),
        locality=_first(addr, 'locality', 'city', 'town', 'address_locality'),
        postcode=_first(addr, 'postcode', 'postal_code'),
        matched=bool(ch.get('match_on_company_number')
                     or ch.get('matched_on_company_number')),
    )
    if not ci.name and not ci.number:
        return None
    return ci


def _temmy_companies(rec: dict) -> list[CompanyInfo]:
    """Every CH-linked company across the record's applicants.

    Primary source is `applicant.json_attributes.companies_house_data[]`.
    Falls back to flat `company_number` on the applicant (search items and
    un-enriched records), so a company number still surfaces even before CH
    enrichment has run on the Temmy side.
    """
    out: list[CompanyInfo] = []
    for a in (rec.get('applicants') or []):
        if not isinstance(a, dict):
            continue
        addr = a.get('address') if isinstance(a.get('address'), dict) else {}
        ja = a.get('json_attributes') or {}
        ch_list = ja.get('companies_house_data') if isinstance(ja, dict) else None
        made = False
        if isinstance(ch_list, list):
            for ch in ch_list:
                ci = _company_from_ch(ch, addr)
                if ci is not None:
                    out.append(ci)
                    made = True
        if not made and _s(a.get('company_number')):
            # Minimal fallback: number only, from the applicant row.
            out.append(CompanyInfo(number=_s(a.get('company_number')),
                                   name=_s(a.get('name')),
                                   locality=_first(addr, 'locality', 'city')))
    return out


def _temmy_filing_date(rec: dict) -> str:
    raw = _first(rec, 'application_date_time', 'application_date')
    if not raw:
        return ''
    return raw.split('T', 1)[0].split(' ', 1)[0]


def _temmy_mark_type(rec: dict) -> str:
    """Temmy exposes the feature type under `mark.feature`.

    Normalised to the vocabulary the scorer branches on: Word / Figurative /
    Combined / Stylized. Unknown values pass through unchanged and simply
    score 0 on the type component rather than crashing.
    """
    mark = rec.get('mark') or {}
    # Live: `mark_type` is a top-level field on both search and detail records.
    feature = (_first(rec, 'mark_type', 'mark_feature_type')
               or _s(mark.get('feature')))
    f = feature.lower()
    if not f:
        return ''
    # 'Figurative & Text' / word+device = a combined mark.
    if 'combin' in f or ('fig' in f and 'text' in f) or ('word' in f and 'fig' in f):
        return 'Combined'
    if 'figur' in f or 'device' in f or 'image' in f:
        return 'Figurative'
    if 'stylis' in f or 'styliz' in f:
        return 'Stylized'
    if f == 'word' or 'verbal' in f:
        return 'Word'
    return feature


def _temmy_mark_text(rec: dict) -> str:
    mark = rec.get('mark') or {}
    return (_s(rec.get('verbal_element_text'))
            or _s(mark.get('verbal_element_text'))
            or _s(mark.get('description'))
            or _s(rec.get('mark_text')))


def from_temmy_item(rec: dict) -> MarkRecord | None:
    """Build a MarkRecord from one item of a Temmy search/detail response.

    Returns None when the record carries no readable mark text — scoring a
    blank mark yields a spurious status-only score, which would put dead
    records on a client's free report.
    """
    if not isinstance(rec, dict):
        return None
    mark_text = _temmy_mark_text(rec)
    if not mark_text:
        return None

    app_no = _first(rec, 'application_number', 'number')
    companies = _temmy_companies(rec)
    # Prefer the first CH-matched company for the shortlist row.
    primary = next((c for c in companies if c.matched), None) \
        or (companies[0] if companies else None)

    raw_status = _first(rec, 'status')
    return MarkRecord(
        mark_text=mark_text,
        mark_type=_temmy_mark_type(rec),
        status=canon_status(raw_status),      # canonical, for scoring
        status_display=raw_status,            # raw, for the client-facing row
        classes=_temmy_classes(rec),
        application_number=app_no,
        owner_name=_temmy_owner(rec),
        filing_date=_temmy_filing_date(rec),
        goods_services=_temmy_goods_services(rec),
        image_url=_s(rec.get('image_url')),
        source_url=UKIPO_CASE_URL.format(app_no) if app_no else '',
        source='temmy',
        temmy_id=rec.get('temmy_id'),
        company=primary,
        companies=companies,
    )
