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
# Display helpers — added 10 Aug 2026 (Jonathan): result rows should show the
# application date, status, mark feature, class numbers AND a plain-English
# class description, not just numbers.
#
# These are the SHORT labels the wizard already used client-side, lifted here
# so every widget (free search, search bar, class assistant) renders classes
# identically from one source instead of each keeping its own copy that can
# drift. The official NICE headings in deploy-v2-hotfix/nice_classes.py are
# the full legal text and far too long for a list row — that is the "not the
# full terms, just the overview" distinction.
# ---------------------------------------------------------------------------

def class_label(n) -> str:
    """Short, friendly class name — e.g. 30 -> 'Coffee, bakery & staple foods'.

    Delegates to freesearch/nice_labels.py, which already owned this and is
    what term_basket uses for `class_label`. Deliberately NOT a second copy
    of the 45 names here: two lists would drift and the UI would show one
    wording in a result row and another in the basket for the same class.

    The official NICE heading (nice_classes.NICE_HEADINGS) is the full legal
    text — far too long for a list row. This is the "overview description".
    """
    try:
        from .nice_labels import short
    except ImportError:
        from nice_labels import short           # module run flat, e.g. tests
    try:
        return short(int(n)) or ''
    except (TypeError, ValueError):
        return ''


def classes_detail(classes) -> list[dict]:
    """[{'n': 30, 'label': 'Coffee, Bakery & Staple Foods'}, ...]

    Structured rather than a pre-joined string so the client can render each
    class as its own tickable row (the confirm-before-adding picker) without
    having to parse text back apart.
    """
    out = []
    for n in (classes or []):
        try:
            i = int(n)
        except (TypeError, ValueError):
            continue
        out.append({'n': i, 'label': class_label(i)})
    return out


def _app_date(rec: dict) -> str:
    """'2003-01-17T00:00:00' -> '17 Jan 2003'. Empty string if absent."""
    raw = _s(rec.get('application_date_time')) or _s(rec.get('application_date'))
    if not raw:
        return ''
    import datetime
    try:
        return datetime.datetime.fromisoformat(
            raw.replace('Z', '')).strftime('%d %b %Y')
    except ValueError:
        return raw[:10]


def _mark_feature(rec: dict) -> str:
    """Word / Figurative / Combined etc.

    Present as `mark_type` on a trademark-search row and as `mark.feature` on
    an applicant-response row — same concept, two shapes, so check both.
    """
    mark = rec.get('mark') or {}
    return _s(rec.get('mark_type')) or _s(mark.get('feature'))


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
        rows = _search_by_text(client, query, limit)
        suggested = False
        matched_on = ''
        if not rows:
            # NOTHING MATCHED — try again, loosely.
            #
            # The register search is a PREFIX match, verified against the live
            # API: "Guinnes" finds GUINNESS (it is a prefix), "Guiness" finds
            # nothing (it diverges at the fifth letter). So the single most
            # common miss — a dropped or doubled letter in the middle of a
            # familiar name — returns a flat "no trademarks", which reads as
            # "this name is free" when it is anything but.
            #
            # The retry costs nothing on the happy path: it only runs when the
            # first search already came back empty, so a search that works is
            # exactly as fast as it was before.
            for variant in _relaxations(query):
                rows = _search_by_text(client, variant, limit, fetch=40)
                if rows:
                    suggested, matched_on = True, variant
                    break
            if rows:
                # Rank against what they actually TYPED, not the relaxed
                # variant, and keep only close matches. Shortening "Guiness"
                # to "Guin" also returns GUINEA and Guinep; scored against the
                # original those sit near 0.77 while GUINNESS scores 0.93, so
                # the threshold keeps the answer and drops the noise. Without
                # it this would trade one bad outcome for another — a wall of
                # near-random names.
                # Re-rank against the ORIGINAL spelling. _search_by_text
                # sorted on the relaxed variant, which puts GUINEA above
                # GUINNESS for a "Guin" retry — right answer to the wrong
                # question. What matters is closeness to what was typed.
                rows.sort(key=lambda r: _sim(query, r['name']), reverse=True)
                rows = [r for r in rows if _sim(query, r['name']) >= SUGGEST_MIN]
                rows = rows[:SUGGEST_MAX]
        results = rows[:SUGGEST_MAX if suggested else limit]
        _fill_details(client, results)
        if suggested:
            return {'mode': 'trademark', 'query': query, 'results': results,
                    'suggested': True, 'matched_on': matched_on}

    return {'mode': 'trademark', 'query': query, 'results': results}


