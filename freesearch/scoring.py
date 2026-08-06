"""Pure scoring over `MarkRecord`. No I/O, no ML, no model load.

This is a *faithful re-homing* of the scoring in `deploy-v2-hotfix/filters.py`
(BR-013, 19 Jun 2026), lifted off positional row tuples and onto MarkRecord.

Every branch below must produce byte-identical results to its counterpart in
filters.py. `tests/test_parity.py` asserts this across a wide synthetic
sample. Until filters.py is switched to delegate here (follow-up), treat that
test as the contract: if it goes red, the free search and the paid audit have
started disagreeing about risk, which is a commercial bug, not just a code
smell.

Deliberately dependency-free:
  - fuzzy matching via stdlib `difflib`, not RapidFuzz
  - no BERT, no CLIP, no ResNet
Free search must cost ~nothing per call. The Toolkit PoC's embedding stack is
better in the abstract and worse here: it loads models per request and speaks
a different risk vocabulary from the audit the client is being sold.
"""
from __future__ import annotations

import difflib
import re

from .models import MarkRecord

# Risk band thresholds — mirrored from filters.risk_from_score.
HIGH_RISK_THRESHOLD = 11
MEDIUM_RISK_THRESHOLD = 8

# Similarity thresholds — mirrored from filters._mark_similarity_score.
STRONG_FUZZY = 0.85
WEAK_FUZZY = 0.78

# Minimum token length for the token-overlap branch of "similar to".
MIN_TOKEN_LEN = 4


def cleanstr(s) -> str:
    if s is None:
        return ''
    return str(s).strip()


def _fuzzy_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Match predicates (filters.mark_matches_search_row / mark_matches_any)
# ---------------------------------------------------------------------------

def mark_matches_search_row(mark_text: str, search_type: str,
                            phrase: str) -> bool:
    """Does `mark_text` match this single (type, phrase) row?"""
    if not mark_text or not phrase:
        return False
    mt = cleanstr(mark_text).upper()
    p = cleanstr(phrase).upper()
    if not p:
        return False
    stype = cleanstr(search_type).lower()

    if stype == 'exact match':
        return mt == p
    if stype == 'starts with':
        return mt == p or mt.startswith(p + ' ') or mt.startswith(p + '-')
    if stype == 'contains':
        return p in mt
    if stype == 'similar to':
        if _fuzzy_ratio(mt, p) >= WEAK_FUZZY:
            return True
        for tok in mt.split():
            if _fuzzy_ratio(tok, p) >= STRONG_FUZZY:
                return True
        # Token-overlap (10 Jun 2026): 'Properly Services' should match a
        # search for 'Nicholson Flooring Services' on the SERVICES token.
        mark_tokens = set(re.findall(r'\w+', mt))
        for ptok in re.findall(r'\w+', p):
            if len(ptok) >= MIN_TOKEN_LEN and ptok in mark_tokens:
                return True
        return False

    # Unknown type falls back to Starts With, as in filters.py.
    return mt == p or mt.startswith(p + ' ')


def mark_matches_any(mark_text: str,
                     word_searches: list[dict] | None) -> bool:
    if not word_searches:
        return False
    return any(
        mark_matches_search_row(mark_text, ws.get('type', ''),
                                ws.get('phrase', ''))
        for ws in word_searches
    )


# ---------------------------------------------------------------------------
# Score components
# ---------------------------------------------------------------------------

def _status_score(status: str) -> int:
    s = cleanstr(status).lower()
    if s == 'registered':
        return 4
    if s == 'pending':
        return 3
    return 0  # 'ended' and anything unrecognised


