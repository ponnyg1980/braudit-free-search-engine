"""Words the engines refuse to search or compare on — in one place at last.

Until 18 Sep 2026 these lists lived outside the scoring package and in more
than one copy, which is the exact drift this package exists to end. They are
gathered here so both products import the same names.

THREE KINDS, and they are not interchangeable (Search & Score Settings spec):

  structural  ltd, plc, llp - never part of a mark. Stripping them is not a
              judgement, and offering staff the chance to put one back would
              only flood the search.
  weak        group, services, global, direct - usually noise, occasionally
              the distinctive part of the mark (DIRECT LINE). These are the
              ones worth offering back.
  industry    courier, bakery - noise for one client and the whole point for
              another. Not represented here at all; they belong to a run or a
              client, never to a global list.

-------------------------------------------------------------------------
THE TWO LEGAL-FORM PATTERNS ARE NOT THE SAME, AND ARE NOT UNIFIED HERE.
-------------------------------------------------------------------------
Moving them revealed that the audit engine and Watch had drifted apart in
both directions:

  only the audit pattern    HOLDINGS, GROUP, UK, (UK), L.L.C
  only the Watch pattern    CORPORATION, CORP, GMBH, SARL, BV, NV, AB, AS,
                            SA, SL, SPA, SRL, PTY, CIC, LLC

Unifying them would change scores on both sides, so it is NOT done in this
release. Watch would begin stripping GROUP and UK, which are common and
load-bearing in British company names; the audit engine would begin stripping
international forms, and would inherit Watch's bare AS and SA, which eat
ordinary English words ("SHOP AS YOU GO LIMITED"). Which list is right is a
scoring decision and needs Jonathan's ruling, per the package rules. Both are
named here so the divergence is visible and documented instead of hidden in
two files, and so a ruling can be applied in one edit.
"""
from __future__ import annotations

import re

#: Tokens too common to search alone — a stem search on these returns noise.
#: Single source; the audit engine's criteria builder was the only copy.
WEAK_TOKENS: frozenset = frozenset({
    "the", "and", "for", "ltd", "limited", "plc", "llp", "uk", "group",
    "holdings", "company", "co", "services", "solutions", "international",
    "global", "systems", "technologies", "digital", "online", "direct",
})

#: Company-name legal forms, as the AUDIT ENGINE has always stripped them.
LEGAL_FORMS_AUDIT = re.compile(
    r"\b(LIMITED|LTD|PLC|LLP|LP|L\.?L\.?C|COMPANY|CO|HOLDINGS|GROUP|"
    r"INCORPORATED|INC|UK|\(UK\))\b\.?", re.I)

#: Company-name legal forms, as TRADEMARK WATCH has always stripped them.
LEGAL_FORMS_WATCH = re.compile(
    r"\b(LIMITED|LTD|PLC|LLP|LP|INCORPORATED|INC|CORPORATION|CORP|COMPANY|CO|"
    r"GMBH|SARL|BV|NV|AB|AS|SA|SL|SPA|SRL|PTY|CIC|LLC)\b\.?", re.I)

#: The forms both agree on — the safe core, for anything written from here on
#: that has no legacy behaviour to preserve. Not used by either product yet.
LEGAL_FORMS_COMMON = re.compile(
    r"\b(LIMITED|LTD|PLC|LLP|LP|INCORPORATED|INC|COMPANY|CO)\b\.?", re.I)


def is_weak(token: str) -> bool:
    """Is this token too common to carry a search on its own?"""
    return str(token or "").strip().lower() in WEAK_TOKENS


def strip_legal_forms(name: str, pattern: re.Pattern = LEGAL_FORMS_COMMON) -> str:
    """Remove legal forms from a company name. The caller passes the pattern
    its product has always used — there is no default that preserves both."""
    return pattern.sub(" ", name or "")
