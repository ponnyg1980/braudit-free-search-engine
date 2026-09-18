"""UK Companies House — company-name conflicts.

A different job from `uk_monitor/companies.py`, which looks UP a known
proprietor to decide whether they are trading (scoring decision D11). This
module searches OUT from the client's mark for companies whose NAME
conflicts with it.

Two API facts that shape the design, both confirmed against the live service
rather than the documentation:

  * `/search/companies` returns the name under **`title`**, not
    `company_name`. An integration that reads `company_name` gets a blank
    string for every row and no error. Worth stating plainly, because the
    Practiva audit reported "No data found" for Companies while the register
    in fact held PRACTIV (UK) LIMITED and PRACTIV APPLIED SOFTWARE LTD.

  * `/advanced-search/companies?company_name_includes=` does substring
    matching and returns `company_name`, `registered_office_address` and
    `sic_codes` inline — most of what a report row needs from one call.

So both are used: advanced search for substring reach, standard search for
its ranked fuzzy matching, deduped on company number.

PREVIOUS NAMES — value, and an honest limit. A company that recently renamed
itself close to a client's mark is among the strongest signals available,
and it lives only on the company profile, so we fetch profiles for rows that
could plausibly matter. But Companies House offers no search over previous
names: we can show that a company we already found used to be called
something else, and we cannot find a company because of what it used to be
called. That is a limit of the register, not of this code, and the report
should not imply otherwise.

Rate limit is 600 requests per five minutes. Profile enrichment is therefore
gated on the row scoring something, exactly as trademark goods enrichment is
gated on mark similarity.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent

from . import _paths  # noqa: F401  — sets up tmh_scoring imports

API = "https://api.company-information.service.gov.uk"
TIMEOUT = 25
MAX_RETRIES = 3
RATE_LIMIT_SEC = 0.12          # ~500/5min, inside the 600 allowance
ENRICH_MIN_SCORE = 3           # fetch the profile only if the row scored something

# Moved into the scoring package 18 Sep 2026, unchanged. It is NOT the same
# pattern Watch uses - see tmh_scoring/ignore_words.py for the divergence and
# why unifying them needs a ruling rather than an edit.
from tmh_scoring.ignore_words import LEGAL_FORMS_AUDIT as _LEGAL_FORMS  # noqa: E402

# SIC 2007 sections. Companies House returns bare codes; there is no API for
# descriptions, so this gives a human-readable sector without inventing the
# SIC-to-Nice mapping that does not exist (see uk_monitor/companies.py).
_SIC_SECTIONS = [
    (1, 399, "Agriculture, forestry & fishing"),
    (500, 999, "Mining & quarrying"),
    (1000, 3399, "Manufacturing"),
    (3500, 3599, "Electricity & gas"),
    (3600, 3999, "Water & waste"),
    (4100, 4399, "Construction"),
    (4500, 4799, "Wholesale & retail trade"),
    (4900, 5399, "Transport & storage"),
    (5500, 5699, "Accommodation & food service"),
    (5800, 6399, "Information & communication"),
    (6400, 6699, "Financial & insurance"),
    (6800, 6899, "Real estate"),
    (6900, 7599, "Professional, scientific & technical"),
    (7700, 8299, "Administrative & support service"),
    (8400, 8499, "Public administration & defence"),
    (8500, 8599, "Education"),
    (8600, 8899, "Human health & social work"),
    (9000, 9399, "Arts, entertainment & recreation"),
    (9400, 9699, "Other service activities"),
    (9700, 9899, "Household employers"),
    (9900, 9999, "Extraterritorial organisations"),
]


def sic_sector(code: str) -> str:
    try:
        n = int(str(code).strip())
    except (TypeError, ValueError):
        return ""
    for lo, hi, label in _SIC_SECTIONS:
        if lo <= n <= hi:
            return label
    return ""


class CompaniesHouseError(RuntimeError):
    """Raised when the register cannot be searched.

    Fail closed: an empty company list and an unreachable register must not
    look the same in a report.
    """


def _api_key() -> str:
    path = _HERE.parent / "secrets.env"
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip().upper() == "COMPANIES_HOUSE_API_KEY":
                return v.strip().strip('"').strip("'")
    raise CompaniesHouseError(f"COMPANIES_HOUSE_API_KEY not found in {path}")


@dataclass
class CompanyRow:
    name: str
    number: str
    status: str = ""
    company_type: str = ""
    incorporated: str = ""
    dissolved: str = ""
    address: str = ""
    jurisdiction: str = ""
    sic_codes: str = ""
    sic_sectors: str = ""
    previous_names: str = ""
    officers: str = ""
    url: str = ""
    source: str = "Companies House (UK)"
    found_by: list = field(default_factory=list)
    score: int = 0
    band: str = ""
    explanation: str = ""
    exclusion_status: str = "not_excluded"
    enriched: bool = False

    @property
    def dedupe_key(self) -> str:
        return (self.number or self.name or "").upper().strip()


class CompaniesHouseClient:
    def __init__(self, api_key: str | None = None):
        key = api_key or _api_key()
        self._auth = base64.b64encode(f"{key}:".encode()).decode()

    def _get(self, path: str):
        req = urllib.request.Request(
            API + path, headers={"Authorization": f"Basic {self._auth}",
                                 "User-Agent": "TMH-audit-engine/1.0"})
        last = None
        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429:                      # rate limited
                    time.sleep(3 * (2 ** attempt))
                    last = exc
                    continue
                if exc.code == 404:
                    return None
                raise CompaniesHouseError(
                    f"Companies House {exc.code} on {path}") from exc
            except Exception as exc:
                last = exc
                time.sleep(1 + attempt)
        raise CompaniesHouseError(f"Companies House unreachable: {last}")

    def advanced(self, phrase: str, size: int = 200) -> list[dict]:
        """Substring search over current company names."""
        out, start = [], 0
        while True:
            q = urllib.parse.urlencode({
                "company_name_includes": phrase, "size": min(size, 100),
                "start_index": start})
            data = self._get(f"/advanced-search/companies?{q}") or {}
            items = data.get("items") or []
            out.extend(items)
            start += len(items)
            if len(items) < min(size, 100) or start >= min(size, data.get("hits", 0)):
                break
            time.sleep(RATE_LIMIT_SEC)
        return out

    def standard(self, phrase: str, size: int = 50) -> list[dict]:
        """Ranked fuzzy search. Note: the name is in `title`."""
        q = urllib.parse.urlencode({"q": phrase[:150], "items_per_page": size})
        data = self._get(f"/search/companies?{q}") or {}
        return data.get("items") or []

    def profile(self, number: str) -> dict:
        return self._get(f"/company/{urllib.parse.quote(number)}") or {}

    def officers(self, number: str, limit: int = 5) -> list[str]:
        data = self._get(f"/company/{urllib.parse.quote(number)}/officers"
                         f"?items_per_page={limit}") or {}
        out = []
        for o in (data.get("items") or [])[:limit]:
            role = (o.get("officer_role") or "").replace("-", " ")
            appointed = o.get("appointed_on") or ""
            out.append(f"{o.get('name','')} ({role}{', ' + appointed if appointed else ''})")
        return out


def _row_from_advanced(item: dict) -> CompanyRow:
    addr = item.get("registered_office_address") or {}
    sics = item.get("sic_codes") or []
    return CompanyRow(
        name=item.get("company_name") or "",
        number=item.get("company_number") or "",
        status=item.get("company_status") or "",
        company_type=item.get("company_type") or "",
        incorporated=item.get("date_of_creation") or "",
        dissolved=item.get("date_of_cessation") or "",
        address=", ".join(str(v) for v in [
            addr.get("address_line_1"), addr.get("address_line_2"),
            addr.get("locality"), addr.get("postal_code"),
            addr.get("country")] if v),
        sic_codes=", ".join(sics),
        sic_sectors="; ".join(sorted({s for s in (sic_sector(c) for c in sics) if s})),
        url=f"https://find-and-update.company-information.service.gov.uk/company/"
            f"{item.get('company_number','')}",
    )


def _row_from_standard(item: dict) -> CompanyRow:
    addr = item.get("address") or {}
    return CompanyRow(
        # `title`, not `company_name` — reading the wrong key yields blanks
        # with no error, which is how a populated register reads as empty.
        name=item.get("title") or "",
        number=item.get("company_number") or "",
        status=item.get("company_status") or "",
        company_type=item.get("company_type") or "",
        incorporated=item.get("date_of_creation") or "",
        dissolved=item.get("date_of_cessation") or "",
        address=item.get("address_snippet") or ", ".join(
            str(v) for v in [addr.get("address_line_1"), addr.get("locality"),
                             addr.get("postal_code")] if v),
        url=f"https://find-and-update.company-information.service.gov.uk/company/"
            f"{item.get('company_number','')}",
    )


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _score(rows: list, crit_set) -> tuple:
    """Score company names against the declared criteria.

    Deliberately scored on name similarity and liveness ONLY. A company is
    not a trademark: it has no Nice classes and no mark type, so those
    components are left empty rather than approximated. The effect is that a
    company scores below a registered mark of equal name similarity, which is
    correct — a company registration is weaker evidence of trading in a field
    than a trademark in that class.

    An active company maps to Registered and a dissolved one to Ended, so
    dissolved companies land in `Result (not live)`: reported, counted as
    checked, carrying no band.
    """
    from tmh_scoring import score_word_result

    # Class-filtered criteria are excluded. The stem criterion exists to scope
    # a broad trademark search to the client's Nice classes; a company has no
    # Nice classes, so on this layer it degenerates into "contains a common
    # word". Left in, every active company containing 'Portal' scored 6 and
    # banded Medium — 330 of them on the Blue Portal audit, which is inflation,
    # not evidence. Companies are therefore assessed against the mark itself,
    # its one-word form and the tagline.
    assessable = [c for c in crit_set.criteria if not c.class_filtered]
    if not assessable:
        assessable = crit_set.criteria
    word_searches = [{"type": c.match_type, "phrase": c.phrase} for c in assessable]
    payload = {"word_searches": word_searches, "client_classes": []}

    # Legal forms are stripped before comparison: 'PRACTIVE LIMITED' should be
    # judged on PRACTIVE. Every UK company carries one, so leaving them in
    # depresses similarity uniformly and arbitrarily.
    kept, noise = [], 0
    for r in rows:
        live = (r.status or "").lower() in {"active", "open", "registered"}
        bare = _LEGAL_FORMS.sub(" ", r.name or "")
        bare = re.sub(r"\s+", " ", bare).strip() or (r.name or "")
        out = score_word_result(
            {"status": "Registered" if live else "Ended",
             "mark_text": bare, "mark_type": "", "classes": ""},
            payload)
        if (out.get("components") or {}).get("similarity", 0) <= 0:
            noise += 1
            continue
        r.score, r.band = out["score"], out["risk_band"]
        r.explanation = out.get("explanation", "")
        kept.append(r)
    return kept, noise


def _apply_exclusions(rows: list, exclusions) -> int:
    """Flag the client's own company. Never removes a row."""
    numbers, names = set(), set()
    for e in exclusions or []:
        if isinstance(e, dict):
            if str(e.get("exclusion_type") or e.get("type") or "").lower() not in (
                    "", "company", "company_number", "trademark"):
                continue
            val = str(e.get("value") or "").strip()
            ref = str(e.get("source_record_id") or "").strip()
        else:
            val, ref = str(e).strip(), ""
        if ref:
            numbers.add(ref.upper().replace(" ", ""))
        if val:
            names.add(_normalise(val))
            if val.replace(" ", "").isdigit():
                numbers.add(val.upper().replace(" ", ""))
    hit = 0
    for r in rows:
        if (r.number.upper() in numbers) or (r.name and _normalise(r.name) in names):
            r.exclusion_status = "excluded_initial_asset"
            hit += 1
    return hit


