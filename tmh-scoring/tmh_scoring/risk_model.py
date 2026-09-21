"""Five-band risk model for TMH audit and monitoring reports.

    Low · Low/Medium · Medium · Medium/High · High

The half-bands are not padding. They exist for ambiguity - specifically for
when the signals disagree. A near-identical mark in a completely different
trade, or an unrelated mark sitting squarely on the client's goods, are both
genuinely uncertain, and forcing either into Medium or High loses the fact
that a human should look at it.

Four inputs:

1. MARK SIMILARITY - how alike are the marks. Sharing a distinctive word
   counts; sharing "services", "consultants" or "couriers" does not.
2. CLASS OVERLAP - and whether it is one class or several. Several is
   materially worse and the old model treated them the same.
3. GOODS OVERLAP - whether the specifications share one term or many, from
   goods_similarity.
4. TRADING EVIDENCE - from Companies House, where the proprietor is a
   company. A dissolved or dormant proprietor is a weaker threat than one
   actively trading in the client's field.

Two rules that follow TMH practice rather than falling out of arithmetic:

* A registered mark is a threat REGARDLESS OF AGE. Age is not allowed to
  reduce risk here. What age affects is how hard the mark is to remove, and
  that belongs in enforceability.py, not in this score.
* A mark that is no longer live is a RESULT, NOT A RISK. It appears on the
  report, it counts toward "checked and cleared", and it carries no band.
"""
from __future__ import annotations

from dataclasses import dataclass, field

LOW = "Low"
LOW_MEDIUM = "Low/Medium"
MEDIUM = "Medium"
MEDIUM_HIGH = "Medium/High"
HIGH = "High"
RESULT_ONLY = "Result (not live)"

BANDS = [LOW, LOW_MEDIUM, MEDIUM, MEDIUM_HIGH, HIGH]

DEAD_STATUSES = {"dead", "expired", "removed", "cancelled", "surrendered",
                 "withdrawn", "refused", "ended", "lapsed", "abandoned"}

# Words that carry no distinguishing weight when shared between two marks.
# Two couriers both called "... Couriers" have not thereby collided.
GENERIC_MARK_WORDS = {
    "services", "service", "solutions", "consultants", "consultant",
    "consulting", "consultancy", "couriers", "courier", "accountants",
    "accountant", "accounting", "group", "holdings", "company", "limited",
    "ltd", "plc", "llp", "international", "global", "national", "uk",
    "the", "and", "of", "for", "co", "associates", "partners", "partnership",
    "agency", "agencies", "systems", "technologies", "technology", "media",
    "marketing", "management", "trading", "enterprises", "products",
    "supplies", "direct", "online", "centre", "center", "studio", "works",
}


@dataclass
class TradingEvidence:
    """What Companies House tells us about the cited proprietor.

    Only meaningful where the proprietor is a company. Individuals and
    overseas proprietors return `has_company=False` and the factor is simply
    not applied rather than counted against them.
    """
    has_company: bool = False
    company_status: str = ""          # active | dissolved | liquidation | dormant
    same_field: bool | None = None    # SIC codes overlap the client's trade
    incorporation_date: str | None = None
    company_number: str | None = None

    @property
    def tier(self) -> int:
        """-1 (weaker threat) .. +2 (stronger threat). 0 when unknown."""
        if not self.has_company:
            return 0
        status = (self.company_status or "").lower()
        if any(k in status for k in ("dissolved", "liquidation", "struck", "closed")):
            return -1
        if "dormant" in status:
            return -1
        if "active" in status:
            return 2 if self.same_field else 1
        return 0


def _mark_words(text: str) -> set:
    return {w.strip(".,()-'").lower() for w in (text or "").split()
            if len(w.strip(".,()-'")) > 2}


def shared_distinctive_words(mark_a: str, mark_b: str) -> list:
    """Words shared by two marks, excluding generic trade vocabulary."""
    shared = _mark_words(mark_a) & _mark_words(mark_b)
    return sorted(w for w in shared if w not in GENERIC_MARK_WORDS)


