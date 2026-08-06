# Free Search — deployment architecture

## Decision (09 Jul 2026)

**Supabase Edge Function = public front door. Python engine = search core,
behind it.**

Chosen over a full TypeScript port of the engine. Reason: the scoring in
`freesearch/scoring.py` is parity-locked to the paid audit's `filters.py` by
6,815 tests. A TS re-implementation re-opens the free-vs-paid risk-band
divergence those tests exist to prevent, and would need its own parity port
maintained forever. Not worth it. Keep one scoring engine, in Python.

```
Browser (TMH site / Temmy Portal / introducer embed)
   │  POST /free-search   GET /jurisdictions
   ▼
Supabase Edge Function  (Deno)              ← public, tenant-facing
   • CORS + tenant allow-list
   • IP rate-limit
   • lead capture → Zoho + Supabase (fast-follow)
   │  proxies search calls to ↓
   ▼
Python engine  (Cloud Run / any container)  ← private, scoring source of truth
   • freesearch.controller.handle_free_search
   • parity-locked to filters.py
   │  free, unlimited ↓
   ▼
TemmyDB HTTP API  (proprietary)
```

Supabase owns exactly the things it's good at: the edge, auth, rate-limiting,
and the CRM/lead seam. Python owns the one thing that must not fork: the risk
score. The boundary between them is the `API_CONTRACT.md` JSON.

## What runs where

| Concern | Home | Status |
|---|---|---|
| Wizard UI, results page | TMH site (embed) | front-end, in parallel |
| CORS, tenant allow-list, rate-limit | Supabase Edge | scaffolded here |
| `/free-search`, `/jurisdictions` logic | Python engine | **built + tested** |
| Retrieve + score + enrich | Python engine | **built + tested** |
| Account gate (1 search/IP → OTP account) | Supabase Edge + migration `0001` | **built** |
| Business profile capture | `temmy_business_profile` table | **built** |
| Lead → Zoho push | Supabase Edge `/account` | seam stubbed, fast-follow |
| TemmyDB access | Python engine | live key needed |

## The account gate (agreed 09 Jul 2026)

1 free anonymous search per IP per tenant. The 2nd+ anonymous search requires a
**Free Temmy Account** — email OTP (Supabase Auth), business info captured,
portal account created. Account holders search unlimited and get the ungated
report. The account replaces the download form as the lead gate: a verified
email beats a form, and it lands them on the portal.

- Enforced in the Edge Function via `free_search_count_for_ip` (migration
  `0001`). Raw IP is never stored — only a salted SHA-256 hash (GDPR / LIA).
- The cap counts only anonymous rows, so an account holder is never blocked by
  their own earlier anonymous search.

## Go-live checklist

1. Apply migration `supabase/migrations/0001_free_search_gate.sql`.
2. Deploy Python engine container (`deploy/` — Dockerfile). Set `TEMMY_API_KEY`.
   Confirm one live search returns the expected applicant/company envelope
   (validates the adapter against real data).
3. Deploy the Edge Function. Set `ENGINE_URL`, `ALLOWED_ORIGINS` (TMH domain),
   `TENANT_ALLOWLIST`, `IP_HASH_SALT`.
4. Front-end: wizard + results page + the `account_required` → OTP → retry
   loop, against `API_CONTRACT.md`.
5. Ship. First search is anonymous; the second converts to an account.

## Fast-follow

- Zoho push inside the `/account` handler → store `zoho_lead_id`.
  `expand_for_profiling` (EU→27) fires at that write, not before.
- term_basket (routes 2b–2e) and the Query Runs trigram retriever.

## Alternative, if you'd rather

If you specifically want *everything* inside Supabase with no separate
container, the fallback is a full TS port of `scoring.py` + `adapters.py` +
`service.py`. Doable, but it must ship with a TS parity harness that re-runs
the same case space against a JSON snapshot of the Python results, or the free
and paid tiers will silently drift. Say the word and I'll scope it.