def mark_similarity_score(mark_u: str, word_searches: list[dict] | None,
                          root: str = '') -> int:
    """Mark-similarity component of the word score.

    Points (max across all matching rows):
        Exact Match     +4
        Starts With     +2
        Contains        +2
        Similar (>=.85) +2
        Similar (>=.78) +1

    Falls back to legacy root-word behaviour when no word_searches supplied,
    preserving back-compat with older Step-5 callers.
    """
    if not word_searches:
        root_u = cleanstr(root).upper()
        if not root_u:
            return 0
        if mark_u == root_u:
            return 4
        if mark_u.startswith(root_u + ' ') or mark_u.startswith(root_u + '-'):
            return 2
        return 0

    best = 0
    for ws in word_searches:
        stype = cleanstr(ws.get('type', '')).lower()
        phrase = cleanstr(ws.get('phrase', '')).upper()
        if not phrase:
            continue

        if stype == 'exact match':
            if mark_u == phrase:
                best = max(best, 4)
        elif stype == 'starts with':
            if (mark_u == phrase or mark_u.startswith(phrase + ' ')
                    or mark_u.startswith(phrase + '-')):
                best = max(best, 2)
        elif stype == 'contains':
            if re.search(r'\b' + re.escape(phrase) + r'\b', mark_u):
                best = max(best, 2)
        elif stype == 'similar to':
            whole = _fuzzy_ratio(mark_u, phrase)
            if whole >= STRONG_FUZZY:
                best = max(best, 2)
            elif whole >= WEAK_FUZZY:
                best = max(best, 1)
            for tok in mark_u.split():
                tr = _fuzzy_ratio(tok, phrase)
                if tr >= STRONG_FUZZY:
                    best = max(best, 2)
                elif tr >= WEAK_FUZZY:
                    best = max(best, 1)
    return best


def _class_overlap(classes: list[int], target_classes) -> int:
    targets = set(target_classes or ())
    return sum(1 for n in classes if n in targets)


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------

def score_word(rec: MarkRecord, target_classes=(),
               word_searches: list[dict] | None = None,
               root: str = '') -> int:
    """WORD-axis threat score. Mirrors filters.score_trademark."""
    score = _status_score(rec.status)
    score += mark_similarity_score(cleanstr(rec.mark_text).upper(),
                                   word_searches, root)

    mtype = cleanstr(rec.mark_type).lower()
    if mtype == 'word':
        score += 2
    elif mtype == 'combined':
        score += 1
    elif 'stylized' in mtype:
        score += 1

    score += _class_overlap(rec.classes, target_classes)
    return score


def score_image(rec: MarkRecord, target_classes=(),
                client_vienna: str = '') -> int:
    """IMAGE-axis threat score. Mirrors filters.score_trademark_image.

    Vienna overlap is a documented no-op: cited-record Vienna codes are not
    captured at initial-audit time (see BR-009, parked). The hook stays; the
    data does not yet exist. Free-tier logo search must NOT depend on this.
    """
    score = _status_score(rec.status)

    mtype = cleanstr(rec.mark_type).lower()
    if 'figurative' in mtype:
        score += 4
    elif 'combined' in mtype:
        score += 3
    elif 'stylized' in mtype or 'stylised' in mtype:
        score += 2
    # word-only: 0 — a word mark does not threaten an image registration

    score += _class_overlap(rec.classes, target_classes)

    _ = client_vienna  # placeholder, per filters.py
    return score


def risk_band(score: int, status: str) -> str:
    """Mirrors filters.risk_from_score."""
    if cleanstr(status).lower() == 'ended':
        return 'Negligible'
    if score >= HIGH_RISK_THRESHOLD:
        return 'High Risk'
    if score >= MEDIUM_RISK_THRESHOLD:
        return 'Medium Risk'
    return 'Low Risk'


_RISK_ORDER = {'High Risk': 3, 'Medium Risk': 2,
               'Low Risk': 1, 'Negligible': 0}


def worst_risk(records: list[MarkRecord]) -> str:
    """Overall headline risk for the report = worst individual band."""
    if not records:
        return 'Negligible'
    return max((r.risk for r in records),
               key=lambda b: _RISK_ORDER.get(b, 0))


def score_record(rec: MarkRecord, target_classes=(),
                 word_searches: list[dict] | None = None,
                 root: str = '', axis: str = 'word') -> MarkRecord:
    """Score in place and set the risk band from the requested axis."""
    rec.word_score = score_word(rec, target_classes, word_searches, root)
    rec.image_score = score_image(rec, target_classes)
    primary = rec.image_score if axis == 'image' else rec.word_score
    rec.risk = risk_band(primary, rec.status)
    return rec
