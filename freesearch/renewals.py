"""Renewal search — "Not sure whether you need to renew? Check your trademarks".

Jonathan, 7 Oct 2026: the website renewal entry point, built like the Free
Search box. The visitor types a trademark name, trademark number, applicant
name or applicant number (or ticks the search types they want), we search
TemmyDB/Heart and show a list of marks to select from, each with its renewal
band from the process map (_Hub/handoffs/RENEWAL_PROCESS_MAP_2026-10-03.md §3).

Read-only: nothing is written anywhere, no session, no Deal. The map is clear
that a Deal only exists once the client has chosen an option (§8, at Quoted).

GET /renewal-search?q=<text>&types=mark_name,mark_number,applicant_name,applicant_number
    types empty  -> automatic: a number tries trademark + applicant number,
                    text tries trademark name + applicant name.
    returns {ok, query, types, marks:[...], owners:[...]}
    an applicant NAME search returns owners (pick one, then
    types=applicant_number&q=<ipo_identifier> lists their marks).

The representative is never returned (§4.1: show everything except the rep).
"""
from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

TYPES = ('mark_name', 'mark_number', 'applicant_name', 'applicant_number')

# --------------------------------------------------------------------------
# Renewal bands (§3). E = expiry date.
#   more than 12 months before E      -> not_yet_due   (set auto-renew)
#   12 to 6.5 months before E         -> approaching   (set auto-renew)
#   under 6.5 months before E         -> renew_now     (collect now, file when
#                                                       the window opens, E-6m)
#   up to 6 months after E            -> late          (late fee added)
#   more than 6 months after E        -> removed       (re-apply; call us)
# Applications not yet registered and marks refused/withdrawn/surrendered are
# not renewable at all, whatever the date says.
# --------------------------------------------------------------------------

BANDS = {
    'renew_now':   {'label': 'Renew now', 'tone': 'urgent', 'order': 0,
                    'say': 'Your renewal is due. Renew now and we file it as soon as the UKIPO window opens.'},
    'late':        {'label': 'Expired: late renewal', 'tone': 'urgent', 'order': 1,
                    'say': 'This mark has passed its renewal date but can still be renewed, with the UKIPO late fee added.'},
    'approaching': {'label': 'Renewal approaching', 'tone': 'soon', 'order': 2,
                    'say': 'Renewal is coming up within the year. Set it to renew automatically so it is never missed.'},
    'not_yet_due': {'label': 'Not yet due', 'tone': 'ok', 'order': 3,
                    'say': 'Nothing to do yet. You can set it to renew automatically now: the earlier the better.'},
    'removed':     {'label': 'Removed from the register', 'tone': 'ended', 'order': 4,
                    'say': 'More than six months past its renewal date, so it can no longer be renewed. Talk to us about re-applying.'},
    'pending':     {'label': 'Application, not yet registered', 'tone': 'info', 'order': 5,
                    'say': 'This is still an application, so there is nothing to renew yet.'},
    'not_live':    {'label': 'Not a live registration', 'tone': 'ended', 'order': 6,
                    'say': 'This mark is not live on the register, so it cannot be renewed. Talk to us if you think that is wrong.'},
    'unknown':     {'label': 'Check with us', 'tone': 'info', 'order': 7,
                    'say': 'We could not read a renewal date for this mark. Talk to us and we will check it for you.'},
}

_PENDING = {'application published', 'published', 'opposed', 'filed', 'pending', 'examination'}
_DATE_ENDED = {'dead', 'expired', 'removed'}          # ended BY the date passing
# refused / withdrawn / surrendered / cancelled: ended for another reason


