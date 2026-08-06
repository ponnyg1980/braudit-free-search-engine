# Free Search — headless service

One endpoint, three clients: thetrademarkhelpline.com, the Temmy Portal, and
introducer white-label embeds. Tenant is a field on the request, not a fork.

```
freesearch/
├── models.py      MarkRecord, FreeSearchRequest/Result, Jurisdictions
├── adapters.py    from_xlsx_row (parity anchor) | from_temmy_item (new)
├── scoring.py     pure scoring over MarkRecord — no I/O, no ML
├── service.py     run_free_search(client, request)
└── tests/
    └── test_parity.py   6,815 assertions that we match filters.py exactly
```

## Why this shape

The audit's scoring in `deploy-v2-hotfix/filters.py` was correct but welded to
positional xlsx row tuples (`r[2]` status, `r[3]` type, `r[5]` mark, `r[7]`
classes). That is the only reason it couldn't be pointed at the live Temmy
API. `MarkRecord` breaks the coupling; the scores are untouched.

The free tier uses the **audit's** engine, not the Toolkit PoC's
BERT/CLIP/Metaphone stack. Three reasons:

1. **Cost.** Deterministic, stdlib-only (`difflib`). No model load, no
   per-call inference. The *scoring* is free; TemmyDB queries are also free
   (our own DB), so the whole tier costs effectively nothing to serve.
2. **Vocabulary.** Free report and paid audit must emit the same risk bands
   for the same mark, or the upsell undermines itself.
3. **Provenance.** `filters.py` has been tuned against real reports (BR-013).

## Run the contract test before every deploy

```bash
python3 -m pytest freesearch/tests/test_parity.py -q
```

Red means the free search and the paid audit have started disagreeing about
risk. That is a commercial bug, not a code smell.

## Companies House layer

TemmyDB links trademark applicants to Companies House, so the free report
shows *who* owns each conflicting mark — company name, number, status, SIC
sector, town — not just the mark. A conflict held by a **dissolved** company
is a materially different risk story than one held by an active competitor,
and the shortlist now surfaces that (`CompanyInfo.is_dissolved`).

