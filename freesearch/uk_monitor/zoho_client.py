"""Direct Zoho CRM API client — self-client OAuth, no connector.

The production transport for every bulk write and for the fortnightly job.
Credentials live in ../secrets.env and are never logged or printed:

    ZOHO_CLIENT_ID=1000.XXXX
    ZOHO_CLIENT_SECRET=...
    ZOHO_REFRESH_TOKEN=...        (written by setup_zoho_selfclient.py)

US data centre (crm.zoho.com -> accounts.zoho.com / www.zohoapis.com).
Access tokens last ~1h and are cached in /tmp between invocations.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from config import SECRETS_PATH

ACCOUNTS = "https://accounts.zoho.com"
API = "https://www.zohoapis.com/crm/v2"
import os as _os
_TOKEN_CACHE = Path(_os.environ.get("ZOHO_TOKEN_CACHE") or f"/tmp/zoho_access_token-{_os.getuid()}.json")


def _secrets() -> dict:
    out = {}
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


class ZohoAuthError(RuntimeError):
    pass


def access_token(force: bool = False) -> str:
    if not force and _TOKEN_CACHE.exists():
        c = json.loads(_TOKEN_CACHE.read_text())
        if c.get("expires_at", 0) > time.time() + 60:
            return c["token"]
    s = _secrets()
    for k in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"):
        if not s.get(k):
            raise ZohoAuthError(f"{k} missing from secrets.env - run "
                                "setup_zoho_selfclient.py first")
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": s["ZOHO_CLIENT_ID"],
        "client_secret": s["ZOHO_CLIENT_SECRET"],
        "refresh_token": s["ZOHO_REFRESH_TOKEN"],
    }).encode()
    req = urllib.request.Request(f"{ACCOUNTS}/oauth/v2/token", data=body,
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if "access_token" not in data:
        raise ZohoAuthError(f"token refresh failed: {data.get('error', '?')}")
    _TOKEN_CACHE.write_text(json.dumps({
        "token": data["access_token"],
        "expires_at": time.time() + int(data.get("expires_in", 3600))}))
    return data["access_token"]


def call(method: str, path: str, payload: dict | None = None,
         retries: int = 3) -> dict:
    """One API call with auth, 429/expiry retry. path e.g. 'Monitoring_Results/upsert'."""
    last = None
    for attempt in range(retries):
        tok = access_token(force=attempt > 0)
        req = urllib.request.Request(
            f"{API}/{path}", method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": f"Zoho-oauthtoken {tok}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            last = f"HTTP {e.code}: {body}"
            if e.code == 401 and attempt < retries - 1:
                continue                       # token expired - refresh
            if e.code == 429 and attempt < retries - 1:
                time.sleep(10 * (attempt + 1))  # rate limited - back off
                continue
            raise RuntimeError(f"Zoho API {path}: {last}") from None
    raise RuntimeError(f"Zoho API {path}: {last}")

def download(path: str, max_bytes: int = 6_000_000) -> bytes:
    """Raw GET (file bytes, not JSON) — e.g. 'files?id=<File_Id__s>' for a
    file-upload field. Needs the ZohoCRM.Files.READ scope (granted 2026-09-14).
    Retries once on 401 like call(); raises on any other HTTP error."""
    last = None
    for attempt in range(2):
        tok = access_token(force=attempt > 0)
        req = urllib.request.Request(f"{API}/{path}", method="GET",
                                     headers={"Authorization": f"Zoho-oauthtoken {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read(max_bytes)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            if e.code == 401 and attempt == 0:
                continue
            raise RuntimeError(f"Zoho API {path}: {last}") from None
    raise RuntimeError(f"Zoho API {path}: {last}")



def upsert(module: str, records: list[dict],
           duplicate_check_fields: list[str],
           trigger: list | None = None) -> list[dict]:
    """Upsert up to 100 records; returns per-record result dicts."""
    assert len(records) <= 100
    out = call("POST", f"{module}/upsert", {
        "data": records,
        "duplicate_check_fields": duplicate_check_fields,
        "trigger": trigger if trigger is not None else [],
    })
    return out.get("data", [])


def update(module: str, records: list[dict],
           trigger: list | None = None) -> list[dict]:
    assert len(records) <= 100
    out = call("PUT", module, {
        "data": records,
        "trigger": trigger if trigger is not None else [],
    })
    return out.get("data", [])
