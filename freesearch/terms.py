"""Clearance Audit Terms of Service: the ONE renderer.

Handover `_Hub/handoffs/TERMS_OF_SERVICE_HANDOVER_SEARCH_AUDIT_2026-09-28.md`.
The terms are fixed text except two clauses (marked [[ ]] in the docx):

  V1  clause 4.2    "within [[5]] working days"          -> turnaround N (1..5)
  V2  clause 2.3(b) "[[and the national registers of the countries named on
                     your order]]"                          -> Current + Planning

Everything that shows, stores or links the terms goes through `render()`, so
the copy the client ticked is byte-for-byte the copy we keep (hashed).

Also home to the England & Wales working-day helper used for
`Deals.Audit_Deadline` (approval + N working days). The same holiday list is
mirrored in the journey (TS) and the checkout Deluge; `holidays_digest()`
lets a test prove the copies match.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import json
import os

DOC_NAME = "Clearance Audit ToS"
DOC_VERSION = "v1.0"
DOC_LABEL = f"{DOC_NAME} {DOC_VERSION}"          # -> Deals.Terms_Accepted
GENERAL_TERMS_URL = "https://www.thetrademarkhelpline.com/terms-and-conditions/"
DEFAULT_TURNAROUND = 5
MAX_TURNAROUND = 5    # longer than our terms promise needs Jonathan, never the form

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE = os.path.join(_HERE, "data", "terms", "clearance_audit_v1.0.html")
_NAMES = os.path.join(_HERE, "data", "terms", "jurisdiction_names.json")

# Clause 2.3 already names these, so they never appear in the country list.
# 'UK' and 'EU' are the ungoverned spellings some Deals hold (see CLAUDE.md,
# Trademark_Jurisdictions holds two vocabularies).
COVERED = {"GB", "UK", "EM", "EU", "WO"}

# England & Wales bank holidays, from https://www.gov.uk/bank-holidays.json
# (fetched 28 Sep 2026). Extend each year; `next_holiday_gap()` warns when the
# list is about to run out.
EW_BANK_HOLIDAYS = frozenset(_dt.date.fromisoformat(d) for d in (
    "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25",
    "2026-08-31", "2026-12-25", "2026-12-28",
    "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31",
    "2027-08-30", "2027-12-27", "2027-12-28",
    "2028-01-03", "2028-04-14", "2028-04-17", "2028-05-01", "2028-05-29",
    "2028-08-28", "2028-12-25", "2028-12-26",
))
HOLIDAYS_LAST_YEAR = 2028


class TermsError(ValueError):
    """An order whose terms cannot be rendered honestly (bad N, no countries)."""


# ---------------------------------------------------------------- V1
def turnaround(value) -> int:
    """Validate the agreed turnaround. Blank -> 5. Only 1..5 whole days."""
    if value in (None, "", 0, "0"):
        return DEFAULT_TURNAROUND
    s = str(value).strip()
    if not s.isdigit():
        raise TermsError("Turnaround must be a whole number of working days.")
    n = int(s)
    if n < 1:
        raise TermsError("Turnaround must be at least 1 working day.")
    if n > MAX_TURNAROUND:
        raise TermsError(f"Turnaround cannot be longer than {MAX_TURNAROUND} "
                         "working days: that would break our terms. Ask Jonathan.")
    return n


def turnaround_text(n: int) -> str:
    return "1 working day" if n == 1 else f"{n} working days"


# ---------------------------------------------------------------- V2
def _names() -> dict:
    with open(_NAMES, encoding="utf-8") as f:
        return json.load(f)


# Picker code -> Zoho Trademark_Jurisdictions code. Mirrors JUR_TO_ZOHO in
# supabase/functions/journey/index.ts, so the countries named in the terms
# are exactly the codes written to the Deal.
PICKER_TO_ZOHO = {"EU": "EM", "ARIPO": "AP", "OAPI": "OA", "GCC": "GC",
                  "WIPO": "WO", "MADRID": "WO", "UK": "GB"}


def combined_jurisdictions(current, planning) -> list:
    """Current + Planning, one list, duplicates removed, order kept (codes)."""
    out, seen = [], set()
    for c in list(current or []) + list(planning or []):
        c = str(c or "").strip().upper()
        c = PICKER_TO_ZOHO.get(c, c)
        if c and c != "NONE" and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def named_countries(codes) -> list:
    """Full (Zoho display) names, alphabetical, without GB/EU/WIPO."""
    names = _names()
    picked = {names.get(c, c) for c in codes if c not in COVERED}
    return sorted(picked, key=lambda s: s.casefold())


def countries_clause(worldwide: bool, codes) -> str:
    """The text that replaces the [[ ]] in clause 2.3(b), incl. leading space."""
    if not worldwide:
        return " and the national registers of the countries you select"
    if not codes:
        raise TermsError("A Worldwide Clearance Audit needs at least one "
                         "jurisdiction (clause 3.1).")
    names = named_countries(codes)
    if not names:
        return ""   # only UK / EU / WIPO chosen: clause already names them
    return " and the national registers of: " + ", ".join(names)


# ---------------------------------------------------------------- render
def render(*, worldwide: bool, turnaround_days=None, current=None,
           planning=None) -> dict:
    """Render the terms for one order. Raises TermsError on an invalid order."""
    n = turnaround(turnaround_days)
    codes = combined_jurisdictions(current, planning)
    clause = countries_clause(worldwide, codes)
    with open(_TEMPLATE, encoding="utf-8") as f:
        body = f.read()
    body = (body.replace("{{TURNAROUND}}", turnaround_text(n))
                .replace("{{COUNTRIES}}", _html.escape(clause, quote=False)))
    if "{{" in body:
        raise TermsError("Terms template has an unfilled placeholder.")
    # Every mention of the general terms links to them, in a new tab
    # (Jonathan, 28 Sep 2026). Clauses 1.2 and 5.4.
    gt = "general Terms and Conditions of Service"
    if body.count(gt) < 1:
        raise TermsError("Terms template no longer mentions the general terms.")
    body = body.replace(gt, f'<a href="{GENERAL_TERMS_URL}" target="_blank" '
                            f'rel="noopener">{gt}</a>')
    return {
        "document": DOC_LABEL,
        "version": DOC_VERSION,
        "turnaround_days": n,
        "jurisdictions": codes,
        "countries_named": named_countries(codes) if worldwide else [],
        "worldwide": bool(worldwide),
        "html": body,
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


def page(rendered: dict, *, note: str = "") -> str:
    """A standalone, printable page around the rendered body."""
    extra = f'<p class="note">{_html.escape(note)}</p>' if note else ""
    return (
        "<!doctype html><html lang='en-GB'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_html.escape(rendered['document'])}</title>"
        "<meta name='robots' content='noindex'>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
        "Roboto,Helvetica,Arial,sans-serif;color:#1D1D1B;max-width:760px;"
        "margin:32px auto;padding:0 22px;line-height:1.55;font-size:15px}"
        "h2{color:#2D455A;font-size:18px;margin:26px 0 8px}"
        "blockquote{margin:6px 0 6px 18px;padding:0}"
        "p:first-child strong{font-size:24px;color:#2D455A}"
        ".note{background:#F7F8FA;border:1px solid #E6E9ED;border-radius:10px;"
        "padding:10px 14px;font-size:13px;color:#3f4c58}"
        "a{color:#E51652}</style></head><body>"
        f"{extra}{rendered['html']}"
        f"<p class='note'>{_html.escape(rendered['document'])} &middot; "
        f"These terms sit alongside our <a href='{GENERAL_TERMS_URL}' "
        "target='_blank' rel='noopener'>general Terms and Conditions of "
        f"Service</a>. Reference {rendered['sha256'][:12]}.</p>"
        "</body></html>")


# ---------------------------------------------------------------- deadline
def is_working_day(d: _dt.date) -> bool:
    return d.weekday() < 5 and d not in EW_BANK_HOLIDAYS


def add_working_days(start: _dt.date, n: int) -> _dt.date:
    """Approval date + n working days (the approval day itself not counted)."""
    d = start
    left = int(n)
    while left > 0:
        d += _dt.timedelta(days=1)
        if is_working_day(d):
            left -= 1
    return d


def holidays_digest() -> str:
    s = ",".join(sorted(d.isoformat() for d in EW_BANK_HOLIDAYS))
    return hashlib.sha256(s.encode()).hexdigest()[:16]
