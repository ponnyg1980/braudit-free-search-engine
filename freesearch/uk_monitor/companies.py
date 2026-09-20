"""D11 - trading evidence from Companies House.

    "Two results can sit at the same band on mark and trade alone. What
     separates them is whether the proprietor is demonstrably trading. Active
     trading moves the band up one step; dissolved or dormant moves it down.
     No company means the factor is skipped, never counted against them."

APPLIED ASYMMETRICALLY, ON PURPOSE (Jonathan's decision, 12 Aug).
--------------------------------------------------------------
Taken literally, "active moves the band up" is not a tie-breaker. "Active" is
simply the normal state of a UK company: on the pilot it moved 291 of 582
results (50%) up a band while only 27 moved down, which is a blanket uplift,
not a discriminator. So:

    dissolved / insolvent / dormant   ->  band DOWN one step
    active, trading in the same field ->  band UP one step   (tier 2)
    merely active                     ->  no shift
    no matching company               ->  no shift

This matches risk_model.TradingEvidence, which already distinguishes tier 1
(active) from tier 2 (active AND same_field).

SAME-FIELD IS NOT YET COMPUTABLE, AND IS NOT FAKED.
Companies House returns SIC codes as bare numbers - ['46900'] - with no
descriptions, and there is no authoritative SIC-to-Nice-class mapping. An
invented one would be a placeholder dressed as evidence, which is the exact
failure D16 records in the deployed code. So tier 2 is wired and will fire
the moment SIC descriptions are available, and until then the uplift never
triggers. The downward signal, which is the one that actually discriminates,
works now.

That last clause governs the design. A proprietor we cannot match to a UK
company is not penalised - individuals, overseas companies and unmatched
names all return tier 0, which shifts nothing. The only way to move a band is
positive evidence in one direction or the other.

MATCHING IS DELIBERATELY STRICT. Companies House search is fuzzy and will
happily return "ELM AND REYDON HALL MANAGEMENT COMPANY LIMITED" for a search
on "Reydon Sports". Accepting a loose match would attach one company's
trading status to a different company's trademark and move a client's risk
band on it. Only a normalised exact match counts; anything else is treated as
no evidence.

Results are cached in the ledger. Proprietors repeat heavily across a run and
across fortnights, and Companies House allows 600 requests per five minutes.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from config import has_secret, secret

API = "https://api.company-information.service.gov.uk"

# Refresh cached statuses monthly - a company's status changes rarely, and a
# stale "active" is a far smaller error than hammering the API every run.
MAX_AGE_DAYS = 30

# Moved into the scoring package 18 Sep 2026, unchanged. It is NOT the same
# pattern the audit engine uses - see tmh_scoring/ignore_words.py.
# Per-jurisdiction legal forms (finding G, ruled 19 Sep 2026). Every company
# this module sees comes from Companies House, so GB is the right default;
# the old pattern stripped GMBH, SARL, AS and SA from British names, and a
# bare AS eats ordinary English ("SHOP AS YOU GO LIMITED").
from tmh_scoring.ignore_words import normalise_key as _normalise_key  # noqa: E402

# Companies House status values, mapped to the three states D11 cares about.
_ACTIVE = {"active"}
_WEAK = {"dissolved", "liquidation", "receivership", "administration",
         "voluntary-arrangement", "converted-closed", "closed",
         "insolvency-proceedings"}


@dataclass
class TradingEvidence:
    """What we know about a proprietor's trading status."""
    tier: int = 0                  # +1 trading, -1 not trading, 0 no evidence
    company_number: str = ""
    company_name: str = ""
    company_status: str = ""
    has_company: bool = False
    dormant: bool = False
    sic_codes: str = ""
    note: str = ""


NO_EVIDENCE = TradingEvidence(note="no matching UK company - factor skipped")


def normalise(name: str, jurisdiction: str = "GB") -> str:
    return _normalise_key(name, jurisdiction)


def _get(path: str, timeout: int = 20):
    key = secret("COMPANIES_HOUSE_API_KEY")
    auth = base64.b64encode(f"{key}:".encode()).decode()
    req = urllib.request.Request(f"{API}{path}",
                                 headers={"Authorization": f"Basic {auth}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt * 3)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1 + attempt)
    return None


def _profile(number: str) -> dict:
    """Company profile - needed for dormancy, which the search result omits.

    D11 names dormant as a downward signal, but Companies House does not
    carry dormancy in company_status; it shows in the accounts type.
    """
    d = _get(f"/company/{urllib.parse.quote(number)}") or {}
    accounts = (d.get("accounts") or {}).get("last_accounts") or {}
    return {"dormant": (accounts.get("type") or "").lower() == "dormant",
            "sic_codes": ", ".join(d.get("sic_codes") or [])}


def _search(query: str, timeout: int = 20) -> list[dict]:
    key = secret("COMPANIES_HOUSE_API_KEY")
    auth = base64.b64encode(f"{key}:".encode()).decode()
    url = (f"{API}/search/companies?q={urllib.parse.quote(query[:150])}"
           f"&items_per_page=10")
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read()).get("items", []) or []
        except urllib.error.HTTPError as e:
            if e.code == 429:                      # rate limited - back off
                time.sleep(2 ** attempt * 3)
                continue
            return []
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1 + attempt)
    return []


