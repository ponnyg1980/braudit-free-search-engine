"""Words the engines refuse to search or compare on — in one place.

THREE KINDS, and they are not interchangeable (Search & Score Settings spec):

  structural  A company's legal form — ltd, plc, GmbH. Never part of the mark
              itself. Stripped before two names are compared so that "Sunrise
              Bakery Limited" and "Sunrise Bakery Ltd" are recognised as one
              business. See below: this is jurisdiction-specific.
  weak        group, services, global, direct — usually noise, occasionally
              the distinctive part of the mark (DIRECT LINE). These are the
              ones worth offering staff the chance to put back.
  industry    courier, bakery — noise for one client and the whole point for
              another. Not represented here at all; they belong to a run or a
              client, never to a global list.

WHERE THIS RUNS (Jonathan, 19 Sep 2026). Legal-form stripping is a REFINE
step, not a scoring step. Normalise once, keep both forms on the row, score
the normalised one and show the client the original. Until 2.2.0 it happened
inside the comparison itself, so the normalised name existed for the instant
the scorer needed it and was never seen again — which is exactly how two
different lists ran side by side for weeks without anyone noticing.

WHY THE LISTS ARE PER JURISDICTION (finding G, ruled 19 Sep 2026)
-----------------------------------------------------------------
2.1.0 recorded that the audit engine and Watch had drifted into two different
legal-form patterns, disagreeing on 27 of 1,183 real names. Neither was right:

  the audit pattern stripped UK, GROUP and HOLDINGS, eating words that carry
  meaning in British names — "UK & FRIED CHICKEN" became "& FRIED CHICKEN"

  the Watch pattern stripped a bare AS and SA, Norwegian and French legal
  forms that are also ordinary English words — "SHOP AS YOU GO LIMITED"
  became "SHOP YOU GO"

Both failures have the same cause: a legal form was applied outside the
country whose law defines it. AS is a legal form in Norway and a preposition
in England, so whether to strip it is not a property of the word, it is a
property of the RECORD. Hence: one generic list of forms that mean the same
thing anywhere, plus a list per jurisdiction applied only when the record is
from that jurisdiction. A British company never meets the Norwegian list.

The per-jurisdiction lists deliberately START SHORT. They grow when a real
record shows a form that should have been stripped and was not — which is a
fact, and can be added — rather than from a guess about what a country might
use, which is an assumption that quietly changes scores. Adding to a list is
a versioned release like any other scoring change.
"""
from __future__ import annotations

import re
from functools import lru_cache

#: Tokens too common to search alone — a stem search on these returns noise.
#: Applied at SEARCH (which queries go out), unlike everything below, which is
#: applied at REFINE (how two names are compared).
WEAK_TOKENS: frozenset = frozenset({
    "the", "and", "for", "ltd", "limited", "plc", "llp", "uk", "group",
    "holdings", "company", "co", "services", "solutions", "international",
    "global", "systems", "technologies", "digital", "online", "direct",
})

#: Legal forms that mean the same thing in every jurisdiction we search and
#: are not ordinary words in any of their languages. Applied to every record.
LEGAL_FORMS_GENERIC: tuple = (
    "LIMITED", "LTD", "PLC", "LLP", "LLC", "L.L.C", "L.L.C.",
    "INCORPORATED", "INC", "CORPORATION", "CORP", "COMPANY", "CO",
)

#: Applied ONLY when the record's own jurisdiction matches. Short by design —
#: see the module docstring. Add from evidence, not from expectation.
LEGAL_FORMS_BY_JURISDICTION: dict = {
    "GB": ("CIC", "CIO", "LP", "UNLIMITED", "LBG"),
    "IE": ("TEO", "CPT", "DAC", "CLG"),
    "US": ("PC", "PLLC", "LP"),
    "DE": ("GMBH", "MBH", "AG", "KG", "KGAA", "UG", "OHG", "GBR", "EG"),
    "AT": ("GMBH", "AG", "KG", "OG"),
    "CH": ("GMBH", "AG", "SARL", "SA"),
    "FR": ("SARL", "SAS", "SASU", "SA", "EURL", "SCI", "SNC", "SCS"),
    "NL": ("BV", "NV", "VOF", "CV"),
    "BE": ("BVBA", "SPRL", "NV", "SA", "CVBA"),
    "ES": ("SL", "SLU", "SA", "SAU", "SCP"),
    "PT": ("LDA", "SA", "UNIPESSOAL"),
    "IT": ("SRL", "SPA", "SNC", "SAS", "SAPA"),
    "NO": ("AS", "ASA", "ANS", "DA"),
    "DK": ("APS", "A/S", "IVS"),
    "SE": ("AB", "HB", "KB"),
    "FI": ("OY", "OYJ", "KY"),
    "PL": ("SP. Z O.O.", "SP Z OO", "SPZOO", "SA"),
    "AU": ("PTY",),
    "NZ": ("NZBN",),
    "JP": ("KK", "KABUSHIKI KAISHA"),
}