def strip_generic(text: str) -> str:
    """Remove generic trade vocabulary, leaving the distinctive element."""
    return " ".join(w for w in (text or "").split()
                    if w.strip(".,()-'").lower() not in GENERIC_MARK_WORDS)


def mark_similarity_tier(similarity_points: int, shared_words: list,
                         client_mark: str = "", cited_mark: str = "",
                         phonetic: bool = False,
                         settings=None) -> tuple[int, str]:
    """0-4, from tmh_scoring's similarity component plus distinctive words.

    `similarity_points` is the similarity element of the word score:
    4 exact, 2 starts-with / contains / strong fuzzy, 1 weak fuzzy.

    Guard against spurious resemblance. A raw fuzzy comparison rewards a
    shared generic suffix - "SWIFT COURIERS" against "RAPID COURIERS" scores
    well purely because both end in COURIERS, and two couriers sharing the
    word "couriers" have not collided. So similarity is re-checked on the
    marks with generic vocabulary stripped, and where the distinctive
    elements do not resemble each other the tier is capped.
    """
    from difflib import SequenceMatcher
    from .settings import resolve

    s = resolve(settings)

    if similarity_points >= 4:
        return 4, "effectively identical"

    core_a, core_b = strip_generic(client_mark).upper(), strip_generic(cited_mark).upper()
    core_ratio = SequenceMatcher(None, core_a, core_b).ratio() if core_a and core_b else 0.0

    # The guard only applies where generic vocabulary was actually present to
    # inflate the similarity. If neither mark contains any, the measured
    # resemblance is real and must not be capped - MOMENTUS against MOMENTUM
    # MORTGAGE has no generic words at all, and its stripped ratio of 0.56 is
    # low only because of the extra word, not because of shared boilerplate.
    stripped_something = (core_a != (client_mark or "").upper()
                          or core_b != (cited_mark or "").upper())

    # No distinctive word in common, generic wording was present, and the
    # distinctive elements do not resemble each other - so whatever
    # similarity was measured came from that shared generic vocabulary.
    if (stripped_something and not shared_words
            and core_ratio < s.generic_guard_ratio and (core_a or core_b)):
        if similarity_points >= 2:
            return 1, ("resemblance is only in generic trade wording "
                       f"({core_a or '-'} vs {core_b or '-'} differ)")
        return 0, "no meaningful resemblance"

    if shared_words and similarity_points >= 2:
        return 4, f"shares distinctive word(s): {', '.join(shared_words[:3])}"
    if similarity_points >= 2:
        return 3, "closely resembles" + (" phonetically" if phonetic else "")
    if shared_words:
        return 2, f"shares distinctive word(s): {', '.join(shared_words[:3])}"
    if similarity_points >= 1:
        return 1, "loosely resembles"
    return 0, "no meaningful resemblance"


def overlap_tier(shared_classes: list, goods_band: str,
                 shared_goods_terms: list, class_link: str = "") -> tuple[int, str]:
    """0-4 for how much the trades actually overlap."""
    n_classes = len(shared_classes or [])
    n_terms = len(shared_goods_terms or [])

    if goods_band in ("identical", "high") and n_classes >= 2:
        return 4, f"same trade across {n_classes} shared classes"
    if goods_band in ("identical", "high"):
        return 3, "same trade" + (f" in class {shared_classes[0]}" if n_classes == 1 else "")
    if n_classes >= 2 and n_terms >= 2:
        return 4, f"{n_classes} shared classes and overlapping goods"
    if goods_band == "related" and n_classes >= 1:
        return 3, f"related goods in shared class{'es' if n_classes > 1 else ''}"
    if n_classes >= 2:
        return 2, f"{n_classes} shared classes, limited goods overlap"
    if goods_band == "related":
        return 2, f"related trade{f' ({class_link})' if class_link else ''}"
    if n_classes == 1 or n_terms >= 1:
        return 1, "single class or single shared term"
    return 0, "no trade overlap"