def _classify(status: str, dormant: bool = False, same_field: bool = False) -> int:
    """-1 weaker threat, 0 no usable evidence, +2 stronger threat.

    Note there is no +1. risk_model returns 1 for a merely-active company,
    but a merely-active company is the norm and tells us nothing that
    separates one result from another - see the module docstring.
    """
    s = (status or "").strip().lower()
    if s in _WEAK:
        return -1
    if dormant:
        return -1                                   # filing dormant accounts
    if s in _ACTIVE:
        return 2 if same_field else 0
    return 0


def lookup(owner_name: str, led=None) -> TradingEvidence:
    """Trading evidence for one proprietor name."""
    key = normalise(owner_name)
    if not key or len(key) < 3:
        return NO_EVIDENCE
    if not has_secret("COMPANIES_HOUSE_API_KEY"):
        return TradingEvidence(note="Companies House key not configured")

    if led is not None:
        row = led.conn.execute(
            "select * from company_status where name_key=?", (key,)).fetchone()
        if row:
            age = (datetime.now() -
                   datetime.fromisoformat(row["refreshed_at"])).days
            if age <= MAX_AGE_DAYS:
                return TradingEvidence(
                    row["tier"], row["company_number"] or "",
                    row["company_name"] or "", row["company_status"] or "",
                    bool(row["has_company"]), bool(row["dormant"]),
                    row["sic_codes"] or "", row["note"] or "")

    ev = NO_EVIDENCE
    for item in _search(owner_name):
        if normalise(item.get("title", "")) == key:
            status = item.get("company_status") or ""
            number = item.get("company_number") or ""
            extra = _profile(number) if number else {"dormant": False, "sic_codes": ""}
            tier = _classify(status, extra["dormant"])
            note = f"Companies House: {status}"
            if extra["dormant"]:
                note += ", filing dormant accounts"
            ev = TradingEvidence(
                tier=tier, company_number=number,
                company_name=item.get("title") or "", company_status=status,
                has_company=True, dormant=extra["dormant"],
                sic_codes=extra["sic_codes"], note=note)
            break

    if led is not None:
        with led.tx() as c:
            c.execute(
                "insert or replace into company_status (name_key, tier, "
                " company_number, company_name, company_status, has_company, "
                " dormant, sic_codes, note, refreshed_at) "
                "values (?,?,?,?,?,?,?,?,?,?)",
                (key, ev.tier, ev.company_number, ev.company_name,
                 ev.company_status, 1 if ev.has_company else 0,
                 1 if ev.dormant else 0, ev.sic_codes, ev.note,
                 datetime.now().isoformat(timespec="seconds")))
    return ev


def lookup_many(names, led=None, workers: int = 6, log=print) -> dict:
    """Resolve a batch of proprietor names concurrently.

    Only names not already cached hit the API, so a second fortnightly run
    over the same proprietors costs nothing.
    """
    from concurrent.futures import ThreadPoolExecutor

    unique = sorted({n for n in names if n and normalise(n)})
    if not unique:
        return {}

    out: dict[str, TradingEvidence] = {}
    todo = []
    for n in unique:
        key = normalise(n)
        row = led.conn.execute("select * from company_status where name_key=?",
                               (key,)).fetchone() if led is not None else None
        if row and (datetime.now() -
                    datetime.fromisoformat(row["refreshed_at"])).days <= MAX_AGE_DAYS:
            out[n] = TradingEvidence(
                row["tier"], row["company_number"] or "", row["company_name"] or "",
                row["company_status"] or "", bool(row["has_company"]),
                bool(row["dormant"]), row["sic_codes"] or "", row["note"] or "")
        else:
            todo.append(n)

    if todo:
        # Look up without the ledger inside threads (SQLite connections are
        # not shareable), then write the cache serially afterwards.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for name, ev in zip(todo, pool.map(lambda n: lookup(n, None), todo)):
                out[name] = ev
        if led is not None:
            stamp = datetime.now().isoformat(timespec="seconds")
            with led.tx() as c:
                c.executemany(
                    "insert or replace into company_status (name_key, tier, "
                    " company_number, company_name, company_status, "
                    " has_company, dormant, sic_codes, note, refreshed_at) "
                    "values (?,?,?,?,?,?,?,?,?,?)",
                    [(normalise(n), out[n].tier, out[n].company_number,
                      out[n].company_name, out[n].company_status,
                      1 if out[n].has_company else 0,
                      1 if out[n].dormant else 0, out[n].sic_codes,
                      out[n].note, stamp) for n in todo])

    matched = sum(1 for e in out.values() if e.has_company)
    up = sum(1 for e in out.values() if e.tier > 0)
    down = sum(1 for e in out.values() if e.tier < 0)
    log(f"  trading: {len(unique)} proprietors, {matched} matched to a UK "
        f"company - {down} move a band DOWN (dissolved/insolvent/dormant), "
        f"{up} move UP (same-field trading), the rest shift nothing")
    return out
