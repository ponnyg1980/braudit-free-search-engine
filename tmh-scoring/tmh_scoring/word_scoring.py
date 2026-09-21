"""Deterministic word-mark scoring.

No models, no network, no GPU. Pure Python + stdlib. Typical cost is a few
microseconds per result, so this can be called inline in a Celery task over a
full result set without batching.

The scoring model is additive across four independent components:

    status points  +  mark-similarity points  +  mark-type points  +  class overlap

and the integer total is then banded (see `bands.py`). A mark whose status is
no longer live is always Negligible, whatever the total.

Design note on mark similarity
------------------------------
Similarity points are awarded against the ACTUAL search criteria that were
run, one criterion at a time, taking the single best match. This matters:
an earlier version of this scorer only rewarded exact/starts-with matches
against one derived root word, so a 'Similar To' hit that legitimately passed
the search filter scored nothing and landed in Low Risk when it should have
been Medium. The MOMENTUM/MOMENTOUS vs MOMENTUS cases in the test suite are
the regression fixtures for exactly that defect (TMH ref: BR-013).

Any criterion type this module does not explicitly recognise falls through to
a generic fuzzy + substring comparison rather than scoring zero, so adding a
new match type upstream can never silently reintroduce that failure mode.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .bands import (
    DEAD_STATUSES,
    FUZZY_STRONG_RATIO,
    FUZZY_WEAK_RATIO,
    MARK_TYPE_POINTS,
    NEGLIGIBLE,
    SIM_CONTAINS,
    SIM_EXACT,
    SIM_FUZZY_STRONG,
    SIM_FUZZY_WEAK,
    SIM_STARTS_WITH,
    STATUS_POINTS,
    WORD_HIGH_MIN,
    WORD_MEDIUM_MIN,
    HIGH,
    MEDIUM,
    LOW,
)
# 2.3.0: thresholds are read from a ScoringSettings rather than from the
# module constants above, so one run can be scored at a different sensitivity
# without changing what any other run scores. The constants stay imported:
# they are what DEFAULTS defaults to, and several consumers import them from
# here by name.
from .settings import ScoringSettings, resolve

__all__ = ["score_word_result", "risk_from_score", "parse_classes",
           "mark_similarity_components", "METHODS"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean(value) -> str:
    """Normalise any incoming cell/field value to a stripped string."""
    if value is None:
        return ""
    return str(value).strip()


def _fuzzy_ratio(a: str, b: str) -> float:
    """Similarity of two strings in the range 0.0-1.0.

    stdlib difflib, deliberately: it needs no third-party dependency, is
    stable across Python versions, and its behaviour is easy to reason about
    when a paralegal asks why a given mark scored what it did.
    """
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# Orthographic variants that sound identical. Applied before Soundex because
# classic Soundex anchors on the first letter, so it can never match
# PHOTOSHOP against FOTOSHOP -- yet that substitution is one of the commonest
# ways a mark is copied phonetically while looking different on paper.
# Ordered: multi-character clusters first.
_PHONETIC_SUBSTITUTIONS = [
    ("PH", "F"),
    ("GH", "F"),
    ("CK", "K"),
    ("QU", "KW"),
    ("Q", "K"),
    ("X", "KS"),
    ("CE", "SE"),
    ("CI", "SI"),
    ("CY", "SY"),
    ("C", "K"),
    ("Z", "S"),
    ("KN", "N"),
    ("GN", "N"),
    ("WR", "R"),
    ("EE", "I"),
    ("OO", "U"),
    ("Y", "I"),
]


def _phonetic_normalise(word: str) -> str:
    """Fold orthographic spelling variants onto a common phonetic form."""
    word = re.sub(r"[^A-Z]", "", (word or "").upper())
    for src, dst in _PHONETIC_SUBSTITUTIONS:
        word = word.replace(src, dst)
    # Collapse doubled letters: MOMMENTUS -> MOMENTUS
    word = re.sub(r"(.)\1+", r"\1", word)
    return word


def _soundex(word: str) -> str:
    """Classic Soundex phonetic code, used for 'Sounds Like' criteria.

    Returns '' for empty/non-alphabetic input so callers can skip comparison.
    """
    word = re.sub(r"[^A-Z]", "", (word or "").upper())
    if not word:
        return ""
    codes = {
        **dict.fromkeys("BFPV", "1"),
        **dict.fromkeys("CGJKQSXZ", "2"),
        **dict.fromkeys("DT", "3"),
        **dict.fromkeys("L", "4"),
        **dict.fromkeys("MN", "5"),
        **dict.fromkeys("R", "6"),
    }
    first, rest = word[0], word[1:]
    out = first
    prev = codes.get(first, "")
    for ch in rest:
        code = codes.get(ch, "")
        if code and code != prev:
            out += code
            if len(out) == 4:
                break
        # H and W are transparent: they do not reset the previous code.
        if ch not in "HW":
            prev = code
    return (out + "000")[:4]


def parse_classes(value) -> list[int]:
    """Parse a Nice-class field into a list of ints.

    Accepts '11, 12, 35', '11 12 35', '[11, 12]', 'Class 11', a list of ints,
    or a list of strings. Anything unparseable is dropped rather than raising,
    because a malformed class string on one scraped row must not fail the run.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(parse_classes(item))
        return sorted(set(out))
    if isinstance(value, int):
        return [value] if 1 <= value <= 45 else []
    parts = re.split(r"[^0-9]+", str(value))
    return sorted({int(p) for p in parts if p.isdigit() and 1 <= int(p) <= 45})


