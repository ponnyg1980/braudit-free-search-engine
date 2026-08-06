#!/usr/bin/env python3
"""ONE RESOLVER — shared identifier + contact-channel resolution, three entry points.

Spec: ENRICHMENT_SPEC.md (Jonathan, 1 Aug). Build brief: FREESEARCH_ENRICHMENT_BRIEF.md.

REPLACES the automatic Apollo call that used to live in freesearch/enrichment.py's Free
Search path. Apollo is an identity *resolver* (needs a domain or LinkedIn URL), not a
search engine — feeding it a bare trademark/brand name made it guess, and its own
`difflib` similarity floor scored things like "Apex" vs "Apexa" as a 0.89 match. Per
Jonathan, 1 Aug: *"Apollo we will park inside Zoho and can be a staff choice to
activate."* Nothing in this module calls Apollo. `enrichment.py` is untouched by this
file and stays for that future manual button.

RESOLUTION ORDER — stop at first success (ENRICHMENT_SPEC.md, "Identifier acquisition")
  1. Domain of a supplied email (free) — strip free providers (gmail/outlook/yahoo/
     icloud/hotmail/live/aol); those identify nobody.
  2. Customer Website field, if supplied (free).
  3. Companies House by name (free) — also harvests SIC codes, officer names, and a
     registered-office TOWN. Done here, ahead of Serper, specifically because Serper's
     `/places` needs a location and Free Search collects no address field — the CH
     registered office is the only location hint most Free Search entries will ever
     have (§2, "🔑 /places needs a LOCATION").
  4. Serper `/places` — company/mark name + whatever location hint step 3 (or the
     caller) supplied. The money endpoint: verified 1 Aug to return a phone number AND
     a website in a single call for a real record (Science Made Simple Ltd).
  4b. Serper `/search` (organic + knowledgeGraph) — only if `/places` came back empty.
     Verified to be noise on its own (see ENRICHMENT_SPEC.md) — never call it first.

  A Companies-House-only result (no Serper hit) is still returned: a company number +
  SIC + named officer is real value even with no phone/website, and costs nothing.

NEVER RESOLVE FROM THE COMPETITOR FIELDS. The Free Search wizard's Competitor Website /
Competitor Trademark describe a DIFFERENT company, captured to classify goods and
services. Feeding either into identifier acquisition would return the competitor's own
decision-maker and we would contact a rival firm believing them to be the enquirer —
and it would look like a clean, confident result. `resolve()` below does not accept
competitor fields as acquisition inputs at all — only as `context_terms` for
corroboration (see next section) — and asserts this at the call site in
freesearch/controller.py. There is no parameter here a caller could accidentally wire
a competitor field into and have it used for the search query itself.

CORROBORATE BEFORE TRUSTING A RESULT. A wrong identifier is worse than none — it makes
everything downstream look confident. Every accepted CH/Places/Search hit is checked
against `context_terms` (goods & services description, classes/terms labels, SIC label,
company name, and yes, the competitor fields AS INDUSTRY CONTEXT ONLY) using the same
token-matching discipline as `search_guard.validate_results` (RULE 2: "when in doubt,
discard — there is no shortage of leads, there is a shortage of trust"). A candidate
that doesn't plausibly match is discarded and recorded `no_match`, never guessed at.

SUPPRESSION. Once Companies House returns a company_number, this module cross-checks it
against TemmyDB for a matching applicant and, if found, checks that applicant's aid
against suppression.json / suppression_applicants.json / suppression_pending.json —
the exact union `intake_v2.py` already uses. A suppressed match is discarded entirely
(`reason='suppressed'`), never partially used. Pre-resolution suppression (before any
company_number is known) genuinely cannot be checked — there is nothing to check yet —
and this module says so explicitly (`suppression_checked=False`) rather than silently
skipping the field.

RECORDING — deliberately NOT `search_guard.record()`. That function is keyed on `aid`
(a TemmyDB applicant id, short numeric strings like "365569") and drives RULE 1's
6-month re-search cooloff for the LinkedIn/Sales-Navigator routes. Free Search sessions
have no aid at the point of resolution — most searched brand names are not existing
TemmyDB applicants at all — so keying `search_guard`'s aid-indexed searched_log.json off
a session_id (or off nothing) would pollute a namespace that means something specific
elsewhere in this codebase and would silently defeat RULE 1 for the routes that
legitimately rely on it. Instead, `_record()` below appends to the SAME
`activity_log.jsonl` file `search_guard` writes to (so `daily_report.py` sees these
attempts for free, grouped under `route="resolver:<entry_point>"`) without touching
`searched_log.json` at all. If a Free Search resolution *does* turn out to match an
existing TemmyDB applicant (via the suppression cross-check above), that is recorded
too, but still through this module's own `_record()`, not `search_guard.record()`.

USAGE
    import contact_resolver as cr
    cfg = cr.load_cfg()   # env vars first, secrets.env fallback
    result = cr.resolve(
        "Science Made Simple", cfg=cfg,
        email=None, website=None, location_hint=None,
        context_terms=["educational science workshops", "class 41", "SIC 85590"],
        competitor_context=["Rival Science Ltd"],   # read-only, corroboration only
        entry_point="freesearch",
    )
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ch_enrich as che          # search_company / officers / profile / norm_name
import ghl_push as g             # load_env / find_env — the codebase's one env loader
import search_guard as sg        # _tokens / _norm — the codebase's one token-match logic

ACTIVITY_LOG = sg.ACTIVITY_LOG   # append to the SAME file search_guard writes to

FREE_EMAIL_PROVIDERS = {
    'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'hotmail.co.uk',
    'yahoo.com', 'yahoo.co.uk', 'icloud.com', 'live.com', 'live.co.uk', 'aol.com',
    'msn.com', 'me.com', 'mail.com', 'protonmail.com',
}

SERPER_BASE = 'https://google.serper.dev'
MIN_CORROBORATION_TOKENS = 1   # >=1 shared meaningful token required to accept a candidate


# --------------------------------------------------------------------------- config ---

def load_cfg(env_path: str | None = None) -> dict:
    """SerperClaudeAPI / COMPANIES_HOUSE_API_KEY / TEMMY_* — env vars first (how the
    deployed freesearch Cloud Run engine gets its secrets), secrets.env file second (how
    every temmy-lead-engine local script gets them). Works either way so this module is
    callable both from controller.py (live) and as a local batch script (§ Entry points).

    NOTE the exact key spelling: `SerperClaudeAPI` — every other key in secrets.env is
    UPPER_SNAKE; this one deliberately is not (ENRICHMENT_SPEC.md, 1 Aug). Both `load_env`
    (dict) and `os.environ` are case-sensitive, so getting the casing wrong here means the
    key silently never loads — do not "tidy" it without changing every reference.
    """
    keys = ('SerperClaudeAPI', 'COMPANIES_HOUSE_API_KEY',
            'TEMMY_API_BASE_URL', 'TEMMY_QUERY_RUNS_API_KEY')
    cfg = {k: os.environ[k] for k in keys if os.environ.get(k)}
    if 'SerperClaudeAPI' in cfg and 'COMPANIES_HOUSE_API_KEY' in cfg:
        return cfg
    try:
        file_cfg = g.load_env(g.find_env(env_path))
        for k in keys:
            cfg.setdefault(k, file_cfg.get(k, ''))
    except SystemExit:
        pass  # no secrets.env reachable — cfg stays whatever os.environ gave us
    return cfg


# ------------------------------------------------------------------- free identifiers ---

def _domain_from_email(email: str) -> str | None:
    m = re.search(r'@([a-z0-9.-]+\.[a-z]{2,})$', (email or '').strip().lower())
    if not m:
        return None
    domain = m.group(1)
    return None if domain in FREE_EMAIL_PROVIDERS else domain


def _domain_from_url(url: str) -> str | None:
    if not url:
        return None
    u = url.strip()
    if '://' not in u:
        u = 'https://' + u
    try:
        host = urllib.parse.urlparse(u).netloc.lower()
    except Exception:
        return None
    host = host[4:] if host.startswith('www.') else host
    return host or None


# ------------------------------------------------------------------------- corroborate ---

def _corroborate(candidate_text: str, context_terms: list[str] | None,
                  min_tokens: int = MIN_CORROBORATION_TOKENS) -> tuple[bool, str]:
    """True/reason — does `candidate_text` (a company/site name+snippet) plausibly match
    what the lead told us about their business? Same token-overlap discipline as
    `search_guard.validate_results` (RULE 2), applied to a single candidate rather than a
    batch. No context_terms at all means nothing to check against — corroboration is
    then vacuously skipped (not failed), same as `search_guard` treats an unvalidatable
    batch: the caller decides whether that's acceptable for this step.
    """
    wanted = set()
    for term in (context_terms or []):
        wanted.update(sg._tokens(term))
    if not wanted:
        return True, 'no context_terms supplied — corroboration skipped'
    blob = sg._norm(candidate_text or '')
    matched = [t for t in wanted if t in blob]
    if len(matched) >= min_tokens:
        return True, f'matched {matched[:5]}'
    return False, f'no overlap with {sorted(wanted)[:8]}'


# --------------------------------------------------------------------------- serper ---

def _serper(endpoint: str, query: str, cfg: dict, num: int = 5) -> dict:
    key = cfg.get('SerperClaudeAPI')
    if not key or not query.strip():
        return {}
    req = urllib.request.Request(
        f'{SERPER_BASE}/{endpoint}',
        data=json.dumps({'q': query, 'gl': 'gb', 'num': num}).encode(),
        headers={'X-API-KEY': key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {'_error': f'HTTP {e.code}'}
    except Exception as e:
        return {'_error': str(e)}


def _best_place(body: dict) -> dict | None:
    places = (body or {}).get('places') or []
    return places[0] if places else None


def _best_organic_website(body: dict) -> str | None:
    kg = (body or {}).get('knowledgeGraph') or {}
    if kg.get('website'):
        return kg['website']
    for r in (body or {}).get('organic') or []:
        if r.get('link'):
            return r['link']
    return None


# --------------------------------------------------------------------- suppression ---

def _jload(path: str, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _suppressed_aids() -> set[str]:
    """Exact union intake_v2.py uses (line ~218-222): suppression.json (list) +
    suppression_applicants.json (dict, aid-keyed) + suppression_pending.json (list of
    {aid: ...}, not yet fanned out by nightly maintenance)."""
    s = set(str(x) for x in _jload(os.path.join(HERE, 'suppression.json'), []))
    s |= set(str(k) for k in _jload(os.path.join(HERE, 'suppression_applicants.json'), {}).keys())
    s |= set(str(e.get('aid')) for e in _jload(os.path.join(HERE, 'suppression_pending.json'), [])
             if e.get('aid'))
    return s


def _aid_for_company_number(company_number: str, cfg: dict) -> str | None:
    """Best-effort: does this company_number match an existing TemmyDB applicant? Needs
    TEMMY_QUERY_RUNS_API_KEY — if that's not configured (e.g. a stripped-down Cloud Run
    deploy), this quietly returns None rather than raising, and the caller records
    `suppression_checked=False` rather than pretending a check happened."""
    if not company_number or not cfg.get('TEMMY_API_BASE_URL') or not cfg.get('TEMMY_QUERY_RUNS_API_KEY'):
        return None
    try:
        import daily_maintenance as dm
        num = company_number.replace("'", "").strip()[:20]
        rows = dm.temmy_runsql(
            cfg, f"SELECT ipo_identifier AS aid FROM applicants "
                 f"WHERE company_number = '{num}' LIMIT 1")
        return str(rows[0]['aid']) if rows else None
    except Exception:
        return None


def check_suppression(company_number: str | None, cfg: dict) -> dict:
    """{'checked': bool, 'suppressed': bool, 'aid': str|None}. See module docstring —
    suppression is only checkable once a company_number exists."""
    if not company_number:
        return {'checked': False, 'suppressed': False, 'aid': None}
    aid = _aid_for_company_number(company_number, cfg)
    if aid is None:
        return {'checked': False, 'suppressed': False, 'aid': None}
    return {'checked': True, 'suppressed': aid in _suppressed_aids(), 'aid': aid}


# ------------------------------------------------------------------------- recording ---

def _record(entry_point: str, search_term: str, outcome: str, *, step: str | None = None,
            credits_used: int = 0, channel: str | None = None, reason: str | None = None):
    """Append one line to activity_log.jsonl. See module docstring for why this is NOT
    search_guard.record() — deliberately does not touch searched_log.json's aid-keyed
    namespace. Best-effort: a logging failure must never break a resolution result."""
    try:
        now = datetime.datetime.now()
        line = {
            'ts': now.isoformat(timespec='seconds'), 'date': now.date().isoformat(),
            'route': f'resolver:{entry_point}', 'account': None,
            'outcome': outcome, 'tier': None, 'synced': False, 'email_found': False,
            'search_term': search_term, 'resolution_step': step,
            'credits_used': credits_used, 'channel_found': channel, 'reason': reason,
        }
        with open(ACTIVITY_LOG, 'a') as f:
            f.write(json.dumps(line) + '\n')
    except Exception as e:
        print(f'  ! activity_log append failed (non-fatal): {e}')


# --------------------------------------------------------------------------- resolve ---

def resolve(search_term: str, *, cfg: dict | None = None,
            email: str | None = None, website: str | None = None,
            location_hint: str | None = None,
            context_terms: list[str] | None = None,
            competitor_context: list[str] | None = None,
            entry_point: str = 'freesearch') -> dict:
    """The one resolver. `search_term` is the company/brand/mark name — the ONLY thing
    ever used to build a Serper/Companies-House query. `competitor_context` (Competitor
    Website / Competitor Trademark, read as plain strings) is accepted ONLY to fold into
    `context_terms` for corroboration — see module docstring's "NEVER RESOLVE FROM THE
    COMPETITOR FIELDS." It is never passed to `_serper()` or `che.search_company()`.

    Returns:
      {'ok': True, 'found': bool, 'step': <which step resolved it, or None>,
       'website': str|None, 'domain': str|None, 'phone': str|None, 'address': str|None,
       'company_number': str|None, 'sic_codes': list|None, 'officer_names': list|None,
       'corroboration': {'checked': bool, 'matched': bool, 'reason': str},
       'suppression': {'checked': bool, 'suppressed': bool, 'aid': str|None},
       'credits_used': int, 'reason': str|None}
    """
    cfg = cfg or load_cfg()
    term = (search_term or '').strip()
    all_context = list(context_terms or []) + list(competitor_context or [])
    credits_used = 0

    if not term:
        _record(entry_point, term, 'not_found', reason='no_search_term')
        return {'ok': True, 'found': False, 'step': None, 'reason': 'no_search_term',
                'credits_used': 0}

    # --- Step 1 (free): domain of a supplied email --------------------------------
    domain = _domain_from_email(email) if email else None
    if domain:
        _record(entry_point, term, 'found', step='email_domain', channel='domain')
        return {'ok': True, 'found': True, 'step': 'email_domain', 'domain': domain,
                'website': f'https://{domain}', 'phone': None, 'address': None,
                'company_number': None, 'sic_codes': None, 'officer_names': None,
                'corroboration': {'checked': False, 'matched': True, 'reason': 'given by lead'},
                'suppression': {'checked': False, 'suppressed': False, 'aid': None},
                'credits_used': 0, 'reason': None}

    # --- Step 2 (free): Customer Website field, if supplied ------------------------
    domain = _domain_from_url(website) if website else None
    if domain:
        _record(entry_point, term, 'found', step='customer_website', channel='domain')
        return {'ok': True, 'found': True, 'step': 'customer_website', 'domain': domain,
                'website': website, 'phone': None, 'address': None,
                'company_number': None, 'sic_codes': None, 'officer_names': None,
                'corroboration': {'checked': False, 'matched': True, 'reason': 'given by lead'},
                'suppression': {'checked': False, 'suppressed': False, 'aid': None},
                'credits_used': 0, 'reason': None}

    # --- Step 3 (free): Companies House by name — also a location hint for Places ---
    ch_hit, ch_conf, ch_officers = None, 'none', []
    ch_key = cfg.get('COMPANIES_HOUSE_API_KEY')
    if ch_key:
        hit, conf = che.search_company(term, ch_key)
        if hit:
            ok, why = _corroborate(hit.get('company_name') or hit.get('matched_name') or '',
                                   all_context)
            if ok:
                ch_hit, ch_conf = hit, conf
                if ch_hit.get('company_number'):
                    ch_officers = che.officers(ch_hit['company_number'], ch_key)
                loc = ch_hit.get('address') or ''
                # crude "town": last comma-separated segment before the postcode, if any
                parts = [p.strip() for p in loc.split(',') if p.strip()]
                if parts and not location_hint:
                    location_hint = parts[-2] if len(parts) > 1 else parts[-1]
            else:
                _record(entry_point, term, 'no_match', step='companies_house', reason=why)

    if ch_hit and ch_hit.get('company_number'):
        supp = check_suppression(ch_hit['company_number'], cfg)
        if supp['suppressed']:
            _record(entry_point, term, 'suppressed', step='companies_house',
                    reason=f"aid {supp['aid']} suppressed")
            return {'ok': True, 'found': False, 'step': None, 'reason': 'suppressed',
                    'suppression': supp, 'credits_used': 0}
    else:
        supp = {'checked': False, 'suppressed': False, 'aid': None}

    # --- Step 4 (~£0.001): Serper /places — name + location hint --------------------
    if cfg.get('SerperClaudeAPI'):
        query = f'{term} {location_hint}'.strip() if location_hint else term
        places_body = _serper('places', query, cfg)
        credits_used += int(places_body.get('credits') or 0)
        best = _best_place(places_body)
        if best:
            ok, why = _corroborate(best.get('title') or '', all_context)
            if ok:
                _record(entry_point, term, 'found', step='serper_places',
                        credits_used=credits_used, channel='phone' if best.get('phoneNumber') else 'website')
                return {
                    'ok': True, 'found': True, 'step': 'serper_places',
                    'website': best.get('website'), 'domain': _domain_from_url(best.get('website') or ''),
                    'phone': best.get('phoneNumber'), 'address': best.get('address'),
                    'company_number': ch_hit.get('company_number') if ch_hit else None,
                    'sic_codes': ch_hit.get('sic') if ch_hit else None,
                    'officer_names': [o['name'] for o in ch_officers] or None,
                    'corroboration': {'checked': True, 'matched': True, 'reason': why},
                    'suppression': supp, 'credits_used': credits_used, 'reason': None,
                }
            _record(entry_point, term, 'no_match', step='serper_places',
                    credits_used=credits_used, reason=why)

        # --- Step 4b: Serper /search fallback — only if /places was empty -----------
        search_body = _serper('search', f'{term} company', cfg)
        credits_used += int(search_body.get('credits') or 0)
        cand_site = _best_organic_website(search_body)
        if cand_site:
            cand_text = ((search_body.get('knowledgeGraph') or {}).get('title') or
                        (search_body.get('organic') or [{}])[0].get('title') or cand_site)
            ok, why = _corroborate(cand_text, all_context)
            if ok:
                _record(entry_point, term, 'found', step='serper_search',
                        credits_used=credits_used, channel='website')
                return {
                    'ok': True, 'found': True, 'step': 'serper_search',
                    'website': cand_site, 'domain': _domain_from_url(cand_site),
                    'phone': None, 'address': None,
                    'company_number': ch_hit.get('company_number') if ch_hit else None,
                    'sic_codes': ch_hit.get('sic') if ch_hit else None,
                    'officer_names': [o['name'] for o in ch_officers] or None,
                    'corroboration': {'checked': True, 'matched': True, 'reason': why},
                    'suppression': supp, 'credits_used': credits_used, 'reason': None,
                }
            _record(entry_point, term, 'no_match', step='serper_search',
                    credits_used=credits_used, reason=why)

    # --- Companies-House-only result: still real value, still free ------------------
    if ch_hit:
        _record(entry_point, term, 'found', step='companies_house', channel='company_number')
        return {
            'ok': True, 'found': True, 'step': 'companies_house',
            'website': None, 'domain': None, 'phone': None,
            'address': ch_hit.get('address'), 'company_number': ch_hit.get('company_number'),
            'sic_codes': ch_hit.get('sic'), 'officer_names': [o['name'] for o in ch_officers] or None,
            'corroboration': {'checked': True, 'matched': True, 'reason': f'CH confidence {ch_conf}'},
            'suppression': supp, 'credits_used': credits_used, 'reason': None,
        }

    _record(entry_point, term, 'not_found', reason='no_identifier_resolved',
            credits_used=credits_used)
    return {'ok': True, 'found': False, 'step': None, 'reason': 'no_identifier_resolved',
            'suppression': supp, 'credits_used': credits_used}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('term')
    ap.add_argument('--env', default=None)
    ap.add_argument('--location', default=None)
    ap.add_argument('--entry-point', default='cli')
    a = ap.parse_args()
    out = resolve(a.term, cfg=load_cfg(a.env), location_hint=a.location, entry_point=a.entry_point)
    print(json.dumps(out, indent=1))