def _add_months(d: dt.date, months: int) -> dt.date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def _date(v) -> dt.date | None:
    s = str(v or '')[:10]
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def band(expiry, status, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    st = str(status or '').strip().lower()
    e = _date(expiry)
    if st in _PENDING:
        key = 'pending'
    elif st and st != 'registered' and st not in _DATE_ENDED:
        key = 'not_live'
    elif not e:
        key = 'unknown'
    elif today > _add_months(e, 6):
        key = 'removed'
    elif today > e:
        key = 'late'
    elif today >= _add_months(e, -6) - dt.timedelta(days=15):     # 6.5 months
        key = 'renew_now'
    elif today >= _add_months(e, -12):
        key = 'approaching'
    else:
        key = 'not_yet_due'
    out = {'key': key, **BANDS[key]}
    if e:
        out['expiry'] = e.isoformat()
        out['window_opens'] = _add_months(e, -6).isoformat()
        out['late_until'] = _add_months(e, 6).isoformat()
        out['days_to_expiry'] = (e - today).days
    out['renewable_online'] = key in ('renew_now', 'late', 'approaching', 'not_yet_due')
    return out


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _s(v) -> str:
    return '' if v is None else str(v).strip()


def _num(q: str) -> str:
    """'uk00003456789', 'UK 3456789', '3456789' -> 'UK00003456789'."""
    digits = re.sub(r'\D', '', q or '')
    if not digits:
        return ''
    return 'UK' + digits.zfill(11)


def _looks_numeric(q: str) -> bool:
    return bool(re.fullmatch(r'\s*(uk)?[\s\d-]{4,}\s*', q or '', re.I))


def _row(r: dict, applicant: str = '') -> dict:
    """Normalise a lookup row and attach its band. Never carries the rep."""
    out = {
        'number': _s(r.get('number')),
        'name': _s(r.get('name')) or '(figurative mark)',
        'status': _s(r.get('status')),
        'applicant': _s(r.get('applicant')) or applicant,
        'classes': r.get('classes') or [],
        'mark_feature': _s(r.get('mark_feature')),
        'application_date': _s(r.get('application_date')),
        'expiry_date': _s(r.get('expiry_date'))[:10],
    }
    out['band'] = band(out['expiry_date'], out['status'])
    return out


def _marks_by_name(client, q: str) -> list[dict]:
    from . import lookup as lk
    res = lk.search_marks(client, q, limit=12)
    return [_row(r) for r in res.get('results', [])]


def _mark_by_number(client, q: str) -> list[dict]:
    from . import lookup as lk
    n = _num(q)
    if not n:
        return []
    try:
        d = client.get_trademark(n)
    except Exception:
        d = None
    if not isinstance(d, dict) or not d.get('application_number'):
        return []
    r = lk._mark_row(d)
    r['expiry_date'] = _s(d.get('expiry_date'))
    return [_row(r)]


def _owners_by_name(client, q: str) -> list[dict]:
    from . import lookup as lk
    res = lk.search_owners(client, q, limit=10)
    return [o for o in res.get('results', []) if o.get('trademark_count')]


def _marks_by_owner(client, ipo) -> tuple[dict | None, list[dict]]:
    from . import lookup as lk
    digits = re.sub(r'\D', '', str(ipo or ''))
    if not digits:
        return None, []
    o = lk.get_owner(client, int(digits))
    if not o:
        return None, []
    name = o['owner'].get('name', '')
    return o['owner'], [_row(t, applicant=name) for t in o.get('trademarks', [])]


def search(client, q: str, types: list[str] | None = None) -> dict:
    q = (q or '').strip()
    types = [t for t in (types or []) if t in TYPES]
    auto = not types
    if auto:
        # 'UK00…' can only be a trademark number; bare digits could be either,
        # so both run and each mark says which one it matched.
        if re.match(r'\s*uk', q, re.I) and _looks_numeric(q):
            types = ['mark_number']
        elif _looks_numeric(q):
            types = ['mark_number', 'applicant_number']
        else:
            types = ['mark_name', 'applicant_name']
    if len(q) < 2:
        return {'ok': False, 'status': 400, 'error': 'Type at least two characters.'}

    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        if 'mark_name' in types:
            jobs['mark_name'] = ex.submit(_marks_by_name, client, q)
        if 'mark_number' in types and re.search(r'\d', q):
            jobs['mark_number'] = ex.submit(_mark_by_number, client, q)
        if 'applicant_name' in types:
            jobs['applicant_name'] = ex.submit(_owners_by_name, client, q)
        if 'applicant_number' in types and re.search(r'\d', q):
            jobs['applicant_number'] = ex.submit(_marks_by_owner, client, q)

    marks, owners, owner, failed = [], [], None, []
    for k, f in jobs.items():
        try:
            res = f.result()
        except Exception:
            failed.append(k)
            continue
        if k == 'applicant_name':
            owners = res
        elif k == 'applicant_number':
            owner, ms = res
            for m in ms:
                m['matched_by'] = k
            marks += ms
        else:
            for m in res:
                m['matched_by'] = k
            marks += res

    seen, uniq = set(), []
    for m in marks:
        if m['number'] and m['number'] not in seen:
            seen.add(m['number'])
            uniq.append(m)
    uniq.sort(key=lambda m: (m['band']['order'], m['band'].get('days_to_expiry', 99999)))
    return {'ok': True, 'status': 200, 'query': q, 'types': types, 'auto': auto,
            'marks': uniq, 'owners': owners, 'owner': owner, 'failed': failed}


# --------------------------------------------------------------------------
# "View this trademark" and "View all trademarks" (Jonathan, 7 Oct)
# --------------------------------------------------------------------------

_STATE = {'registered': 'Registered', 'published': 'Published', 'filed': 'Filed',
          'examination': 'Examination', 'opposed': 'Opposed', 'withdrawn': 'Withdrawn',
          'refused': 'Refused', 'dead': 'Dead', 'expired': 'Expired', 'removed': 'Removed',
          'surrendered': 'Surrendered', 'cancelled': 'Cancelled', 'renewed': 'Renewed'}


def _addr(a: dict) -> str:
    a = a or {}
    parts = [a.get(k) for k in ('care_of', 'premises', 'line_1', 'line_2', 'line_3', 'line_4',
                                'locality', 'region', 'postcode')]
    out = ', '.join(_s(p) for p in parts if _s(p))
    c = _s(a.get('country'))
    return out + (', ' + c if c and c not in ('GB', 'UK') else '')


def mark_detail(client, number: str) -> dict | None:
    """Everything about one mark EXCEPT the representative (§4.1). Built field
    by field from an allow-list, so nothing new Temmy adds (contacts, cognism
    ids, representatives) can leak through by accident."""
    try:
        from . import class_headings as ch
    except ImportError:
        import class_headings as ch
    n = _num(number)
    if not n:
        return None
    try:
        d = client.get_trademark(n)
    except Exception:
        d = None
    if not isinstance(d, dict) or not d.get('application_number'):
        return None
    m = d.get('mark') or {}
    classes = []
    for c in d.get('nice_class_trademarks') or []:
        if not isinstance(c, dict) or c.get('removed_on') or c.get('active') is False:
            continue
        try:
            num = int(c.get('number'))
        except (TypeError, ValueError):
            continue
        classes.append({'n': num, 'heading': ch.heading(num),
                        'spec': _s(c.get('goods_services_description'))})
    if not classes:
        classes = [{'n': int(x), 'heading': ch.heading(int(x)), 'spec': ''}
                   for x in (d.get('classes') or []) if str(x).isdigit()]
    classes.sort(key=lambda c: c['n'])
    owners = []
    for a in d.get('applicants') or []:
        if not isinstance(a, dict):
            continue
        owners.append({'name': _s(a.get('name')), 'ipo_identifier': a.get('ipo_identifier'),
                       'kind': _s(a.get('kind')), 'company_number': _s(a.get('company_number')),
                       'country': _s(a.get('incorporation_country_code') or a.get('nationality_code')),
                       'address': _addr(a.get('address'))})
    history = []
    for t in d.get('trademark_transitions') or []:
        if not isinstance(t, dict) or t.get('removed_on'):
            continue
        st = _s(t.get('to_state')).lower()
        dte = _s((t.get('metadata') or {}).get('date') or t.get('added_on'))[:10]
        history.append({'state': _STATE.get(st, st.replace('_', ' ').title()), 'date': dte})
    history.sort(key=lambda h: h['date'], reverse=True)
    status = _s(d.get('status'))
    expiry = _s(d.get('expiry_date'))[:10]
    return {
        'number': _s(d.get('application_number')),
        'name': _s(m.get('verbal_element_text')) or '(figurative mark)',
        'feature': _s(m.get('feature')), 'kind': _s(m.get('kind_mark')),
        'description': _s(m.get('description')),
        'image_url': _s(d.get('image_url')),
        'status': status,
        'dates': {'filed': _s(d.get('application_date_time'))[:10],
                  'published': _s(d.get('publication_date'))[:10],
                  'registered': _s(d.get('registration_date'))[:10],
                  'expiry': expiry,
                  'last_updated': _s(d.get('last_updated_on'))[:10]},
        'band': band(expiry, status),
        'classes': classes, 'owners': owners, 'history': history,
        'ukipo_url': 'https://trademarks.ipo.gov.uk/ipo-tmcase/page/Results/1/' + _s(d.get('application_number')),
    }


def portfolio(client, ipo) -> dict | None:
    owner, marks = _marks_by_owner(client, ipo)
    if owner is None:
        return None
    marks.sort(key=lambda m: (m['band']['order'], m['band'].get('days_to_expiry', 99999)))
    counts = {}
    for m in marks:
        counts[m['band']['key']] = counts.get(m['band']['key'], 0) + 1
    return {'owner': owner, 'marks': marks, 'counts': counts}


def handle(params: dict, client) -> dict:
    raw = params.get('types') or params.get('type') or ''
    if isinstance(raw, list):
        raw = ','.join(raw)
    types = [t.strip() for t in str(raw).split(',') if t.strip()]
    try:
        action = str(params.get('action', '') or '')
        if action == 'mark':
            m = mark_detail(client, str(params.get('number', '')))
            return {'ok': True, 'status': 200, 'mark': m} if m else \
                {'ok': False, 'status': 404, 'error': 'We could not find that trademark.'}
        if action == 'portfolio':
            ipo, others = params.get('ipo'), []
            if not ipo and params.get('number'):
                # "View all trademarks" from a mark: the portfolio of its
                # (first) owner. Joint owners are offered as switches.
                m = mark_detail(client, str(params.get('number')))
                owners = [o for o in (m or {}).get('owners', []) if o.get('ipo_identifier')]
                if not owners:
                    return {'ok': False, 'status': 404, 'error': 'We could not find the owner of that trademark.'}
                ipo, others = owners[0]['ipo_identifier'], owners[1:]
            p = portfolio(client, ipo)
            if p:
                p['other_owners'] = others
            return {'ok': True, 'status': 200, **p} if p else \
                {'ok': False, 'status': 404, 'error': 'We could not find that applicant.'}
        return search(client, str(params.get('q', '')), types)
    except Exception as e:                       # degrade loudly, JSON-shaped
        return {'ok': False, 'status': 500, 'error': f'renewal search failed: {e}'}