# ---------------------------------------------------------------------------
# Mark similarity
# ---------------------------------------------------------------------------

def _fuzzy_points(mark_u: str, phrase: str, s: ScoringSettings) -> int:
    """Best fuzzy score of `phrase` against the whole mark and each of its tokens.

    Token-level comparison is what catches 'MOMENTUM MORTGAGE' vs 'MOMENTUS':
    the whole-string ratio is dragged down by the second word, but the first
    token on its own is a strong match.
    """
    best = 0
    ratios = [_fuzzy_ratio(mark_u, phrase)]
    ratios.extend(_fuzzy_ratio(tok, phrase) for tok in mark_u.split())
    for r in ratios:
        if r >= s.fuzzy_strong_ratio:
            best = max(best, s.sim_fuzzy_strong)
        elif r >= s.fuzzy_weak_ratio:
            best = max(best, s.sim_fuzzy_weak)
    return best


def _phonetic_points(mark_u: str, phrase: str, s: ScoringSettings) -> int:
    """Best phonetic score of `phrase` against the whole mark and its tokens.

    Three layers, strongest first: identical phonetic normalisation, identical
    Soundex code, or a strong fuzzy match between the normalised forms. The
    layering matters because no single phonetic algorithm covers trademark
    practice well -- Soundex misses initial-sound substitutions, and
    normalisation alone misses vowel drift (KWIK vs QUICK).
    """
    target_norm = _phonetic_normalise(phrase)
    target_sdx = _soundex(phrase)
    if not target_norm:
        return 0

    candidates = [mark_u] + mark_u.split()
    for cand in candidates:
        cand_norm = _phonetic_normalise(cand)
        if not cand_norm:
            continue
        if cand_norm == target_norm:
            return s.sim_fuzzy_strong
        if target_sdx and _soundex(cand) == target_sdx:
            return s.sim_fuzzy_strong
        if _fuzzy_ratio(cand_norm, target_norm) >= s.fuzzy_strong_ratio:
            return s.sim_fuzzy_strong

    # Nothing strong; allow a weak award for a near-miss on the whole string.
    if _fuzzy_ratio(_phonetic_normalise(mark_u), target_norm) >= s.fuzzy_weak_ratio:
        return s.sim_fuzzy_weak
    return 0


def _contains_whole_word(mark_u: str, phrase: str) -> bool:
    """True if `phrase` appears in `mark_u` on word boundaries.

    Word-boundary anchored so that searching 'ACE' does not match 'PALACE'.
    """
    return bool(re.search(r"\b" + re.escape(phrase) + r"\b", mark_u))


#: How each criterion type is compared. Named so the Triage panel can say
#: "sounds like scored 2" instead of only showing the total (18 Sep 2026).
METHODS = ("exact", "starts_with", "contains", "fuzzy", "phonetic")