#: NOT legal forms. Descriptive words that frequently ARE the brand, which is
#: why the audit engine eating them was the defect in finding G. Kept as their
#: own list, off unless a caller asks, so that turning them on is a decision
#: someone made rather than something that happened.
DESCRIPTIVE_SUFFIXES: tuple = (
    "HOLDINGS", "HOLDING", "GROUP", "INTERNATIONAL", "GLOBAL", "WORLDWIDE", "UK",
)

#: What the sources actually say, mapped to the keys above. Companies House
#: returns "england-wales" and friends; registers return ISO codes.
_JURISDICTION_ALIASES: dict = {
    "ENGLAND-WALES": "GB", "ENGLAND": "GB", "WALES": "GB", "SCOTLAND": "GB",
    "NORTHERN-IRELAND": "GB", "UNITED-KINGDOM": "GB", "UNITED KINGDOM": "GB",
    "UK": "GB", "GBR": "GB", "EW": "GB",
    "IRELAND": "IE", "IRL": "IE",
    "UNITED STATES": "US", "USA": "US",
    "GERMANY": "DE", "DEU": "DE", "AUSTRIA": "AT", "SWITZERLAND": "CH",
    "FRANCE": "FR", "FRA": "FR", "NETHERLANDS": "NL", "NLD": "NL",
    "BELGIUM": "BE", "SPAIN": "ES", "ESP": "ES", "PORTUGAL": "PT",
    "ITALY": "IT", "ITA": "IT", "NORWAY": "NO", "DENMARK": "DK",
    "SWEDEN": "SE", "SWE": "SE", "FINLAND": "FI", "POLAND": "PL",
    "AUSTRALIA": "AU", "NEW ZEALAND": "NZ", "JAPAN": "JP",
    # EM is the EU trade mark register, not a country: no national legal forms.
    "EM": "", "EU": "", "WO": "",
}


def jurisdiction_key(value) -> str:
    """'england-wales' -> 'GB'; 'GB' -> 'GB'; unknown or blank -> ''.

    An empty key means "generic list only", which is the safe answer: a form
    we cannot place is a form we should not strip.
    """
    v = str(value or "").strip().upper()
    if not v:
        return ""
    v = _JURISDICTION_ALIASES.get(v, v)
    return v if v in LEGAL_FORMS_BY_JURISDICTION else ""


@lru_cache(maxsize=256)
def legal_form_pattern(jurisdiction: str = "", descriptive: bool = False) -> re.Pattern:
    """The pattern to strip from a name from `jurisdiction`.

    Longest form first so that LIMITED is not left as a stray "ED" by LTD, and
    word-bounded at both ends so a form inside a word survives (INCORPORATED
    must not eat the INC in INCREDIBLE).
    """
    forms = list(LEGAL_FORMS_GENERIC)
    forms += list(LEGAL_FORMS_BY_JURISDICTION.get(jurisdiction_key(jurisdiction), ()))
    if descriptive:
        forms += list(DESCRIPTIVE_SUFFIXES)
    forms = sorted(set(forms), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(f) for f in forms) + r")\b\.?", re.I)


def normalise_company_name(name: str, jurisdiction: str = "",
                           descriptive: bool = False) -> tuple:
    """(compared_as, stripped) for one name.

    BOTH halves matter. `compared_as` is what the scorer should see; `stripped`
    is what was taken out, and it exists so the Triage panel can show
    "compared as: Health Radio" instead of a client wondering why their mark
    matched something that does not look like it. A caller that keeps only the
    first half has recreated the defect this module was written to fix.
    """
    raw = str(name or "")
    pattern = legal_form_pattern(jurisdiction_key(jurisdiction), descriptive)
    stripped = [m.group(1) for m in pattern.finditer(raw)]
    bare = re.sub(r"\s+", " ", pattern.sub(" ", raw)).strip(" .,-")
    return (bare or raw), stripped


def normalise_key(name: str, jurisdiction: str = "") -> str:
    """The comparison key: legal forms out, then everything but A-Z0-9 out."""
    bare, _ = normalise_company_name(name, jurisdiction)
    return re.sub(r"[^A-Z0-9]+", "", bare.upper())


def is_weak(token: str) -> bool:
    """Is this token too common to carry a search on its own?"""
    return str(token or "").strip().lower() in WEAK_TOKENS


# ---------------------------------------------------------------------------
# Superseded by the per-jurisdiction lists in 2.2.0. Kept for one release so a
# caller that has not been migrated still imports, and so the ruling on
# finding G can be read against what it replaced. Do not use in new code.
# ---------------------------------------------------------------------------
LEGAL_FORMS_AUDIT = re.compile(
    r"\b(LIMITED|LTD|PLC|LLP|LP|L\.?L\.?C|COMPANY|CO|HOLDINGS|GROUP|"
    r"INCORPORATED|INC|UK|\(UK\))\b\.?", re.I)
LEGAL_FORMS_WATCH = re.compile(
    r"\b(LIMITED|LTD|PLC|LLP|LP|INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|"
    r"GMBH|SARL|BV|NV|AB|AS|SA|SL|SPA|SRL|PTY|CIC|LLC)\b\.?", re.I)
