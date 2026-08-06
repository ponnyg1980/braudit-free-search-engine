"""The band vocabulary and the corpus definition — one source of truth.

WHAT WE ARE SAYING (Jonathan, 15 Jul):

    "In your business type: All use this, Most use this, Some use this,
     a few have this."

That is DESCRIPTIVE, not prescriptive. We report what the register actually
shows for businesses like theirs; we do not tell them what to file. That's
more honest, it's what the data can actually support, and it keeps us clear of
handing out regulated advice in a free tool.

THE CORPUS

    UK registered trade marks,
    filed in the last 3 years,
    by Company or Organisation applicants.

  * Registered only — a refused or withdrawn mark isn't precedent.
  * Last 3 years — current filing practice, not 1990s habits.
  * Organisations only — TMH's clients are businesses, and it removes the
    individual bulk-filer noise (one applicant filing a dozen marks with
    identical boilerplate specs) without needing any dedupe heuristics.

  ~362,000 filings qualify; ~202,000 of those carry a UK company number, which
  is what lets us confirm the SIC ↔ business type mapping.

Economies of scale do the rest: at this volume the occasional over-broad filing
moves a percentage point, it doesn't change "All use this".
"""
from __future__ import annotations

# Tier keys are a/b/c/d, strongest → weakest. Only the strings change if the
# wording is revisited; nothing else in the codebase reasons about the words.
TIER_LABELS: dict[str, str] = {
    'a': 'All use this',
    'b': 'Most use this',
    'c': 'Some use this',
    'd': 'A few have this',
}

# Share of the business type's filings that use the class (or the term).
# "Most" must mean a majority — anything else makes the sentence untrue.
THRESHOLDS: list[tuple[float, str]] = [
    (0.75, 'a'),   # All        — 3 in 4 or better
    (0.50, 'b'),   # Most       — a literal majority
    (0.15, 'c'),   # Some       — a real minority, not noise
    (0.0, 'd'),    # A few
]

TIER_RANK = {'a': 3, 'b': 2, 'c': 1, 'd': 0}

# Terms spread more thinly than classes, so they band on their own scale.
TERM_THRESHOLDS: list[tuple[float, str]] = [
    (0.30, 'a'), (0.12, 'b'), (0.04, 'c'), (0.0, 'd'),
]

# Doc 1's G&S Classes module carries a 2-value frequency field (Always /
# Sometimes) — kept for Alex's Template seeding.
FREQ_ALWAYS_AT = 'b'          # tier a or b -> "Always"
DROP_BELOW = 0.05             # below this share, not worth showing at all

# --- corpus SQL fragments (shared by every seeding query) -------------------
CORPUS_YEARS = 3
CORPUS_WHERE = (
    "t.status = 'Registered' "
    "AND t.application_date_time >= (CURRENT_DATE - INTERVAL '3 years') "
    "AND a.kind = 'Company or Organisation'"
)


def tier_for(share: float, *, terms: bool = False) -> str:
    for thr, key in (TERM_THRESHOLDS if terms else THRESHOLDS):
        if share >= thr:
            return key
    return 'd'


def label_for(share: float, *, terms: bool = False) -> str:
    return TIER_LABELS[tier_for(share, terms=terms)]


def frequency_for(tier: str) -> str:
    """Doc 1 Template field: Always / Sometimes."""
    return 'Always' if TIER_RANK[tier] >= TIER_RANK[FREQ_ALWAYS_AT] else 'Sometimes'