def mark_similarity_components(mark_u: str, word_searches,
                               settings: ScoringSettings | None = None) -> list[dict]:
    """Every comparison made between the cited mark and the criteria run.

    One entry per comparison, in the order the scorer makes them, each with
    the method that produced it and the points it was worth. A criterion that
    matched nothing is reported with points 0 rather than omitted — "we tried
    sounds-like and it scored nothing" is the answer to most of the questions
    staff ask about a result, and it was previously unanswerable because the
    scorer returned only the winning total and a sentence.

    `_mark_similarity_points` is the one-line view of this list, so the two
    cannot drift: it picks the best entry from exactly these comparisons.
    """
    s = resolve(settings)
    out: list[dict] = []

    def emit(method: str, points: int, reason: str, phrase: str, stype: str):
        out.append({"method": method, "points": int(points), "reason": reason,
                    "phrase": phrase, "criterion_type": stype})

    for ws in word_searches or []:
        stype = _clean(ws.get("type")).lower()
        phrase = _clean(ws.get("phrase")).upper()
        if not phrase:
            continue

        if stype == "exact match":
            hit = mark_u == phrase
            emit("exact", s.sim_exact if hit else 0,
                 f"exact match on '{phrase}'" if hit else f"not an exact match for '{phrase}'",
                 phrase, stype)

        elif stype == "starts with":
            hit = (mark_u == phrase or mark_u.startswith(phrase + " ")
                   or mark_u.startswith(phrase + "-"))
            emit("starts_with", s.sim_starts_with if hit else 0,
                 f"starts with '{phrase}'" if hit else f"does not start with '{phrase}'",
                 phrase, stype)

        elif stype == "contains":
            hit = _contains_whole_word(mark_u, phrase)
            emit("contains", s.sim_contains if hit else 0,
                 f"contains '{phrase}'" if hit else f"does not contain '{phrase}'",
                 phrase, stype)

        elif stype == "similar to":
            pts = _fuzzy_points(mark_u, phrase, s)
            emit("fuzzy", pts,
                 f"similar to '{phrase}'" if pts else f"not similar enough to '{phrase}'",
                 phrase, stype)

        elif stype == "sounds like":
            pts = _phonetic_points(mark_u, phrase, s)
            emit("phonetic", pts,
                 f"sounds like '{phrase}'" if pts else f"does not sound like '{phrase}'",
                 phrase, stype)

        else:
            # Unrecognised criterion type (including 'related words' and any
            # match type added upstream later). Falls back to substring +
            # fuzzy rather than scoring zero -- see the BR-013 note above.
            # BOTH comparisons are made, and both are reported, in this order.
            hit = _contains_whole_word(mark_u, phrase)
            emit("contains", s.sim_contains if hit else 0,
                 f"contains '{phrase}' ({stype or 'untyped'} criterion)" if hit
                 else f"does not contain '{phrase}' ({stype or 'untyped'} criterion)",
                 phrase, stype)
            pts = _fuzzy_points(mark_u, phrase, s)
            emit("fuzzy", pts,
                 f"resembles '{phrase}' ({stype or 'untyped'} criterion)" if pts
                 else f"does not resemble '{phrase}' ({stype or 'untyped'} criterion)",
                 phrase, stype)

    return out


def _mark_similarity_points(mark_u: str, word_searches,
                            settings: ScoringSettings | None = None) -> tuple[int, str]:
    """Points for how closely the cited mark matches the criteria actually run.

    Returns (points, explanation). Only the single best-matching criterion
    counts; points are not cumulative across criteria. First best wins, so a
    later criterion worth the same does not displace an earlier one.
    """
    best, why = 0, "no criterion matched"
    for c in mark_similarity_components(mark_u, word_searches, settings):
        if c["points"] > best:
            best, why = c["points"], c["reason"]
    return best, why


# ---------------------------------------------------------------------------
# Banding
# ---------------------------------------------------------------------------