def _normalise(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", _LEGAL_FORMS.sub(" ", (name or "").upper()))


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

_BAND_ORDER = {"High": 0, "Medium/High": 1, "Medium": 2, "Low/Medium": 3,
               "Low": 4, "Result (not live)": 5}


def _discovery_terms(crit_set) -> list[tuple]:
    """Substring forms to SEARCH with — not the criteria we assess against.

    `company_name_includes` is a true substring search, so it honours forms
    the trademark scorer cannot. Searching `Practiva` returns nothing;
    searching `Practiv` returns PRACTIV (UK) LIMITED and PRACTIV APPLIED
    SOFTWARE LTD, which are exactly the conflicts an audit exists to find.

    This is NOT the Practiva defect in reverse. That defect was searching
    broadly and DECLARING narrowly, so results were binned at import against
    criteria that never ran. Here the declared criteria remain the sole basis
    for assessment: discovery casts wider, scoring drops anything that
    matches no declared criterion, and every reported row therefore still
    satisfies a criterion on the Order Form. It is the same arrangement as
    Temmy's generous SQL blocking with tmh_scoring judging.
    """
    terms: list[tuple] = []
    seen: set[str] = set()

    def add(t: str, why: str):
        t = (t or "").strip()
        if len(t) >= 4 and t.upper() not in seen:
            seen.add(t.upper())
            terms.append((t, why))

    for c in crit_set.criteria:
        # A tagline is not a company name, and the class-scoped stem is a
        # trademark device that on this register just means "contains a
        # common word" (it produced 240 wasted calls and nothing kept on
        # Blue Portal). Only the mark, its one-word form and staff additions
        # are searched here.
        if c.origin in ("tagline", "stem"):
            continue
        add(c.phrase, f"{c.match_type}: {c.phrase}")
        toks = [t for t in re.split(r"[^A-Za-z0-9]+", c.phrase) if t]
        if len(toks) == 1 and len(toks[0]) >= 6:
            # Trim the tail so PRACTIVA also reaches PRACTIV / PRACTIVE.
            add(toks[0][:-1], f"stem of '{c.phrase}' (substring discovery)")
        elif len(toks) > 1:
            add("".join(toks), f"one-word form of '{c.phrase}'")
    return terms


def search_companies(crit_set, exclusions=None, client=None,
                     enrich: bool = True, log=None) -> tuple:
    """Run the company-name layer. Returns (rows, notes)."""
    client = client or CompaniesHouseClient()
    notes: list = []
    merged: dict = {}

    def collect(items, mapper, label):
        added = 0
        for it in items:
            row = mapper(it)
            if not (row.number or row.name):
                continue
            key = row.dedupe_key
            if key in merged:
                if label not in merged[key].found_by:
                    merged[key].found_by.append(label)
            else:
                row.found_by.append(label)
                merged[key] = row
                added += 1
        return added

    for term, why in _discovery_terms(crit_set):
        n_adv = collect(client.advanced(term), _row_from_advanced, why)
        time.sleep(RATE_LIMIT_SEC)
        n_std = collect(client.standard(term), _row_from_standard, why)
        time.sleep(RATE_LIMIT_SEC)
        notes.append(f"Companies House '{term}': {n_adv} by name-includes, "
                     f"{n_std} by ranked search")

    rows = list(merged.values())
    rows, noise = _score(rows, crit_set)
    if noise:
        notes.append(f"{noise} company result(s) discarded — returned by the "
                     "register's fuzzy search but matching no declared criterion")

    excluded = _apply_exclusions(rows, exclusions)
    if excluded:
        notes.append(f"{excluded} company row(s) flagged as the client's own")

    # Profile enrichment: previous names, jurisdiction, officers. Gated on the
    # row scoring something, to stay inside the rate limit.
    if enrich:
        done = 0
        for r in sorted(rows, key=lambda x: -x.score):
            if r.score < ENRICH_MIN_SCORE or r.exclusion_status != "not_excluded":
                continue
            try:
                prof = client.profile(r.number)
                time.sleep(RATE_LIMIT_SEC)
            except CompaniesHouseError:
                continue
            if not prof:
                continue
            prev = prof.get("previous_company_names") or []
            r.previous_names = "; ".join(
                f"{p.get('name','')} (to {p.get('ceased_on','')})" for p in prev)
            r.jurisdiction = prof.get("jurisdiction") or ""
            if not r.sic_codes:
                sics = prof.get("sic_codes") or []
                r.sic_codes = ", ".join(sics)
                r.sic_sectors = "; ".join(sorted({s for s in (sic_sector(c) for c in sics) if s}))
            try:
                r.officers = "; ".join(client.officers(r.number))
                time.sleep(RATE_LIMIT_SEC)
            except CompaniesHouseError:
                pass
            r.enriched = True
            done += 1
            if log:
                log(f"  enriched {r.name}")
        if done:
            notes.append(f"{done} company profile(s) enriched with previous names, "
                         "jurisdiction and officers")
        renamed = sum(1 for r in rows if r.previous_names)
        if renamed:
            notes.append(f"{renamed} of those have had a previous name — a company "
                         "recently renamed close to the client's mark is a strong signal")

    notes.append("note: Companies House cannot be searched BY previous name, so a "
                 "company that has since renamed away from the mark will not appear")

    rows.sort(key=lambda r: (_BAND_ORDER.get(r.band, 9), -r.score, r.name.upper()))
    return rows, notes
