# Deploy brief — Braudit Free Search engine (Cloud Run)

Hi Alex,

One new Cloud Run service please, next to the Temmy API. It's the public
Free Trademark Search: the wizard UI and its scoring engine in one small
container (stdlib Python + `requests`, no framework). Everything is in the
MOAT project; the Dockerfile is ready.

## Build & deploy

Build context is the project root (the engine imports `temmy.py` /
`jurisdictions.py` from `deploy-v2-hotfix/`):

```bash
docker build -f freesearch/deploy/Dockerfile -t braudit-free-search .
gcloud run deploy braudit-free-search \
  --image <pushed-image> \
  --region europe-west2 \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 5 --memory 512Mi
```

## Environment variables (values from the usual key store — never in this file)

| Var | Purpose |
|---|---|
| `TEMMY_API_BASE_URL` | as per Temmy API |
| `TEMMY_API_KEY` | standard key (search + detail fetch) |
| `TEMMY_QUERY_RUNS_API_KEY` | enables the fast single-query path (~2-3s searches). Omit and it falls back to REST search+hydrate (slower but works) |
| `ALLOWED_ORIGINS` | comma-separated CORS allow-list, e.g. `https://thetrademarkhelpline.com,https://portal.temmy.co.uk`. Unset = `*` (fine for the test deploy, tighten before partner rollout) |
| `SerperClaudeAPI` | **added 01 Aug 2026** — powers the `/enrich` contact resolver. ⚠️ note the non-standard casing: every other key is UPPER_SNAKE, this one deliberately isn't. `contact_resolver.load_cfg()` reads `os.environ` by that exact spelling, so a "tidied" `SERPER_CLAUDE_API` silently loads nothing |
| `COMPANIES_HOUSE_API_KEY` | ditto — free tier, the other half of the `/enrich` resolver |

Watch for trailing newlines/spaces when pasting keys — a `\n` in a key makes
an invalid HTTP header (we hit exactly this with the MCP deployment's key).

## What the service exposes

| Route | What |
|---|---|
| `GET /` | the Free Search wizard (self-contained HTML) |
| `GET /embed.js` | partner embed script (iframe injector, tenant-aware) |
| `GET /class-assistant` | the class-selection widget |
| `GET /jurisdictions` | jurisdictions list for the wizard |
| `POST /free-search` | the search itself (JSON contract in `freesearch/API_CONTRACT.md`) |
| `GET /lookup/<action>` | class-tool lookups (company/marks/owners/sic…) |
| `GET /healthz` | liveness |

## Smoke test after deploy

```bash
curl -s https://<service-url>/healthz
curl -s -X POST https://<service-url>/free-search \
  -H 'Content-Type: application/json' \
  -d '{"word_marks":["MOMENTUS"],"classes":[36],"jurisdictions":{"trading_now":["GB"]},"tenant_id":"tmh"}'
# expect: summary with total_flagged ~52, overall_risk "Medium Risk", displayed 5
```

Then open `https://<service-url>/` in a browser — the full wizard journey
should run: Name → Searches → Jurisdictions → Classes → Results.

## How sites embed it (for reference)

```html
<script src="https://<service-url>/embed.js" data-tenant="tmh" async></script>
```

`data-tenant` distinguishes TMH / portal / each introducer; it rides through
to the API on every search, so per-tenant reporting and Zoho routing work.
A custom domain (e.g. `search.thetrademarkhelpline.com`) can be mapped in
Cloud Run whenever convenient — the embed snippet just changes host.

Ta,
Jonathan