def risk_from_score(score: int, status: str,
                    settings: ScoringSettings | None = None) -> str:
    """Legacy numeric banding, retained for callers that only have a score.

    Prefer score_word_result, which routes through the five-band risk model
    and can take goods similarity and trading evidence into account.
    """
    from .bands import RESULT_ONLY, LOW_MEDIUM, MEDIUM_HIGH

    s = resolve(settings)
    if _clean(status).lower() in DEAD_STATUSES:
        return RESULT_ONLY
    if score >= s.word_high_min:
        return HIGH
    if score >= s.word_medium_min:
        return MEDIUM
    if score >= s.word_low_medium_min:
        return LOW_MEDIUM
    return LOW


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score_word_result(result: dict, criteria: dict,
                      settings: ScoringSettings | None = None) -> dict:
    """Score a single search result against the order's search criteria.

    Parameters
    ----------
    result : dict
        One normalised result row. Recognised keys (all optional; missing
        keys are treated as empty):
            status     : str  -- 'Registered' | 'Pending' | 'Ended' | ...
            mark_text  : str  -- the cited mark's text
            mark_type  : str  -- 'Word' | 'Combined' | 'Stylized' | ...
            classes    : str | list -- Nice classes, e.g. '11, 35' or [11, 35]
    criteria : dict
        The order's search criteria. Recognised keys:
            word_searches  : list[dict] -- [{'type': 'Exact Match',
                                             'phrase': 'MOMENTUS'}, ...]
            client_classes : str | list -- the client's own Nice classes

    Returns
    -------
    dict
        {
          'score':      int,
          'risk_band':  'High' | 'Medium/High' | 'Medium' | 'Low/Medium' | 'Low' | 'Result (not live)',
          'components': {'status': int, 'similarity': int,
                         'mark_type': int, 'class_overlap': int},
          'matched_classes': [int, ...],
          'explanation': str   # human-readable, safe to surface in the UI
        }

    The function never raises on malformed input: unparseable fields score
    zero and the result is still banded, because one bad scraped row must not
    fail a whole run.
    """
    s = resolve(settings)
    status = _clean(result.get("status"))
    status_key = status.lower()
    mark_u = _clean(result.get("mark_text")).upper()
    mark_type = _clean(result.get("mark_type")).lower()

    # --- status ---
    status_pts = STATUS_POINTS.get(status_key, 0)

    # --- mark similarity ---
    sim_pts, sim_why = _mark_similarity_points(mark_u, criteria.get("word_searches"), s)

    # --- mark type ---
    type_pts = 0
    for key, pts in MARK_TYPE_POINTS.items():
        if key in mark_type:
            type_pts = max(type_pts, pts)

    # --- class overlap ---
    cited_classes = parse_classes(result.get("classes"))
    client_classes = parse_classes(criteria.get("client_classes"))
    matched = sorted(set(cited_classes) & set(client_classes))
    class_pts = len(matched)

    score = status_pts + sim_pts + type_pts + class_pts

    # Band comes from the five-band risk model, not from the raw total. The
    # total is a measurement; the band is a judgement that also weighs how
    # much of the trade actually overlaps and whether the proprietor trades.
    from .risk_model import assess_risk

    best_phrase = ""
    best_pts = -1
    for ws in (criteria.get("word_searches") or []):
        phrase = _clean(ws.get("phrase"))
        if not phrase:
            continue
        pts, _ = _mark_similarity_points(mark_u, [ws], s)
        if pts > best_pts:
            best_pts, best_phrase = pts, phrase

    goods_band = criteria.get("goods_band") or result.get("goods_band") or "unrelated"
    shared_goods = criteria.get("shared_goods_terms") or []
    assessment = assess_risk(
        settings=s,
        status=status, similarity_points=sim_pts,
        client_mark=best_phrase, cited_mark=_clean(result.get("mark_text")),
        shared_classes=matched, goods_band=goods_band,
        shared_goods_terms=shared_goods,
        class_link=criteria.get("class_link", ""),
        trading=criteria.get("trading"),
    )
    band = assessment.band

    explanation = (
        f"{assessment.explanation}; "
        f"status '{status or 'unknown'}' +{status_pts}; "
        f"{sim_why} +{sim_pts}; "
        f"type '{mark_type or 'unknown'}' +{type_pts}; "
        f"class overlap {matched or 'none'} +{class_pts} "
        f"= {score} -> {band}"
    )

    return {
        "score": score,
        "risk_band": band,
        "components": {
            "status": status_pts,
            "similarity": sim_pts,
            "mark_type": type_pts,
            "class_overlap": class_pts,
        },
        "matched_classes": matched,
        "risk_detail": assessment.as_dict(),
        "explanation": explanation,
    }
