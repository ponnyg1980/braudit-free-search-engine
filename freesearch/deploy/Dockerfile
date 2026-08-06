# Python engine container — the private scoring core behind the Supabase Edge
# front door. No web framework: freesearch/api.py serves the controller on
# stdlib http.server, so the image is tiny and cold-starts fast.
#
# Build context is the repo root (needs both freesearch/ and deploy-v2-hotfix/,
# because the engine imports temmy.py and jurisdictions.py from there):
#   docker build -f freesearch/deploy/Dockerfile -t braudit-free-search .

FROM python:3.12-slim

WORKDIR /app

# The engine itself is stdlib-only. temmy.py uses `requests`.
RUN pip install --no-cache-dir requests

# Bring in the engine and its two cross-imports.
COPY freesearch/ /app/freesearch/
COPY deploy-v2-hotfix/temmy.py /app/deploy-v2-hotfix/temmy.py
COPY deploy-v2-hotfix/jurisdictions.py /app/deploy-v2-hotfix/jurisdictions.py

# The /enrich route's resolver (added 01 Aug 2026). controller.py::_resolver()
# sys.path-inserts ../temmy-lead-engine and imports contact_resolver, which in
# turn imports ch_enrich -> ghl_push + daily_maintenance, and search_guard.
# These FIVE FILES ONLY — the temmy-lead-engine folder itself is ~120 MB of
# local ops data (CSVs, xlsx, JSON state) that must never enter the image.
# Without these, POST /enrich raises ImportError; the Edge Function catches it
# and records "not found", so lead capture still works but enrichment silently
# never finds anything.
COPY temmy-lead-engine/contact_resolver.py   /app/temmy-lead-engine/contact_resolver.py
COPY temmy-lead-engine/ch_enrich.py          /app/temmy-lead-engine/ch_enrich.py
COPY temmy-lead-engine/ghl_push.py           /app/temmy-lead-engine/ghl_push.py
COPY temmy-lead-engine/search_guard.py       /app/temmy-lead-engine/search_guard.py
COPY temmy-lead-engine/daily_maintenance.py  /app/temmy-lead-engine/daily_maintenance.py

ENV PORT=8080
EXPOSE 8080

# Cloud Run sets $PORT; api.serve() honours it.
CMD ["python", "-m", "freesearch.api"]