Because the API is free and unlimited (it's our own DB), the service
detail-fetches (`get_trademark`) each *shortlisted* record whose search item
lacked company data, then reads the CH block off the applicant. Only matches
are enriched, so fetch count tracks the result set, not the candidate pool.

## Known limits (deliberate, documented)

**UPDATE (15 Jul 2026) — Query Runs is live; SQL retriever shipped.** Query Runs
was re-enabled on `temmy-api-prod` (no-key now returns the correct `401`, valid
key `200`). `queryruns.py` runs ONE sanitised `ILIKE` SQL call that returns
every matching mark with its classes/status/type/applicant in ~1.5s.
`service.run_free_search(..., qr_retriever=...)` uses it as the primary path
(REST search+hydrate remains the automatic fallback if QR is unavailable).
Result: **class filtering is now exact** — `STEALTH` in class 11 returns its 12
real marks (was 0), and whole-search latency is ~2-3s. Scoring/adapters/
serializer/gate/wizard are unchanged; only `_candidate_records` was swapped.
The section below documents the REST path that this superseded.

**HISTORIC (09 Jul 2026) — the REST retriever and why it needed replacing.** Validated end-to-end against live TemmyDB. The adapter now handles the
real shapes correctly (top-level `mark_type`; classes + Companies House only on
the detail record under `applicants[].json_attributes.companies_house_data`;
statuses like `Dead`/`Refused`/`Application Published` normalised to the
canonical set). But two things surfaced that the REST `search` + per-record
`get_trademark` design can't solve well:

1. **Latency ~8s.** Search items carry no classes/CH, so each match needs a
   detail fetch. Even parallelised (thread pool, rate-limit off) and bounded to
   the top 12, it's too slow for a website.
2. **Class filtering is lossy.** Because we can only afford to hydrate the top
   text-similarity slice, a mark that matches the searched *class* but sits
   outside that slice is missed — e.g. `STEALTH` in class 11 returns 0 even
   though STEALTH marks exist across classes 7/9/10/28.

The definitive fix is `TemmyQueryRunsClient`: one SQL query that text-matches,
class-filters, and returns classes + applicant + CH in a single round-trip.

**Blocker: Query Runs is globally DISABLED on the prod deployment**
(`temmy-api-prod`). The routes exist in code (OpenAPI lists
`POST /api/v2/query-runs` etc.), but every call returns `404 {"detail":"Not
found"}`. Proven against Temmy's own privileged doc
(`temmy-access/temmy-query-runs-privilegedbackup.md`): its status table says
`401` = missing/invalid key, `404` = "Query Runs disabled". A call with **no
key header** returns 404 (not 401), so it is not a credential problem — it is
the server-side disable flag ("Query Runs can be globally disabled by the
server; disabled deployments return 404").

Action to unblock: Temmy admin enables Query Runs on the prod deployment. No
code or key change needed on our side. Once enabled, `_candidate_records`
moves to SQL, the search drops to ~1-2s and class filtering becomes complete;
scoring, adapters and the contract stay put.

**What we did instead (measured, live):** the search endpoint is ~2.5-3s per
call and the server *serialises* concurrent searches, while detail fetches are
~0.15s and *do* parallelise. So the retriever now makes ONE search call (a
single short prefix stem — `MOMENT` still catches `MOMENTUS` and `MOMENTUM
MORTGAGE`), with a wide page size (latency is flat in result size), then
hydrates the top matches concurrently over a pooled keep-alive session. That
takes the free search from 8-25s down to **~4s**. Connection pooling
(`temmy_pooled.py`) matters mostly for the parallel detail fetches.

Residual limit until Query Runs lands: class filtering is applied after
hydrating only the top text-similarity slice, so a mark matching the searched
*class* but outside that slice can be missed (e.g. a class-specific `STEALTH`).
Acceptable for a free teaser that sells the exhaustive audit; fully fixed by
the SQL retriever. `_candidate_records()` is the only function that changes
when Query Runs is switched on.

**Retrieval recall (REST path).** `TemmyClient.search_trademarks` is a *prefix* search.
The scorer is more sensitive than the retriever: a naive call for `MOMENTUS`
never returns `MOMENTUM MORTGAGE`, which BR-013 grades a real threat. We
compensate with prefix expansion (`MOMENTUS → MOMENTU → MOMENT → …`) and,
because queries are free, spend them freely (up to `MAX_API_CALLS`). This
recovers stem variants but not internal-substring marks.

The clean upgrade is `_candidate_records()` on `TemmyQueryRunsClient` with a
trigram / `ILIKE '%stem%'` query — true substring recall in one call. Bounded
change; scoring, adapters and the report contract stay put. Left as a
follow-up only because a *public* endpoint hitting the privileged SQL layer
wants an abuse/rate guard in front of it (our server load, not query cost).

**Vienna / logo.** The free tier accepts a logo, stores it as a lead
qualifier, and does **not** classify it. Figurative conflict analysis needs
Vienna codes + visual similarity — that is the Brand Audit (BR-009, parked).
This keeps the free/paid boundary exactly where the business model needs it,
and keeps Vienna off the critical path of the lead engine.

**Duplication.** `scoring.py` currently re-implements rather than imports
`filters.py`, because `filters.py` pulls in `openpyxl` at module scope. The
parity test is the guard. Follow-up: switch `filters.py` to delegate here,
then this test becomes a tautology and can be deleted.

## Not yet built

- Lead capture + email gate (`FreeSearchResult.preview()` is the hook)
- Zoho / Supabase push
- Nice **terms** directory — `nice_classes.py` has 45 headings, not terms.
  Every class-selection route (2a–2e) must output specification *terms*, not
  just class numbers, because terms feed the application. Build the
  `term_basket` once, then five populators.