@dataclass
class RiskAssessment:
    band: str
    mark_tier: int = 0
    overlap_tier: int = 0
    trading_tier: int = 0
    ambiguous: bool = False
    reasons: list = field(default_factory=list)
    explanation: str = ""

    @property
    def is_risk(self) -> bool:
        return self.band != RESULT_ONLY

    def as_dict(self):
        return {"band": self.band, "is_risk": self.is_risk,
                "mark_tier": self.mark_tier, "overlap_tier": self.overlap_tier,
                "trading_tier": self.trading_tier, "ambiguous": self.ambiguous,
                "reasons": self.reasons, "explanation": self.explanation}


# Band from the two primary tiers. Rows are mark similarity 0-4, columns are
# trade overlap 0-4. Deliberately asymmetric: a near-identical mark with no
# trade overlap still warrants a look, whereas a weak mark on identical goods
# is a much smaller concern.
_MATRIX = [
    #  overlap: 0            1            2            3             4
    [LOW,        LOW,         LOW,         LOW,          LOW_MEDIUM],   # mark 0
    [LOW,        LOW,         LOW_MEDIUM,  LOW_MEDIUM,   MEDIUM],       # mark 1
    [LOW,        LOW_MEDIUM,  MEDIUM,      MEDIUM,       MEDIUM_HIGH],  # mark 2
    [LOW_MEDIUM, MEDIUM,      MEDIUM,      MEDIUM_HIGH,  HIGH],         # mark 3
    [MEDIUM,     MEDIUM,      MEDIUM_HIGH, HIGH,         HIGH],         # mark 4
]


def _shift(band: str, steps: int) -> str:
    i = BANDS.index(band)
    return BANDS[max(0, min(len(BANDS) - 1, i + steps))]


def assess_risk(status: str, similarity_points: int, client_mark: str,
                cited_mark: str, shared_classes=None, goods_band: str = "unrelated",
                shared_goods_terms=None, class_link: str = "",
                trading: TradingEvidence | None = None,
                phonetic: bool = False, settings=None) -> RiskAssessment:
    """Produce a five-band risk assessment for one cited mark.

    `settings` (2.3.0) is an optional ScoringSettings. Omitted, the shipped
    calibration applies and the result is identical to every prior version.
    """
    from .settings import resolve

    s = resolve(settings)

    # A mark that is no longer live is reported, but it is not a risk.
    if (status or "").strip().lower() in DEAD_STATUSES:
        return RiskAssessment(
            RESULT_ONLY,
            reasons=[f"status '{status}' - not live"],
            explanation=(f"shown as a result; a mark with status '{status}' cannot "
                         f"found an objection and carries no risk band"),
        )

    shared_words = shared_distinctive_words(client_mark, cited_mark)
    m_tier, m_why = mark_similarity_tier(similarity_points, shared_words,
                                         client_mark, cited_mark, phonetic, s)
    o_tier, o_why = overlap_tier(shared_classes, goods_band, shared_goods_terms, class_link)

    band = _MATRIX[m_tier][o_tier]
    reasons = [m_why, o_why]

    # Trading evidence is the tie-breaker. Two results can sit at the same
    # band on mark and trade alone - an identical mark in an unrelated trade,
    # and a weak mark on identical goods, are both Medium - and what
    # separates them is whether the proprietor is demonstrably trading.
    # Evidence of active trading moves the band up one step; same-field
    # trading is recorded in the reasons but does not move it further,
    # because the trade overlap is already counted on its own axis.
    t_tier = trading.tier if trading else 0
    if t_tier > 0:
        band = _shift(band, 1)
        reasons.append("proprietor actively trading"
                       + (" in the same field" if t_tier == 2 else ""))
    elif t_tier < 0:
        band = _shift(band, -1)
        reasons.append(f"proprietor {trading.company_status.lower()} - weaker threat")
    elif trading and not trading.has_company:
        reasons.append("proprietor is not a UK company - no trading evidence available")

    # Ambiguity: the signals disagree. Pull a confident band back to its
    # neighbouring half-band so a human reviews it rather than a report
    # asserting something the evidence does not support on both axes.
    ambiguous = abs(m_tier - o_tier) >= 3
    if ambiguous and band in (HIGH, LOW):
        band = _shift(band, -1 if band == HIGH else 1)
        reasons.append("signals disagree - flagged for review")

    return RiskAssessment(band, m_tier, o_tier, t_tier, ambiguous, reasons,
                          "; ".join(reasons))
