"""Framework-agnostic HTTP controller.

`handle_free_search(payload, client)` takes the JSON the wizard posts and
returns the JSON the results page renders. No web framework in the signature,
on purpose: the same function is the body of a FastAPI route, a Flask view, a
Supabase edge function, or an AWS Lambda. That is what "run from any website"
requires — one controller, many thin adapters.

It must stay fast. No model load, no blocking work beyond the (free) TemmyDB
calls the service makes. Validation is defensive and total: a malformed
payload returns a 400-shaped dict, never a stack trace, because this endpoint
is public and embedded in third-party pages.
"""
from __future__ import annotations

import base64
import binascii

from .jurisdictions_data import VALID_CODES, expand_for_profiling, picker_payload
from .models import FreeSearchRequest, Jurisdictions
from .serialize import serialize_result
from .service import run_free_search

MAX_WORD_MARKS = 3          # word + (optional) second word; tagline separate


def _qr_retriever():
    """Build a Query Runs retriever from env, or None (falls back to REST).

    Kept here so the controller stays the single wiring point; the engine
    itself takes the retriever as an argument and has no env knowledge.
    """
    import os
    key = (os.environ.get('TEMMY_QUERY_RUNS_API_KEY') or '').strip()
    if not key:
        return None
    try:
        from .queryruns import QueryRunsRetriever
        return QueryRunsRetriever(
            key, base_url=(os.environ.get('TEMMY_API_BASE_URL') or '').strip() or None)
    except Exception:
        return None
MAX_MARK_LEN = 200
MAX_CLASSES = 45


class BadRequest(ValueError):
    """Raised for client-side payload errors -> HTTP 400."""


