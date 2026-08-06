"""The headless Free Search service.

One function — `run_free_search` — that every client calls: the Trademark
Helpline site, the Temmy Portal, and an introducer's white-label embed. The
tenant is a field on the request, not a fork in the code. Build it once here
and the introducer integration is a config change rather than a rewrite.

PERFORMANCE MODEL (measured against live Temmy, 09 Jul 2026)
-----------------------------------------------------------
Two hard facts drive the design:

  * A `search_trademarks` call costs ~2.5-3s REGARDLESS of result-set size,
    and the server SERIALISES concurrent searches — firing stems in parallel
    is no faster than sequential. So the number of search calls is the
    dominant, irreducible cost.
  * A `get_trademark` detail call is ~0.15s and detail calls DO parallelise.

Therefore: issue ONE search call (a single prefix stem, generous page size —
size is free), then hydrate the top matches concurrently. That lands the whole
free search around ~4s. More stems = +3s each for marginal recall, so we don't.

RECALL
------
`search_trademarks` is a prefix search on the verbal element. A single short
stem still catches the variants that matter: searching "MOMENT" returns
"MOMENTUS" AND "MOMENTUM MORTGAGE" (the BR-013 threat). We pick one stem =
the head word truncated toward STEM_LEN, which trades a little precision for
one-call speed. The text-match filter (`mark_matches_any`, the audit's own
predicate) then keeps only genuine matches.

The exhaustive, class-correct retriever is a single `TemmyQueryRunsClient` SQL
query. The Query Runs routes ARE enabled (present in the API's OpenAPI schema),
but the `TEMMY_QUERY_RUNS_API_KEY` in secrets is currently rejected — every
query-runs route returns a custom 404 "Not found", the app's way of refusing an
unauthorised key. Pending a confirmed/refreshed key. Once it authenticates,
`_candidate_records` moves to SQL and this whole stem/hydrate dance disappears;
scoring, adapters and the contract stay put.

HYDRATION (confirmed necessary against live Temmy, 09 Jul 2026)
--------------------------------------------------------------
Temmy's SEARCH item is lean: mark text, status, mark_type, applicants[name] —
but NO classes and NO Companies House data. Both live only on the DETAIL
record (`get_trademark`). So after the text-match filter we detail-fetch each
matched candidate (free — it's our own DB) to pull classes, goods/services and
the CH block, THEN apply the class filter and score. Without this, any search
with a class filter would return nothing, because the lean search items have
no classes to match. We hydrate only text matches, so the fetch count tracks
the (small) matched set, not the (large) candidate pool.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from .adapters import from_temmy_item
from .models import FreeSearchRequest, FreeSearchResult, MarkRecord
from .scoring import mark_matches_any, score_record, worst_risk

log = logging.getLogger(__name__)

# Parallelism. Temmy is our own DB (free, unlimited) and its client's default
# 0.25s inter-call rate-limit is unnecessary here, so the free-search client is
# built with rate_limit_sec=0 (see api._make_client) and we fan calls out
# across a small thread pool. Detail fetches are I/O-bound; requests releases
# the GIL, so this turns ~20 sequential round-trips into a couple of seconds.
POOL_WORKERS = 16

# One search call per phrase (search dominates latency; the server serialises
# concurrent searches, so extra calls only add ~3s each). Page size is free —
# latency is flat in result-set size — so we cast a wide net in the one call.
MAX_API_CALLS = 4          # = max phrases (word marks + tagline)
PAGE_SIZE = 500

# Detail fetches ARE fast and DO parallelise; hydrate the top slice by a lean
# pre-score (text similarity + status + type off the search item). Bounds the
# round-trips to HYDRATE_TOPN. The gated full report can hydrate deeper later.
HYDRATE_TOPN = 12

# Single stem length. The head word truncated toward this many chars: long
# enough to stay specific, short enough to catch close variants in the ONE
# search call ('MOMENTUS' -> 'MOMENT', which also returns MOMENTUM…). The whole
# word is used when it's already shorter.
STEM_LEN = 6
MIN_STEM = 4


def _stems(phrase: str) -> list[str]:
    """Return the SINGLE best prefix stem for a phrase (one search call).

    We deliberately do NOT expand into multiple stems: each extra stem is a
    separate ~3s serialised search on Temmy's side for marginal recall. One
    short prefix of the head word catches the exact mark and its close variants
    in a single call:

        'MOMENTUS'        -> ['MOMENT']   (also matches MOMENTUM MORTGAGE)
        'MONZO'           -> ['MONZO']    (already short)
        'FRIARS PHARMACY' -> ['FRIARS']   (head word; verbal element indexes L→R)
    """
    p = (phrase or '').strip()
    if not p:
        return []
    head = re.split(r'\s+', p)[0]
    if len(head) <= STEM_LEN:
        return [head]
    stem = head[:STEM_LEN]
    return [stem if len(stem) >= MIN_STEM else head]


def _candidate_records(client, request: FreeSearchRequest) -> tuple[list[MarkRecord], bool]:
    """Retrieve a candidate pool from Temmy. Returns (records, truncated).

    Stem searches run concurrently — they're independent GETs and the client
    has no per-call cost, so there's no reason to serialise them.
    """
    phrases = [p for p in (*request.word_marks, request.tagline) if p]
    stems: list[str] = []
    for phrase in phrases:
        for stem in _stems(phrase):
            if stem not in stems:
                stems.append(stem)
    truncated = False
    if len(stems) > MAX_API_CALLS:
        stems = stems[:MAX_API_CALLS]
        truncated = True

    def _search(stem):
        try:
            return (client.search_trademarks(text=stem, limit=PAGE_SIZE)
                    or {}).get('items', [])
        except Exception:
            log.warning('temmy search failed for stem %r', stem, exc_info=True)
            return None

    seen: dict[str, MarkRecord] = {}
    with ThreadPoolExecutor(max_workers=POOL_WORKERS) as pool:
        for items in pool.map(_search, stems):
            if items is None:
                truncated = True
                continue
            for item in items:
                rec = from_temmy_item(item)
                if rec is None:
                    continue
                key = rec.application_number or f'{rec.mark_text}|{rec.filing_date}'
                seen.setdefault(key, rec)

    return list(seen.values()), truncated


def _hydrate(client, records: list[MarkRecord]) -> None:
    """Replace lean search records with their full detail records in place.

    Search items lack classes + CH data; the detail record has everything.
    We fetch it for each text-matched record (free) and copy the rich fields
    across, preserving the object identity so the caller's list is updated.
    Capped at MAX_HYDRATE for latency; on failure we keep the lean record so
    the search still returns something.
    """
    todo = [rec for rec in records
            if rec.application_number and not (rec.classes or rec.company)]
    if not todo:
        return

    def _fetch(rec):
        try:
            return rec, client.get_trademark(rec.application_number)
        except Exception:
            log.warning('temmy detail fetch failed for %s',
                        rec.application_number, exc_info=True)
            return rec, None

    with ThreadPoolExecutor(max_workers=POOL_WORKERS) as pool:
        for rec, detail in pool.map(_fetch, todo):
            if not detail:
                continue
            full = from_temmy_item(detail)
            if not full:
                continue
            rec.classes = full.classes or rec.classes
            rec.goods_services = full.goods_services or rec.goods_services
            rec.company = full.company
            rec.companies = full.companies
            rec.owner_name = rec.owner_name or full.owner_name
            rec.filing_date = full.filing_date or rec.filing_date
            rec.image_url = full.image_url or rec.image_url
            rec.mark_type = full.mark_type or rec.mark_type
            if full.status:
                rec.status = full.status
                rec.status_display = full.status_display or rec.status_display


def _enrich_company_only(client, records: list[MarkRecord]) -> None:
    """Fill Companies House owner detail on the shortlist (QR path).

    The Query Runs rows already carry classes/status/type, so we only need a
    detail fetch to pull the CH block for the handful of records we display.
    Parallel and small (top-N), so it stays sub-second.
    """
    todo = [r for r in records if r.application_number and r.company is None]
    if not todo:
        return

    def _fetch(rec):
        try:
            return rec, client.get_trademark(rec.application_number)
        except Exception:
            return rec, None

    with ThreadPoolExecutor(max_workers=POOL_WORKERS) as pool:
        for rec, detail in pool.map(_fetch, todo):
            if not detail:
                continue
            full = from_temmy_item(detail)
            if not full:
                continue
            rec.company = full.company
            rec.companies = full.companies
            rec.goods_services = rec.goods_services or full.goods_services
            rec.filing_date = full.filing_date or rec.filing_date


def run_free_search(client, request: FreeSearchRequest,
                    qr_retriever=None) -> FreeSearchResult:
    """UK IPO free search: retrieve, filter, score, band.

    `client` is a `temmy.TemmyClient` (duck-typed, so tests can pass a fake).
    `qr_retriever` is an optional `queryruns.QueryRunsRetriever`. When present,
    candidates come from ONE Query Runs SQL call carrying classes/status/type/
    applicant — fast (~1.5s) and class-complete, so class filtering is exact
    and no per-record hydration is needed (CH detail is fetched only for the
    displayed shortlist). Without it, we fall back to the REST search+hydrate
    path. Scoring, adapters, serializer and gate are identical either way.
    """
    result = FreeSearchResult(request=request)

    word_searches = request.word_searches()
    if not word_searches:
        result.notes.append('No word mark or tagline supplied.')
        return result

    target_classes = tuple(request.classes)
    axis = 'image' if request.image_bytes and not request.word_marks else 'word'

    if qr_retriever is not None:
        # --- Query Runs path: class-complete candidates in one call ----------
        stems: list[str] = []
        for phrase in [p for p in (*request.word_marks, request.tagline) if p]:
            for s in _stems(phrase):
                if s not in stems:
                    stems.append(s)
        try:
            records, truncated = qr_retriever.search(stems)
        except Exception:
            log.warning('query-runs retrieval failed; falling back to REST',
                        exc_info=True)
            records, truncated = _candidate_records(client, request)
            qr_retriever = None
        result.truncated = truncated

    if qr_retriever is not None:
        matched = [rec for rec in records
                   if mark_matches_any(rec.mark_text, word_searches)]
        kept: list[MarkRecord] = []
        for rec in matched:                     # classes already present
            if target_classes and not set(rec.classes) & set(target_classes):
                continue
            kept.append(score_record(rec, target_classes, word_searches, axis=axis))
        kept.sort(key=lambda r: (r.image_score if axis == 'image'
                                 else r.word_score), reverse=True)
        # Only the shortlist (top 5) shows company detail on the anonymous view;
        # enrich a small margin. The gated full report enriches deeper later.
        _enrich_company_only(client, kept[:6])
        result.conflicts = kept
        result.total_conflicts = len(kept)
        result.overall_risk = worst_risk(kept)
        _finalise_notes(result, request)
        return result

    # --- REST fallback path (search + hydrate) -------------------------------
    records, truncated = _candidate_records(client, request)
    result.truncated = truncated

    matched = [rec for rec in records
               if mark_matches_any(rec.mark_text, word_searches)]
    for rec in matched:
        score_record(rec, (), word_searches, axis=axis)
    matched.sort(key=lambda r: (r.image_score if axis == 'image'
                                else r.word_score), reverse=True)
    top = matched[:HYDRATE_TOPN]
    _hydrate(client, top)
    if len(matched) > HYDRATE_TOPN:
        result.truncated = True
    kept = []
    for rec in top:
        if target_classes and not set(rec.classes) & set(target_classes):
            continue
        kept.append(score_record(rec, target_classes, word_searches, axis=axis))
    kept.sort(key=lambda r: (r.image_score if axis == 'image'
                             else r.word_score), reverse=True)

    result.conflicts = kept
    result.total_conflicts = len(kept)
    result.overall_risk = worst_risk(kept)
    _finalise_notes(result, request)
    return result


def _finalise_notes(result: FreeSearchResult, request: FreeSearchRequest) -> None:
    if request.image_bytes:
        # Free tier accepts the logo, captures it as a lead qualifier, and
        # does NOT attempt Vienna classification. Figurative conflict analysis
        # is what the paid audit is for (BR-009 lives there).
        result.notes.append(
            'Logo received. Figurative conflict analysis requires Vienna '
            'classification and visual similarity scoring against the '
            'figurative register — both are part of the Brand Audit.')
    if result.truncated:
        result.notes.append(
            'Search breadth was capped. A Brand Audit runs an unbounded '
            'search across the full register.')