# How close a relaxed hit has to be to what was typed, and how many we show.
#
# 0.80 is measured, not guessed. Across a set of real misspellings —
# Guiness/GUINNESS .93, Cadburys/CADBURY .93, Volkswagon/VOLKSWAGEN .90,
# Heiniken/HEINEKEN .88, Nestle/NESTLE .83 — the weakest genuine correction
# scores .83. The strongest piece of collateral the same relaxed queries drag
# in — GUINEA and Guinep against "Guiness" — scores .77. So .80 sits in a
# real gap: every intended correction survives and every unintended one goes.
# Move it down and GUINEA comes back; move it up past .83 and accented or
# possessive forms start disappearing.
SUGGEST_MIN = 0.80
SUGGEST_MAX = 6


def _search_by_text(client, text: str, limit: int, *, fetch: int = 25) -> list:
    """One text search, ranked by closeness. Returns [] on any failure."""
    try:
        body = client.search_trademarks(text=text, limit=max(limit, fetch))
    except Exception:
        body = {}
    rows = [_mark_row(it) for it in (body or {}).get('items', [])]
    rows.sort(key=lambda r: _sim(text, r['name']), reverse=True)
    return rows


def _relaxations(query: str) -> list[str]:
    """Cheaper spellings to retry, best first. At most two extra calls.

    1. Without spaces — the search is prefix-based, so "Guin ness" and
       "cor search" match nothing even though the mark is one word.
    2. A shortened prefix — this is what catches the dropped/doubled letter,
       because the start of a name is nearly always typed correctly and the
       mistake is further in. Trimming three characters off "Guiness" gives
       "Guin", which reaches GUINNESS.

    Deliberately NOT a list of generated misspellings: that would be a dozen
    round trips to guess at something the prefix already solves in one.
    """
    out = []
    squashed = ''.join(query.split())
    if squashed and squashed != query:
        out.append(squashed)
    stem = squashed or query
    cut = max(4, len(stem) - 3)
    if cut < len(stem):
        out.append(stem[:cut])
    return out


def _fill_details(client, rows: list[dict]) -> None:
    """Add application_date + classes to search rows, in place.

    The search endpoint doesn't return either (mark_type IS there, so the
    feature is free). Only a per-trademark detail fetch has them, so this
    runs them concurrently: measured on the live pooled client, 6 rows take
    ~1.0s in parallel against ~1.8s sequentially, on top of a ~3s search.
    That is a real cost, accepted because the list is far more useful with a
    date and classes on it, and the hexagon loader now covers the wait.

    Best-effort throughout: any row whose detail fetch fails simply keeps its
    empty date/classes rather than failing the whole search. A slower, richer
    list is a fair trade; a list that 500s because one mark is odd is not.
    """
    todo = [r for r in rows if r.get('number') and not r.get('classes')]
    if not todo:
        return

    def one(row):
        try:
            d = client.get_trademark(row['number'])
        except Exception:
            return
        if not isinstance(d, dict):
            return
        row['application_date'] = row['application_date'] or _app_date(d)
        row['mark_feature'] = row['mark_feature'] or _mark_feature(d)
        cls = [int(n) for n in (d.get('classes') or []) if str(n).isdigit()]
        if cls:
            row['classes'] = cls
            row['classes_detail'] = classes_detail(cls)

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(todo))) as ex:
            list(ex.map(one, todo))
    except Exception:
        for r in todo:      # pool unavailable — still correct, just slower
            one(r)


def _mark_row(rec: dict) -> dict:
    """One row of a result list.

    application_date / classes are absent from a trademark-SEARCH response
    (it returns only applicants, application_number, expiry_date, id,
    last_updated_on, mark_type, status, verbal_element_text) but present on
    an applicant response and on a detail fetch. So they come back empty
    here and search_marks() fills them in — see the note there.
    """
    classes = [int(n) for n in (rec.get('classes') or []) if str(n).isdigit()]
    return {
        'number': _s(rec.get('application_number')),
        'name': _mark_name(rec),
        'status': _s(rec.get('status')),
        'applicant': _applicant_names(rec),
        'application_date': _app_date(rec),
        'mark_feature': _mark_feature(rec),
        'classes': classes,
        'classes_detail': classes_detail(classes),
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
        cls = [int(n) for n in (t.get('classes') or []) if str(n).isdigit()]
        tms.append({
            'number': _s(t.get('application_number')),
            'name': _mark_name(t),
            'status': _s(t.get('status')),
            'classes': cls,
            # Free here — unlike the trademark-search path, the applicant
            # response already carries application_date_time and mark.feature,
            # so an owner's list needs no extra fetches at all.
            'application_date': _app_date(t),
            'mark_feature': _mark_feature(t),
            'classes_detail': classes_detail(cls),
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
