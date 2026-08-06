"""Apollo enrichment — MANUAL ONLY, the staff-pressed button (Jonathan, 1 Aug 2026).

⚠️ NOT CALLED AUTOMATICALLY BY ANYTHING. Removed from the Free Search auto path
1 Aug 2026 — see FREESEARCH_ENRICHMENT_BRIEF.md and ENRICHMENT_SPEC.md in
temmy-lead-engine/. The automatic "no contact info" branch now calls
temmy-lead-engine/contact_resolver.py (Serper + Companies House) instead —
see freesearch/controller.py::handle_enrich. This file is kept, not deleted,
for the future manual Apollo button in Zoho/Cerebrum ("Apollo we will park
inside Zoho and can be a staff choice to activate" — Jonathan, 1 Aug).

WHY IT WAS PULLED FROM AUTOMATION: Apollo is an identity *resolver* — it needs
a domain or a LinkedIn URL to work well. Given a bare trademark/brand string
it guesses, and the `difflib` similarity floor this file used to gate on
fails in both directions ("Apex" vs "Apexa" scores ~0.89 — false positive;
"SCITYGATE" vs "Scitygate Telecom UK Ltd" scores low — false negative). A
staff member choosing to press a button on a record they've already judged
worth the spend sidesteps that problem entirely; an automated org-search on a
mark name does not.

Called ONLY by whatever eventually wires up the manual button — never by the
public wizard, same boundary as company.py's Companies House lookup. Key in
`APOLLO_API_KEY`.

CHANGES MADE 1 AUG PER ENRICHMENT_SPEC.md ("Apollo call discipline") — the
cost logic below was already sound and is unchanged; these four are not:
  1. `email_status` is now checked. Apollo returns `verified` / `guessed` /
     `unavailable` on a revealed email. A `guessed` (pattern-generated, e.g.
     first.last@domain guessed from a naming convention) address is treated
     as a NAME-ONLY result, not a usable channel — those are the ones that
     bounce. `email_status` is returned alongside `email` so a caller can see
     which they got; do not present a `guessed` address as a confirmed one.
  2. `reveal_personal_emails` is now False, not True. For cold B2B outreach
     to someone who has not given their details, a work email is defensible;
     a personal one is materially harder to justify — same legitimate-
     interest reasoning already applied to the LinkedIn rep gate elsewhere in
     this codebase.
  3. The `difflib` similarity floor is replaced with
     `search_guard._tokens()`/`_norm()` — the same token-overlap discipline
     RULE 2 already uses for LinkedIn batch validation, which does not have
     the "Apex" vs "Apexa" failure mode a raw character-similarity ratio has.
  4. Exceptions are no longer swallowed silently. `except Exception: return
     None` made a bad API key, a rate limit, and a genuine no-match
     indistinguishable — enrichment could be dead for weeks and just look
     like a low hit rate. `_post()` now returns a dict tagged with `_error`
     on failure instead of None, and callers can tell "Apollo said no" apart
     from "the call never really happened."

⚠️ Apollo marks catch-all domains `verified` even when no mailbox exists
behind the specific address. Small UK businesses filing trademarks are
disproportionately on catch-all domains — expect the real bounce rate to
exceed what `email_status` alone would suggest. Track status vs. actual
bounce once the button is live so the gap is measured, not assumed.

CREDIT DISCIPLINE — read before changing thresholds
  Apollo bills per call, not per successful match: an org-search page costs a
  credit whether or not anything useful comes back, a people-search page the
  same, and *revealing* a found person's email/phone is the expensive part
  (a phone reveal runs several times the cost of an email reveal).
    1. org search, one page, up to 5 results
    2. only proceed past org search if the best result's token-overlap
       against the searched name clears MIN_ORG_CONFIDENCE — see fix #3
       above; a loose text match on a trademark name is not a company match,
       and revealing a wrong person's contact details is worse than finding
       nothing
    3. people search is NOT auto-revealed with phone numbers by default —
       `reveal_phone=False` unless the caller asks for it, since phone reveal
       is the single costliest call in the whole path (unchanged, already
       correct per the spec review)
  If bounce/false-positive rates need tightening later, raise
  MIN_ORG_CONFIDENCE rather than relaxing it.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_ROOT = os.path.dirname(HERE)
_LEAD_ENGINE = os.path.join(_ENGINE_ROOT, 'temmy-lead-engine')
if os.path.isdir(_LEAD_ENGINE) and _LEAD_ENGINE not in sys.path:
    sys.path.insert(0, _LEAD_ENGINE)
try:
    import search_guard as _sg   # _tokens()/_norm() — see fix #3 above
except ImportError:
    _sg = None   # degrades to a simpler substring check; see _similarity()

APOLLO_BASE = 'https://api.apollo.io/api/v1'

MIN_ORG_CONFIDENCE = 1   # minimum shared meaningful tokens (see fix #3 above)
CANDIDATE_TITLES = [
    'owner', 'founder', 'co-founder', 'director', 'managing director',
    'ceo', 'marketing manager', 'brand manager', 'head of marketing',
]


def _api_key(key: str | None = None) -> str:
    return (key or os.environ.get('APOLLO_API_KEY', '')).strip()


def _post(path: str, body: dict, *, key: str, timeout: int = 20) -> dict:
    """Never returns None on failure — fix #4: a caller must be able to tell
    'Apollo said no' apart from 'the call itself failed'. Empty dict `{}` is
    reserved for "call succeeded, nothing came back"; a failure always carries
    `_error` naming the failure class."""
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        APOLLO_BASE + path, data=data, method='POST',
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'x-api-key': key,
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_txt = ''
        try:
            body_txt = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        return {'_error': f'HTTP {e.code}', '_detail': body_txt}
    except urllib.error.URLError as e:
        return {'_error': f'network: {e.reason}'}
    except TimeoutError:
        return {'_error': 'timeout'}
    except Exception as e:
        return {'_error': f'{type(e).__name__}: {e}'}


def _similarity(a: str, b: str) -> int:
    """Shared meaningful-token count between two names — replaces the old
    difflib character-similarity ratio (fix #3). Falls back to a plain
    substring check if search_guard isn't importable (shouldn't happen once
    this file lives alongside temmy-lead-engine, but this module must not
    hard-crash on import if that folder ever moves)."""
    if _sg is not None:
        ta, tb = set(_sg._tokens(a)), set(_sg._tokens(b))
        return len(ta & tb)
    return 1 if (a or '').strip().lower() in (b or '').strip().lower() else 0


def _search_organization(search_term: str, *, key: str) -> dict:
    """Best-guess company match for a searched mark name. One page, cheap.

    Returns {} for a clean no-match, {'_error': ...} for a call failure, or
    the matched org dict — never conflates the three (fix #4).
    """
    body = _post('/mixed_companies/search', {
        'q_organization_name': search_term,
        'page': 1, 'per_page': 5,
    }, key=key)
    if body.get('_error'):
        return body
    candidates = (body.get('organizations') or []) + (body.get('accounts') or [])
    if not candidates:
        return {}
    best = max(candidates, key=lambda c: _similarity(search_term, c.get('name') or ''))
    score = _similarity(search_term, best.get('name') or '')
    if score < MIN_ORG_CONFIDENCE:
        return {}
    return {
        'id': best.get('id'),
        'name': best.get('name'),
        'domain': best.get('primary_domain') or best.get('website_url'),
        'confidence': score,
    }


def _search_people(org_id: str, *, key: str) -> dict:
    """First plausible decision-maker at the matched org. Titles ordered by
    CANDIDATE_TITLES so an owner/founder outranks a generic marketing hire.
    Returns {} for no match, {'_error': ...} for a call failure."""
    body = _post('/mixed_people/search', {
        'organization_ids': [org_id],
        'person_titles': CANDIDATE_TITLES,
        'page': 1, 'per_page': 5,
    }, key=key)
    if body.get('_error'):
        return body
    people = body.get('people') or []
    if not people:
        return {}

    def rank(p: dict) -> int:
        title = (p.get('title') or '').lower()
        for i, t in enumerate(CANDIDATE_TITLES):
            if t in title:
                return i
        return len(CANDIDATE_TITLES)

    people.sort(key=rank)
    return people[0]


def _reveal(person: dict, *, key: str, reveal_phone: bool) -> dict:
    """The costly call — only reached once org+person both passed the earlier
    cheap filters. reveal_phone defaults False (see module docstring).

    Fix #2: reveal_personal_emails is False — work email only. Fix #1:
    email_status is returned alongside email so callers can see verified vs.
    guessed rather than treating every returned address as confirmed.
    """
    body = _post('/people/match', {
        'id': person.get('id'),
        'first_name': person.get('first_name'),
        'last_name': person.get('last_name'),
        'organization_name': (person.get('organization') or {}).get('name'),
        'reveal_personal_emails': False,
        'reveal_phone_number': bool(reveal_phone),
    }, key=key)
    if body.get('_error'):
        return body
    p = body.get('person') or {}
    return {
        'email': p.get('email') or person.get('email'),
        'email_status': p.get('email_status'),   # 'verified' | 'guessed' | 'unavailable' | None
        'phone': (p.get('phone_numbers') or [{}])[0].get('raw_number')
                 if p.get('phone_numbers') else None,
    }


def enrich_contact(search_term: str, classes: list[int] | None = None, *,
                    reveal_phone: bool = False, key: str | None = None) -> dict:
    """search_term (the mark/brand name) -> best-effort company + contact.

    Returns one of:
      {'ok': True, 'found': False,
       'reason': 'not_configured'|'no_search_term'|'no_organization_match'|
                 'no_contact_match'|'reveal_empty'|'guessed_email_only'|'api_error', ...}
      {'ok': True, 'found': True, 'company_name', 'domain', 'contact_name',
       'title', 'email', 'email_status', 'phone', 'source': 'apollo', 'confidence'}

    A `guessed` email (fix #1) is deliberately NOT treated as `found: True` —
    a pattern-generated address is a name-only result, not a usable channel;
    it comes back as `guessed_email_only` with the guessed address visible
    under `guessed_email` so a human can decide whether to use it anyway.

    `classes` isn't sent to Apollo (it has no Nice-class concept) — kept in
    the signature so callers don't have to special-case dropping it, and in
    case a future refinement narrows candidates by classes-implied industry.
    """
    k = _api_key(key)
    if not k:
        return {'ok': True, 'found': False, 'reason': 'not_configured'}

    term = (search_term or '').strip()
    if not term:
        return {'ok': True, 'found': False, 'reason': 'no_search_term'}

    org = _search_organization(term, key=k)
    if org.get('_error'):
        return {'ok': False, 'found': False, 'reason': 'api_error', 'detail': org}
    if not org:
        return {'ok': True, 'found': False, 'reason': 'no_organization_match'}

    person = _search_people(org['id'], key=k)
    if person.get('_error'):
        return {'ok': False, 'found': False, 'reason': 'api_error', 'detail': person,
                'company_name': org['name'], 'domain': org.get('domain')}
    if not person:
        return {'ok': True, 'found': False, 'reason': 'no_contact_match',
                'company_name': org['name'], 'domain': org.get('domain'),
                'confidence': org['confidence']}

    revealed = _reveal(person, key=k, reveal_phone=reveal_phone)
    if revealed.get('_error'):
        return {'ok': False, 'found': False, 'reason': 'api_error', 'detail': revealed,
                'company_name': org['name'], 'domain': org.get('domain')}

    contact_name = ' '.join(filter(None, [person.get('first_name'), person.get('last_name')]))
    email = revealed.get('email')
    email_status = revealed.get('email_status')
    phone = revealed.get('phone')

    if email and email_status == 'guessed' and not phone:
        return {'ok': True, 'found': False, 'reason': 'guessed_email_only',
                'company_name': org['name'], 'domain': org.get('domain'),
                'contact_name': contact_name, 'title': person.get('title'),
                'guessed_email': email, 'confidence': org['confidence']}

    if not email and not phone:
        return {'ok': True, 'found': False, 'reason': 'reveal_empty',
                'company_name': org['name'], 'domain': org.get('domain'),
                'contact_name': contact_name, 'confidence': org['confidence']}

    return {
        'ok': True, 'found': True,
        'company_name': org['name'], 'domain': org.get('domain'),
        'contact_name': contact_name,
        'title': person.get('title'),
        'email': email if email_status != 'guessed' else None,
        'guessed_email': email if email_status == 'guessed' else None,
        'email_status': email_status,
        'phone': phone,
        'source': 'apollo', 'confidence': org['confidence'],
    }