def _clean_codes(raw, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise BadRequest(f'{field} must be a list of country codes')
    out = []
    for c in raw:
        code = str(c or '').upper().strip()
        if not code:
            continue
        if code not in VALID_CODES:
            raise BadRequest(f'{field}: unknown code {code!r}')
        out.append(code)
    return tuple(out)


def _clean_classes(raw) -> tuple[int, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise BadRequest('classes must be a list of integers 1-45')
    out = []
    for n in raw:
        try:
            v = int(n)
        except (TypeError, ValueError):
            raise BadRequest(f'classes: {n!r} is not an integer')
        if not 1 <= v <= 45:
            raise BadRequest(f'classes: {v} out of range 1-45')
        out.append(v)
    if len(out) > MAX_CLASSES:
        raise BadRequest('too many classes')
    return tuple(sorted(set(out)))


def _clean_mark(v, field: str) -> str:
    s = str(v or '').strip()
    if len(s) > MAX_MARK_LEN:
        raise BadRequest(f'{field} too long')
    return s


def _decode_image(v) -> bytes | None:
    """Accept a base64 data URI or bare base64. We do NOT search it — we only
    record that a logo was supplied, per the free/paid boundary."""
    if not v:
        return None
    s = str(v)
    if ',' in s and s.strip().lower().startswith('data:'):
        s = s.split(',', 1)[1]
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest('logo is not valid base64')


def parse_request(payload: dict) -> FreeSearchRequest:
    """Map the wizard's posted JSON onto a FreeSearchRequest.

    Wizard -> request field mapping:
        Step 1/2 name        -> word_marks[0]
        Step 2 tagline       -> tagline
        Step 2 logo          -> image_bytes (captured, not searched)
        Step 3 classes       -> classes
        Step 4 trade now     -> jurisdictions.trading_now
        Step 5 plan to trade -> jurisdictions.planning_to_trade
    """
    if not isinstance(payload, dict):
        raise BadRequest('payload must be a JSON object')

    name = _clean_mark(payload.get('name') or payload.get('word_mark'), 'name')
    extra = payload.get('word_marks') or []
    if isinstance(extra, str):
        extra = [extra]
    word_marks = tuple(m for m in (name, *[_clean_mark(x, 'word_marks')
                                           for x in extra]) if m)[:MAX_WORD_MARKS]
    if not word_marks:
        raise BadRequest('a name (word mark) is required')

    tagline = _clean_mark(payload.get('tagline'), 'tagline') or None
    image = _decode_image(payload.get('logo') or payload.get('image_base64'))

    trading_now = _clean_codes(
        payload.get('trading_now') or payload.get('jurisdictions_now'),
        'trading_now')
    planning = _clean_codes(
        payload.get('planning_to_trade') or payload.get('jurisdictions_plan'),
        'planning_to_trade')

    return FreeSearchRequest(
        word_marks=word_marks,
        tagline=tagline,
        image_bytes=image,
        classes=_clean_classes(payload.get('classes')),
        # Store the office-level codes the client actually picked (clean for
        # the disclaimer). Group expansion (EU -> 27 members) happens later at
        # the Zoho push via expand_for_profiling, not here.
        jurisdictions=Jurisdictions(
            trading_now=trading_now,
            planning_to_trade=planning,
        ),
        tenant_id=str(payload.get('tenant_id') or 'tmh').strip()[:64],
    )


def handle_free_search(payload: dict, client, *, gated: bool = False) -> dict:
    """The engine entry point. Returns a JSON-serialisable dict.

    `gated` may be forced by the keyword arg OR by a trusted `_gated` flag in
    the payload. Only the private Supabase Edge Function calls this engine, and
    it sets `_gated` after deciding — from the caller's account session —
    whether the full report is unlocked. The engine never decides gating from
    an anonymous browser payload; it only obeys the trusted front door.

    Errors are returned as `{'ok': False, 'error': ...}` with a `status` hint
    rather than raised, so thin web adapters can map them to HTTP codes
    without try/except sprawl.
    """
    if isinstance(payload, dict) and payload.get('_gated'):
        gated = True

    try:
        request = parse_request(payload)
    except BadRequest as exc:
        return {'ok': False, 'status': 400, 'error': str(exc)}

    try:
        result = run_free_search(client, request, qr_retriever=_qr_retriever())
    except Exception:  # never leak internals to an embedded public page
        return {'ok': False, 'status': 502,
                'error': 'Search is temporarily unavailable. Please retry.'}

    return {'ok': True, 'status': 200,
            'result': serialize_result(result, gated=gated)}


def handle_jurisdictions() -> dict:
    """GET handler for the Step 4/5 picker data."""
    return {'ok': True, 'status': 200, 'picker': picker_payload()}


def _resolver():
    """Cross-folder import of temmy-lead-engine/contact_resolver.py — same
    sys.path pattern api.py's _make_client() uses for deploy-v2-hotfix."""
    import sys
    from pathlib import Path
    lead_engine = Path(__file__).resolve().parents[1] / 'temmy-lead-engine'
    if str(lead_engine) not in sys.path:
        sys.path.insert(0, str(lead_engine))
    import contact_resolver
    return contact_resolver


def handle_enrich(payload: dict) -> dict:
    """The 'no contact info' branch entry point (Jonathan, 01 Aug 2026).

    Called only by the private `journey` Edge Function's /enrich relay, which
    itself is only reachable from the Cerebrum workflow — never directly by
    the public wizard. Takes a Free Search that never gave contact info and
    tries to find a company + contact channel so Cerebrum can route it to a
    human for individually-judged outreach instead of dropping it.

    CHANGED 01 Aug 2026 (FREESEARCH_ENRICHMENT_BRIEF.md / ENRICHMENT_SPEC.md):
    this used to call Apollo's org search directly on the searched mark name —
    Apollo is an identity *resolver*, not a search engine, and guessing from a
    bare brand string misfired in both directions. Apollo is now a manual
    staff button elsewhere (freesearch/enrichment.py, untouched, unused here)
    — this automatic path calls temmy-lead-engine/contact_resolver.py
    (Serper + Companies House) instead.

    ⚠️ competitor_website / competitor_trademark, if present in the payload,
    are read ONLY into `competitor_context` below — corroboration material,
    per the spec's "NEVER RESOLVE FROM THE COMPETITOR FIELDS." Nothing in
    this function passes them to the resolver as `search_term` or `website`;
    contact_resolver.resolve() does not even have a parameter that would
    accept them for acquisition.
    """
    if not isinstance(payload, dict):
        return {'ok': False, 'status': 400, 'error': 'payload must be a JSON object'}
    try:
        search_term = _clean_mark(
            payload.get('search_term') or payload.get('name'), 'search_term')
    except BadRequest as exc:
        return {'ok': False, 'status': 400, 'error': str(exc)}
    if not search_term:
        return {'ok': False, 'status': 400, 'error': 'search_term is required'}

    # Legitimate identity/context inputs — may be used for acquisition.
    email = str(payload.get('email') or '').strip() or None
    website = str(payload.get('business_website') or payload.get('website') or '').strip() or None
    location_hint = str(payload.get('location_hint') or '').strip() or None

    context_terms = [t for t in (
        payload.get('business_name'), payload.get('trading_name'),
        payload.get('tagline'),
    ) if t]

    # Competitor fields — corroboration-only, see docstring. Deliberately
    # collected into a SEPARATE list from context_terms and passed to the
    # resolver's own separate `competitor_context` parameter, never merged
    # into anything that could reach a search query.
    competitor_context = [t for t in (
        payload.get('competitor_website'), payload.get('competitor_trademark'),
        payload.get('competitor_name'),
    ) if t]

    try:
        resolver = _resolver()
        result = resolver.resolve(
            search_term, cfg=resolver.load_cfg(),
            email=email, website=website, location_hint=location_hint,
            context_terms=context_terms, competitor_context=competitor_context,
            entry_point='freesearch',
        )
    except Exception as exc:  # never leak internals — this is called from a webhook relay
        return {'ok': False, 'status': 502,
                'error': 'Enrichment is temporarily unavailable. Please retry.',
                'detail': f'{type(exc).__name__}: {exc}'}

    result.setdefault('status', 200)
    return result


def handle_suggest_classes(payload: dict) -> dict:
    """Class & term suggestion — the shared entry point.

    Jonathan, 10 Aug: "I want this sonnet agent to be accessible in Brand
    Audit and also as a standalone class selection tool for my staff... all
    of these class selection tools we want to build in a way that is
    transferrable to other applications."

    So this is deliberately generic. It takes text and returns classes with
    verified terms; it knows nothing about Free Search, Brand Audit or who is
    calling. Every caller — the wizard, the audit form, the staff tool, and
    whatever comes next — uses this same shape.

    Request:
      { text, provides?: 'goods'|'services'|'both', context?: {...} }

    `context` is an optional dict of extra description the caller has (goods
    list, services list, tagline, anything). It is appended to the text, not
    interpreted, so a new caller can pass new fields without changing this.

    Never raises: the class picker must still work when the agent is down.
    """
    if not isinstance(payload, dict):
        return {'ok': False, 'status': 400, 'error': 'payload must be a JSON object'}

    text = str(payload.get('text') or '').strip()
    ctx = payload.get('context') or {}
    if isinstance(ctx, dict):
        extra = [f'{k.replace("_", " ")}: {v}' for k, v in ctx.items()
                 if v and isinstance(v, (str, int, float))]
        if extra:
            text = (text + '\n' + '\n'.join(extra)).strip()
    if len(text) < 10:
        return {'ok': False, 'status': 400, 'error': 'too_short',
                'message': 'Tell us a little more about the business.'}

    provides = payload.get('provides')
    if provides not in ('goods', 'services', 'both'):
        provides = None

    try:
        from . import class_agent
    except ImportError:
        import class_agent  # type: ignore

    try:
        out = class_agent.suggest(text, provides=provides)
    except Exception as exc:  # noqa: BLE001 - never fail the caller's UI
        return {'ok': False, 'status': 502, 'error': 'agent_unavailable',
                'message': 'Suggestions are unavailable — please pick classes manually.',
                'detail': f'{type(exc).__name__}: {exc}'}

    out.setdefault('status', 200 if out.get('ok') else 502)
    return out


def handle_read_website(payload: dict) -> dict:
    """Read a visitor's own website and answer the business questions from it.

    Jonathan, 10 Aug: "What the website URL should do, is try and answer the
    questions from describe your business."

    Returns the SAME answer shape the guided questions produce, so the client
    can drop it straight into that form for the visitor to check and correct.
    Deliberately does NOT classify — that is a second, explicit step once a
    human has confirmed what we read is right.

    Also deliberately honest about thin pages: a landing page or a
    JavaScript-rendered shell yields nothing useful, and saying so beats
    guessing from a domain name.
    """
    if not isinstance(payload, dict):
        return {'ok': False, 'status': 400, 'error': 'payload must be a JSON object'}
    url = str(payload.get('url') or '').strip()
    if not url:
        return {'ok': False, 'status': 400, 'error': 'url_required',
                'message': 'Please give a website address.'}

    try:
        from . import web_reader, class_agent
    except ImportError:
        import web_reader, class_agent  # type: ignore

    try:
        text, final_url = web_reader.fetch_text(url)
    except web_reader.FetchError as exc:
        return {'ok': False, 'status': 200, 'error': 'fetch_failed',
                'message': str(exc)}
    except Exception:  # noqa: BLE001
        return {'ok': False, 'status': 200, 'error': 'fetch_failed',
                'message': "We couldn't read that website."}

    if web_reader.looks_thin(text):
        return {'ok': False, 'status': 200, 'error': 'thin_page',
                'message': ("There isn't enough on that page for us to work from "
                            "— it may be a landing page, or built in a way we "
                            "can't read. Please describe the business instead.")}

    out = class_agent.answers_from_website(text, cfg={})
    if not out.get('ok'):
        return {'ok': False, 'status': 200, 'error': out.get('error', 'agent_failed'),
                'message': ("We couldn't make sense of that page — please "
                            "describe the business instead.")}

    out['status'] = 200
    out['source_url'] = final_url
    return out


# ---------------------------------------------------------------------------
# Search-bar lookup handlers (the reusable component's backend)
# ---------------------------------------------------------------------------

def handle_lookup(action: str, params: dict, client) -> dict:
    """Dispatch the standalone search bar's calls.

    action: 'marks' | 'mark' | 'owners' | 'owner' | 'basket'
    params: {q, number, id} as relevant.
    """
    from . import lookup as lk
    from .term_basket import from_trademark_detail

    try:
        if action == 'marks':
            return {'ok': True, 'status': 200,
                    **lk.search_marks(client, str(params.get('q', '')))}
        if action == 'owners':
            return {'ok': True, 'status': 200,
                    **lk.search_owners(client, str(params.get('q', '')))}
        if action == 'mark':
            m = lk.get_mark(client, str(params.get('number', '')))
            if not m:
                return {'ok': False, 'status': 404, 'error': 'trademark not found'}
            m.pop('_detail', None)
            return {'ok': True, 'status': 200, 'mark': m}
        if action == 'owner':
            o = lk.get_owner(client, params.get('id'))
            if not o:
                return {'ok': False, 'status': 404, 'error': 'owner not found'}
            return {'ok': True, 'status': 200, **o}
        if action == 'basket':
            m = lk.get_mark(client, str(params.get('number', '')))
            if not m:
                return {'ok': False, 'status': 404, 'error': 'trademark not found'}
            basket = from_trademark_detail(m['_detail'], source_label=m['name'])
            return {'ok': True, 'status': 200, 'basket': basket.to_dict()}
        if action == 'sic':
            from . import sic_engine
            mapping = sic_engine.map_sic_codes(params.get('q', ''))
            return {'ok': True, 'status': 200, **mapping,
                    'basket': sic_engine.to_basket(mapping).to_dict()}
        if action == 'company':
            # Companies House search (name or number) — the friendly SIC UI.
            from . import company
            return {'ok': True, 'status': 200,
                    **company.search_companies(str(params.get('q', '')))}
        if action == 'company-classes':
            from . import company
            r = company.company_classes(str(params.get('number', '')))
            if r is None:
                return {'ok': False, 'status': 404, 'error': 'company not found'}
            return {'ok': True, 'status': 200, **r}
        if action == 'sectors':
            from . import taxonomy
            return {'ok': True, 'status': 200, 'sectors': taxonomy.sectors(),
                    'activities': [{'key': k, **{kk: vv for kk, vv in v.items()
                                                 if kk != 'allows'}}
                                   for k, v in taxonomy.ACTIVITIES.items()],
                    'expansion': {'key': taxonomy.EXPANSION_FLAG,
                                  'label': taxonomy.EXPANSION_LABEL}}
        if action == 'find':
            # ONE global search over sectors + business types. A sector result
            # is a signpost (show its business types); a business type goes
            # straight to classes.
            from . import taxonomy
            return {'ok': True, 'status': 200,
                    **taxonomy.search(str(params.get('q', '')))}
        if action == 'find-business-type':      # back-compat
            from . import taxonomy
            return {'ok': True, 'status': 200,
                    **taxonomy.search_business_types(str(params.get('q', '')))}
        if action == 'business-types':
            from . import taxonomy
            return {'ok': True, 'status': 200,
                    'business_types': taxonomy.business_types(
                        str(params.get('sector', '')))}
        if action == 'taxonomy-classes':
            from . import taxonomy
            acts = [a for a in str(params.get('activities', '')).split(',') if a]
            r = taxonomy.resolve(str(params.get('sector', '')),
                                 str(params.get('business_type', '')),
                                 acts,
                                 str(params.get('plan_to_expand', '')).lower()
                                 in ('1', 'true', 'yes'))
            if r is None:
                return {'ok': False, 'status': 404,
                        'error': 'unknown sector / business type'}
            return {'ok': True, 'status': 200, **r}
        if action == 'industries':
            from . import industry
            return {'ok': True, 'status': 200,
                    **industry.search_industries(str(params.get('q', '')))}
        if action == 'industry-classes':
            from . import industry
            r = industry.industry_classes(str(params.get('name', '')))
            if r is None:
                return {'ok': False, 'status': 404, 'error': 'industry not found'}
            return {'ok': True, 'status': 200, **r}
        if action == 'band':
            # Frequency-band classes/terms across a real set of marks — either a
            # competitor owner's whole portfolio (?id=) or a hand-picked set of
            # application numbers (?numbers=UK...,UK...). Route 4 / team tool.
            from . import banding
            if params.get('numbers'):
                r = banding.band_from_numbers(client, params.get('numbers'))
            elif params.get('id'):
                r = banding.band_owner_portfolio(client, params.get('id'))
            else:
                return {'ok': False, 'status': 400,
                        'error': 'band needs id (owner) or numbers (marks)'}
            return {'ok': True, 'status': 200, **r,
                    'basket': banding.to_basket(r).to_dict()}
    except Exception:
        return {'ok': False, 'status': 502,
                'error': 'Lookup is temporarily unavailable. Please retry.'}
    return {'ok': False, 'status': 400, 'error': f'unknown lookup action {action!r}'}
