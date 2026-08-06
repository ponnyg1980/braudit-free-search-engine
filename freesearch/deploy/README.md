# Deploy — Python engine + Supabase Edge front door

Two pieces (see `../ARCHITECTURE.md` for why): a private Python engine and a
public Supabase Edge Function that proxies to it.

## 1. Python engine → Cloud Run

```bash
# from repo root (build context needs freesearch/ + deploy-v2-hotfix/)
gcloud run deploy braudit-free-search \
  --source . \
  --dockerfile freesearch/deploy/Dockerfile \
  --set-env-vars TEMMY_API_KEY=<key> \
  --no-allow-unauthenticated        # only the Edge Function may call it
```

Smoke it (through an authed proxy or locally):

```bash
python -m freesearch.api           # serves on :8080
curl localhost:8080/jurisdictions
curl -X POST localhost:8080/free-search \
  -H 'Content-Type: application/json' \
  -d '{"name":"MOMENTUS","classes":[36],"trading_now":["GB"]}'
```

**Do this first and eyeball one live result** — it validates the Companies
House adapter against a real Temmy envelope. If field names differ from the
documented schema, the fix is confined to `adapters._temmy_companies`.

## 2. Supabase Edge Function

```bash
supabase functions deploy free-search \
  --project-ref <ref>
supabase secrets set \
  ENGINE_URL=<cloud-run-url> \
  ALLOWED_ORIGINS=https://www.thetrademarkhelpline.com \
  TENANT_ALLOWLIST=tmh \
  RATE_LIMIT_PER_MIN=20
```

Front-end then calls the Edge Function URL:
`https://<ref>.supabase.co/functions/v1/free-search`.

## 3. Front-end

Build against `../API_CONTRACT.md`. The Edge Function passes payloads through
unchanged, so the contract is identical whether you hit the engine directly
(dev) or the Edge Function (prod).

## Launch state

Search live, gate manual: the Download button collects the lead form; the
`/lead` handler returns 501 until the fast-follow gate ships. Reports are
fulfilled manually in the interim.
