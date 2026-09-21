"""Two-layer conflict model. Experimental - not deployed anywhere.

Separates three things the current scoring conflates:

    Layer 1  MARK SIMILARITY   0-4   How alike are the two trademarks?
    Layer 2  TRADE SIMILARITY  0-4   How related are the goods/services?
    ------------------------------------------------------------------
             CONFLICT          matrix of the two - a factual assessment
             RIGHTS STRENGTH   0-10  Can this right actually cause a problem?

Three rules drive the design.

1. LAYER 1 SEES ONLY THE TWO MARKS. Not status, not classes, not age, not
   jurisdiction, and critically NOT the search operator that found the
   result. MOMENTUM MORTGAGE is exactly as similar to MOMENTUS whether it
   surfaced through Similar To, Contains, a Vienna code, Google or Signa.
   Search operators govern recall - whether a result enters the pool. They
   must not govern the assessment once it is in.

2. CONFLICT AND RIGHTS STAY SEPARATE. An expired registration identical to
   the client's mark is a genuine finding and a negligible threat. One
   number cannot say both. Two can:
       expired identical mark   conflict 8/10, rights 1/10
       live identical mark      conflict 8/10, rights 9/10

3. CLASS OVERLAP IS EVIDENCE, NOT A GATE. Nice classes organise the
   register; they are not a measure of commercial similarity. Different
   class plus closely related goods is a real conflict and must not be
   discarded before scoring.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher

# tmh-scoring 2.0.0: imports are package-relative. Previously these came from
# tm_monitor/ and a lost scratch package via hard-coded sys.path inserts.
from .goods_similarity import goods_similarity  # noqa: E402

# Reuse the phonetic machinery already written and tested.
from .word_scoring import _phonetic_normalise, _soundex  # noqa: E402
from .risk_model import GENERIC_MARK_WORDS, strip_generic  # noqa: E402
from .risk_model import (  # noqa: E402
    _MATRIX as _BAND_MATRIX, _shift as _shift_band,
    LOW, LOW_MEDIUM, MEDIUM, MEDIUM_HIGH, HIGH, RESULT_ONLY,
)
# 2.3.0: every threshold below now comes from a ScoringSettings so that one
# run can be scored at a different sensitivity without moving any other run.
# Omit `settings=` anywhere and DEFAULTS applies, which is exactly the
# calibration these constants held before.
from .settings import ScoringSettings, DEFAULTS, resolve  # noqa: E402


DEAD_STATUSES = {"dead", "expired", "removed", "cancelled", "surrendered",
                 "withdrawn", "refused", "ended", "lapsed", "abandoned",
                 "expired/removed", "not renewed"}

PENDING_STATUSES = {"pending", "published", "application published",
                    "examination", "opposed", "filed"}


# ---------------------------------------------------------------------------
# Layer 1 - mark similarity
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9 ]+", " ", (text or "").upper()).strip()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _distinctive_tokens(text: str) -> list:
    """Tokens excluding generic trade vocabulary."""
    return [t for t in text.split()
            if len(t) > 2 and t.lower() not in GENERIC_MARK_WORDS]


# How much of a mark the matching token has to account for before that token
# match is treated as a similarity between the MARKS rather than between one
# word of each. "SMART" is the whole of one mark but only 56% of "SmartCard";
# "SOBER" is a quarter of "Sober Living Clean Mind". Both are partial.
TOKEN_COVERAGE_FOR_WHOLE = DEFAULTS.token_coverage_for_whole


def _token_coverage(token: str, mark: str) -> float:
    """What share of `mark`'s letters the matching token accounts for."""
    letters = re.sub(r"[^A-Z0-9]+", "", (mark or "").upper())
    return len(token) / len(letters) if letters else 0.0


def _best_token_pair(a: str, b: str):
    """The best-matching distinctive token from each side, with its ratio."""
    ta, tb = _distinctive_tokens(a), _distinctive_tokens(b)
    best, bx, by = 0.0, "", ""
    for x in ta:
        for y in tb:
            r = _ratio(x, y)
            if r > best:
                best, bx, by = r, x, y
    return best, bx, by


def _best_token_ratio(a: str, b: str) -> float:
    """Best similarity between any DISTINCTIVE token of a and of b.

    Catches MOMENTUM MORTGAGE against MOMENTUS, where the whole-string ratio
    is dragged down by the second word but the distinctive tokens match.

    Generic tokens are excluded, or SWIFT COURIERS and RAPID COURIERS score
    a perfect 1.00 on the shared word "COURIERS" - which is the whole point
    of the exercise, and precisely the false positive we are trying to kill.
    """
    ta, tb = _distinctive_tokens(a), _distinctive_tokens(b)
    if not ta or not tb:
        return 0.0
    best = 0.0
    for x in ta:
        for y in tb:
            best = max(best, _ratio(x, y))
    return best


# Soundex encodes one letter plus three consonant codes and then stops. On
# strings of very different length that is not a phonetic judgement, it is a
# four-consonant prefix test: SMARTCARD and SMART both code S563, as do
# "SmartCard" and "SMART COW". Requiring comparable length keeps the real
# cases (VANQUISHER/VANQUISH at 0.80, VETSURE/VETSURA at 1.00) and drops the
# prefix collisions.
SOUNDEX_MIN_LENGTH_RATIO = DEFAULTS.soundex_min_length_ratio


_VOWELS = "AEIOUY"


def syllables(text: str) -> list:
    """Split a mark into rough syllables — vowel groups with the consonants
    that lead them. Approximate by design: it only has to be consistent
    between two marks, not to satisfy a phonetician.
    """
    w = "".join(ch for ch in _phonetic_normalise(text) if ch.isalnum())
    if not w:
        return []
    out, cur, seen_vowel = [], "", False
    for ch in w:
        is_v = ch in _VOWELS
        if is_v and seen_vowel and cur and cur[-1] not in _VOWELS:
            out.append(cur)                     # new syllable starts at its onset
            cur, seen_vowel = ch, True
            continue
        cur += ch
        seen_vowel = seen_vowel or is_v
    if cur:
        if out and not seen_vowel:              # trailing consonants join the last
            out[-1] += cur
        else:
            out.append(cur)
    return out


def phonetic_score(a: str, b: str, settings: ScoringSettings | None = None) -> float:
    """How much two marks SOUND alike, 0-1. A score, never a verdict.

    Jonathan, 16 Sep 2026: "I tend to only hear 'sounds like' when it's 2 to 3
    syllables and they tend to rhyme." So the signal is strongest for short
    marks that rhyme, and decays for longer ones — a four-syllable mark
    carries enough sound to distinguish itself.

    This replaced a boolean built on Soundex equality, which coded any mark
    to its first letter plus three consonants and truncated: RENTAL CLOSET
    and RENTALHEALTH both became R534, so every "Rental Something" on the
    register was declared phonetically equivalent to every other one.

    Compared on the DISTINCTIVE part of each mark, so SWIFT COURIERS and
    RAPID COURIERS do not rhyme their way into a match on the shared generic
    tail (D9).
    """
    s = resolve(settings)
    core_a = _phonetic_normalise(strip_generic(_norm(a))) or _phonetic_normalise(_norm(a))
    core_b = _phonetic_normalise(strip_generic(_norm(b))) or _phonetic_normalise(_norm(b))
    if not core_a or not core_b:
        return 0.0
    if core_a == core_b:
        return 1.0

    sa, sb = syllables(core_a), syllables(core_b)
    if not sa or not sb:
        return 0.0

    # How much of the shorter mark's sound the longer one carries.
    shared = 0
    for x, y in zip(sa, sb):
        if x == y or _ratio(x, y) >= s.phonetic_syllable_match:
            shared += 1
    overlap = shared / min(len(sa), len(sb))

    rhymes = (_ratio(sa[-1], sb[-1]) >= s.phonetic_syllable_match) or sa[-1] == sb[-1]
    score = _ratio(core_a, core_b)
    if rhymes:
        score = max(score, (overlap + 1.0) / 2.0)

    # The 2-3 syllable window is where the ear actually confuses two brands.
    # Outside it the signal is real but weaker, so it is damped rather than
    # discarded — a long mark can still be flagged, it just cannot carry the
    # top tier on sound alone.
    longest = max(len(sa), len(sb))
    if longest > 3:
        score *= s.phonetic_damp_4 if longest == 4 else s.phonetic_damp_5plus
    if abs(len(sa) - len(sb)) >= 2:             # different shape when spoken
        score *= s.phonetic_damp_shape
    return round(min(score, 1.0), 3)


def _edits(a: str, b: str) -> int:
    """Damerau-Levenshtein distance — insertions, deletions, substitutions and
    transpositions. A swapped pair of letters is one slip of the finger, not
    two."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if not la or not lb:
        return max(la, lb)
    prev2, prev = None, list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]):
                cur[j] = min(cur[j], prev2[j - 2] + cost)
        prev2, prev = prev, cur
    return prev[lb]


def orthographic_score(a: str, b: str,
                       settings: ScoringSettings | None = None) -> float:
    """How much two marks are SPELLED alike, 0-1.

    Edit distance, not a sequence ratio, because the ratio is biased by length
    in exactly the wrong direction. One letter changed scores:

        VETSURE / VETSURA                          0.857   (7 chars)
        MOMENTUS / MOMENTUM                        0.875
        SPECSAVERS / SPECSAVERZ                    0.900
        INTERNATIONALBUSINESS / ...BUSINESZ        0.952  (21 chars)

    The SHORT mark is the more confusable one, and it scored lowest — VETSURE
    against VETSURA fell below the tier-3 threshold on a single letter
    (Jonathan, 17 Sep 2026: "vetsure and vetsura have one letter of
    difference, that would make it high risk"). Counting edits removes the
    length bias: one slip is one slip whatever the mark's length.
    """
    s = resolve(settings)
    x = "".join(ch for ch in _norm(a) if ch.isalnum())
    y = "".join(ch for ch in _norm(b) if ch.isalnum())
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    n = _edits(x, y)
    longest = max(len(x), len(y))
    if n == 1:
        return s.ortho_one_edit
    if n == 2:
        return (s.ortho_two_edits_long if longest >= s.ortho_long_min_chars
                else s.ortho_two_edits_short)
    if n == 3 and longest >= s.ortho_very_long_min_chars:
        return s.ortho_three_edits_long
    return _ratio(x, y)


def _phonetic_match(a: str, b: str) -> bool:
    """Kept for callers that want a yes/no. Deliberately strict: the graded
    phonetic_score() is what feeds assessment."""
    return phonetic_score(a, b) >= DEFAULTS.phonetic_strong


@dataclass
class MarkSimilarity:
    tier: int
    reason: str
    orthographic: float = 0.0
    phonetic: bool = False
    shared_words: list = field(default_factory=list)
    visual: str | None = None

    # 2.5.0 — the raw axis measurements, kept so that a row can be re-banded
    # at a different sensitivity, and so the Triage panel can say WHY a row
    # moved rather than only that it did.
    #
    # `orthographic` above is NOT one of these: it has carried `best`, the
    # winning score across all axes, since before the axes were separated, and
    # renaming it would break every stored row. `spelling` below is the
    # spelling axis proper. Both are kept; neither is removed.
    written: float = 0.0        # whole-string / distinctive-core resemblance
    sound: float = 0.0          # phonetic_score
    spelling: float = 0.0       # orthographic_score (edit distance)
    token: float = 0.0          # best distinctive-token pair
    axis: str = ""              # which of written/sound/spelling carried it

    def axes(self) -> dict:
        """The three independent axes plus the token measure, for display."""
        return {"written": self.written, "sound": self.sound,
                "spelling": self.spelling, "token": self.token,
                "axis": self.axis}


def _segment(mark: str, vocabulary) -> set:
    """Words in `mark`, opening up run-together ones.

    Brands concatenate constantly — RentalPassport, SnapFit, RentWallet — and
    a plain whitespace split cannot see the word inside. Measured 17 Sep 2026:
    "Rental Passport" shared a distinctive word with "Rental Closet" and
    scored tier 2, while "RentalPassport" scored tier 0 and was discarded as
    noise before triage. Same mark, spaces removed, and it vanished.

    Two ways in, both requiring a real boundary so that PARENTAL never yields
    RENTAL — a word buried mid-token is a coincidence of spelling, not a
    shared element:

      1. a case boundary in the original text (RentalPassport → Rental, Passport)
      2. the other mark's own word at the START or END of the token, where
         what remains is itself at least three letters — so RENTALPASSPORT
         yields RENTAL, but PARENTAL (leaving "PA") does not
    """
    out = set()
    for raw in (mark or "").split():
        w = "".join(ch for ch in raw if ch.isalnum())
        if not w:
            continue
        out.add(w.upper())
        parts = re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+|\d+", raw)
        if len(parts) > 1:
            out.update(p.upper() for p in parts if len(p) > 2)
        u = w.upper()
        for v in vocabulary:
            if len(v) < 3 or v == u:
                continue
            rest = (u[len(v):] if u.startswith(v)
                    else u[:-len(v)] if u.endswith(v) else None)
            # The remainder must itself look like a word. PARENTAL ends with
            # RENTAL and leaves "PA" — two letters is not the other half of a
            # compound, it is a coincidence of spelling, and treating it as a
            # shared element put SKY PARENTAL ALERT beside the client's mark.
            if rest is not None and len(rest) >= 3:
                out.add(v)
                out.add(rest)
    return {w for w in out if len(w) > 2}


def mark_similarity(client_mark: str, cited_mark: str,
                    visual_decision: str | None = None,
                    settings: ScoringSettings | None = None) -> MarkSimilarity:
    """0-4, from the two mark texts alone (plus visual evidence if supplied).

    Deliberately takes no status, class, date or search-criterion argument.
    """
    s = resolve(settings)
    a, b = _norm(client_mark), _norm(cited_mark)
    if not a or not b:
        return MarkSimilarity(0, "one or both marks have no text")

    if a == b:
        return MarkSimilarity(4, "identical mark text", 1.0, True,
                              written=1.0, sound=1.0, spelling=1.0,
                              token=1.0, axis="written")

    core_a, core_b = _norm(strip_generic(a)), _norm(strip_generic(b))
    words_a = {w.upper() for w in a.split() if len(w) > 2}
    words_b = {w.upper() for w in b.split() if len(w) > 2}
    shared = sorted((_segment(a, words_b) & _segment(b, words_a))
                    - {w.upper() for w in GENERIC_MARK_WORDS})

    whole = _ratio(a, b)
    core = _ratio(core_a, core_b) if core_a and core_b else 0.0
    token, tok_a, tok_b = _best_token_pair(a, b)

    # Similarity of the marks AS WHOLES, kept separate from similarity of one
    # word inside each. Conflating the two is what made a single shared word
    # read as "very close (1.00)".
    whole_like = max(whole, core)

    # A token match speaks for the whole mark only when that token is most of
    # both marks. SMARTCARD/SMART and "Sober Living Clean Mind"/"GO SOBER FOR
    # OCTOBER" are partial resemblances, not near-identities.
    token_is_whole = (
        token >= s.token_strong
        and _token_coverage(tok_a, a) >= s.token_coverage_for_whole
        and _token_coverage(tok_b, b) >= s.token_coverage_for_whole
    )

    # Sound is a SCORE, not a switch (Jonathan, 16 Sep 2026: "we are creating
    # a scoring engine not a decision, we just want to flag and score"). It
    # competes with the written measures for `best` and flows through the same
    # thresholds, instead of jumping a mark to tier 3 on its own.
    phon_score = max(phonetic_score(a, b, s),
                     phonetic_score(core_a, core_b, s) if core_a and core_b else 0.0)
    phon = phon_score >= s.phonetic_strong

    # THREE INDEPENDENT AXES (Jonathan, 17 Sep 2026): "there are 3 independent
    # ways to score — looks like, sounds like, spelled like. Any one can be a
    # high score." So each is measured on its own and the strongest carries
    # the mark; they are never averaged, because averaging lets two quiet
    # axes talk a loud one down. `whole_like`/`token` are the written-form
    # measures, ortho_score is spelling proper (edit distance), phon_score is
    # sound, and visual_floor below is looks.
    ortho_score = max(orthographic_score(a, b, s),
                      orthographic_score(core_a, core_b, s) if core_a and core_b else 0.0)
    # The three axes. `token` is NOT one of them: a shared word scoring 1.00
    # says something about one word, not about either mark as a whole, and it
    # stays behind the token_is_whole coverage guard below. Folding it in here
    # sent 149 rows up a band on the shared word "RENTAL" alone.
    axes_best = max(whole_like, phon_score, ortho_score)
    best = max(axes_best, token)
    axis = max((whole_like, "written"), (phon_score, "sound"),
               (ortho_score, "spelling"))[1]

    # Visual evidence can raise the tier but never lower it - a redrawn logo
    # is a real similarity that the text comparison cannot see.
    visual_floor = {"identical": 4, "similar": 3, "weak": 1}.get(visual_decision or "", 0)

    if core_a and core_b and core_a == core_b:
        tier, reason = 4, f"identical distinctive element '{core_a}'"
    # ANY ONE axis at 0.90+ carries the mark to tier 3 — written form, sound
    # or spelling. Testing `whole_like` alone here is what left VETSURE /
    # VETSURA at tier 2 while its spelling axis read 0.95.
    elif (whole_like >= s.written_strong or phon_score >= s.phonetic_strong
          or ortho_score >= s.orthographic_strong or token_is_whole):
        tier = 3
        if (axis == "spelling" and ortho_score >= s.orthographic_strong
                and ortho_score > max(whole_like, phon_score)):
            n = _edits("".join(c for c in a if c.isalnum()),
                       "".join(c for c in b if c.isalnum()))
            reason = f"spelled alike — {n} letter{'s' if n != 1 else ''} different"
        elif axis == "sound" and phon_score >= s.phonetic_strong and phon_score > whole_like:
            reason = f"sounds alike ({phon_score:.2f})"
        else:
            reason = f"very close ({best:.2f})"
    # PARTIAL RESEMBLANCE - tier 2, not 3.
    #
    # Sharing one distinctive word inside a longer mark is a real similarity
    # and a real result, but it is not near-identity, and treating it as such
    # was the largest single source of overstated risk once the model was fed
    # register-wide candidates rather than a human-narrowed audit pool.
    # "Sober Living Clean Mind" against "GO SOBER FOR OCTOBER" is a Medium,
    # not a High. Changed 12 Aug 2026 on Jonathan's decision; the previous
    # behaviour is preserved in two_layer.py.PRE-TIER-CHANGE-2026-08-12.
    elif shared:
        tier, reason = 2, ("shares distinctive word(s) but is otherwise "
                           f"different: {', '.join(shared[:3])}")
    elif token >= s.token_strong:
        tier, reason = 2, (f"shares a near-identical word ('{tok_a}'/'{tok_b}') "
                           f"within otherwise different marks")
    elif best >= s.similar_min:
        tier, reason = 2, f"similar ({best:.2f})"
    elif best >= s.slight_min:
        tier, reason = 1, f"slight resemblance ({best:.2f})"
    else:
        tier, reason = 0, f"no meaningful resemblance ({best:.2f})"

    if visual_floor > tier:
        tier = visual_floor
        reason = f"visual match: {visual_decision} (text similarity only {best:.2f})"

    return MarkSimilarity(tier, reason, round(best, 3), phon, shared,
                          visual_decision,
                          written=round(whole_like, 3), sound=round(phon_score, 3),
                          spelling=round(ortho_score, 3), token=round(token, 3),
                          axis=axis)


# ---------------------------------------------------------------------------
# Layer 2 - trade similarity
# ---------------------------------------------------------------------------

@dataclass
class TradeSimilarity:
    tier: int
    reason: str
    shared_classes: list = field(default_factory=list)
    goods_band: str = "unknown"
    shared_terms: list = field(default_factory=list)
    evidence: str = "classes only"


def _classes(value) -> list[int]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        out = []
        for v in value:
            out.extend(_classes(v))
        return sorted(set(out))
    return sorted({int(p) for p in re.split(r"[^0-9]+", str(value))
                   if p.isdigit() and 1 <= int(p) <= 45})


def trade_similarity(client_classes, cited_classes,
                     client_goods: str = "", cited_goods: str = "",
                     idf: dict | None = None,
                     settings: ScoringSettings | None = None) -> TradeSimilarity:
    """0-4 for how closely the two trades actually overlap.

    Specification text is the primary evidence where present. Class overlap
    is supporting evidence and, on its own, is weak - class 35 alone covers
    advertising, business management and retail.
    """
    s = resolve(settings)
    ca, cb = _classes(client_classes), _classes(cited_classes)
    shared = sorted(set(ca) & set(cb))

    if client_goods and cited_goods:
        g = goods_similarity(client_goods, cited_goods, ca, cb, idf, s)
        band, terms = g.band, g.shared_terms
        if band == "identical" or (band == "high" and len(shared) >= 2):
            return TradeSimilarity(4, f"same trade ({g.explanation[:60]})", shared, band, terms, "specification")
        if band == "high":
            return TradeSimilarity(3, f"same trade ({g.explanation[:60]})", shared, band, terms, "specification")
        if band == "related":
            tier = 3 if shared else 2
            return TradeSimilarity(tier, f"related trade ({g.explanation[:60]})", shared, band, terms, "specification")
        if band == "slight":
            return TradeSimilarity(1, f"limited overlap ({g.explanation[:50]})", shared, band, terms, "specification")
        # Specifications genuinely unrelated - shared classes do not rescue it.
        return TradeSimilarity(0 if not shared else 1,
                               "specifications unrelated"
                               + (f" despite shared class {shared}" if shared else ""),
                               shared, band, terms, "specification")

    # No usable specification text on one or both sides. Fall back to
    # classes, and be explicit about WHICH side is missing - the two cases
    # need different remedies. Missing client text is ours to fix from the
    # order form; missing cited text needs an enrichment lookup against
    # Temmy, Signa or the register itself.
    if client_goods and not cited_goods:
        evidence = "classes only - cited specification missing"
    elif cited_goods and not client_goods:
        evidence = "classes only - client specification missing"
    else:
        evidence = "classes only - no specification text either side"

    if len(shared) >= s.classes_for_tier3:
        return TradeSimilarity(3, f"{len(shared)} shared classes ({evidence})", shared,
                               "unknown", [], evidence)
    if len(shared) >= s.classes_for_tier2:
        return TradeSimilarity(2,
                               (f"shared class {shared[0]} ({evidence})" if len(shared) == 1
                                else f"{len(shared)} shared classes ({evidence})"),
                               shared, "unknown", [], evidence)
    return TradeSimilarity(0, f"no shared classes ({evidence})", shared,
                           "unknown", [], evidence)


# Enrichment is worth paying for only where it could change the answer.
# Look at the conflict matrix: with mark similarity at 0 or 1, even a
# perfect trade match tops out at 4 - it cannot reach a reportable band. So
# fetching specifications for those rows buys nothing. Gate on Layer 1.
ENRICHMENT_MIN_MARK_TIER = 2


def needs_enrichment(mark: MarkSimilarity, trade: TradeSimilarity) -> bool:
    """True if fetching the cited specification could change the outcome."""
    if trade.evidence == "specification":
        return False
    return mark.tier >= ENRICHMENT_MIN_MARK_TIER


# ---------------------------------------------------------------------------
# Conflict = matrix of the two layers
# ---------------------------------------------------------------------------
# Rows are mark similarity 0-4, columns trade similarity 0-4, values 0-10.
# Note the top-left corner: an identical mark in an unrelated trade still
# scores 5, not 0. It is a reportable finding and, for a well-known mark, a
# dilution question. A product would wrongly zero it.
_CONFLICT = [
    [0, 0, 1, 1, 2],
    [0, 1, 2, 3, 4],
    [1, 2, 4, 5, 6],
    [2, 4, 5, 7, 8],
    [5, 6, 7, 9, 10],
]


@dataclass
class Assessment:
    conflict: int
    rights: int
    priority: str
    mark: MarkSimilarity = None
    trade: TradeSimilarity = None
    rights_reasons: list = field(default_factory=list)
    live: bool = True
    senior: bool | None = None        # D12 - is the CLIENT the senior party?

    def as_row(self):
        return {
            "conflict": self.conflict, "rights": self.rights,
            "priority": self.priority, "live": self.live,
            "client_is_senior": self.senior,
            "mark_tier": self.mark.tier if self.mark else None,
            "trade_tier": self.trade.tier if self.trade else None,
            "mark_reason": self.mark.reason if self.mark else "",
            "trade_reason": self.trade.reason if self.trade else "",
            "trade_evidence": self.trade.evidence if self.trade else "",
            "rights_reason": "; ".join(self.rights_reasons),
        }


# Expiry is a gradient, not a switch. In the UK a lapsed registration can be
# renewed as of right (with a late fee) for six months after expiry, and may
# then be restored at the registrar's discretion for a further six. Only
# after twelve months is it beyond recovery.
#
# A mark that expired last month is very nearly a live right - the proprietor
# can simply pay and have it back, and would if challenged. One that expired
# three years ago cannot be revived at all. Calling both "not live"
# understates the first badly.
#
# Windows differ by office (EUIPO grants six months' grace and no
# discretionary restoration), so these are defaults, overridable per office.
RENEWAL_GRACE_MONTHS = 6        # renewable as of right, late fee payable
# Q4 closed 21 Aug 2026: 6 months, per Jonathan's own filing+126-months rule
# (offered 6 vs 12, he expressed no preference, the recommendation matching
# his stated rule was applied). Was 12 (fail-safe) through the pilot runs -
# results scored before this date treated 6-12-month-lapsed marks as
# restorable (rights 4) rather than gone (rights 1).
RESTORATION_MONTHS = 6          # restorable at the registrar's discretion


# A UK registration runs for ten years from the filing date, and each
# renewal runs for ten more. Filing date is always present in Braudit and
# TemmyDB data, so expiry can be derived where it is not given directly:
#
#   filing + 120 months  = expiry of the current term
#   filing + 126 months  = end of the window in which it can be brought back
#
TERM_MONTHS = 120
RESTORATION_END_MONTHS = 126


def _add_months(d: date, months: int) -> date:
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return date(y, m + 1, day)


def derive_expiry(filing_date, today: date | None = None, live: bool = True,
                  term_months: int = TERM_MONTHS):
    """Work out the relevant term-end date from the filing date.

    Renewal resets the clock, so filing + 120 months is only the FIRST
    expiry. A mark filed in 2005 and renewed twice expires in 2035.

    For a mark still on the register we therefore return the NEXT term end
    (useful in its own right - it is the renewal date). For one that has
    lapsed we return the MOST RECENT term end, which is when it most likely
    fell away.

    Returns (expiry_date, was_derived) or (None, False).
    """
    filed = _parse_date(filing_date)
    if not filed:
        return None, False
    today = today or date.today()
    elapsed = (today.year - filed.year) * 12 + (today.month - filed.month)
    terms = elapsed / term_months
    n = (int(terms) + 1) if live else max(1, int(terms))
    return _add_months(filed, n * term_months), True


def expiry_stage(expiry_date=None, today: date | None = None,
                 grace_months: int = RENEWAL_GRACE_MONTHS,
                 restore_months: int = RESTORATION_MONTHS,
                 filing_date=None, live: bool = False):
    """How recoverable is a lapsed registration?

    Uses the stated expiry date where present. Falls back to deriving it
    from the filing date, which Braudit and TemmyDB always carry - so
    "recoverability unknown" should now be rare rather than routine.

    Returns (stage, months_since_expiry, source).
    """
    exp = _parse_date(expiry_date)
    source = "stated expiry date"
    if not exp:
        exp, derived = derive_expiry(filing_date, today, live=live)
        source = "derived from filing date" if derived else "no date available"
    if not exp:
        return "unknown", None, source

    today = today or date.today()
    months = (today.year - exp.year) * 12 + (today.month - exp.month)
    if months < 0:
        return "not yet expired", months, source
    if months < grace_months:
        return "renewable", months, source
    if months < restore_months:
        return "restorable", months, source
    return "lapsed", months, source


def rights_strength(status: str, registration_date=None, jurisdiction_match: bool | None = None,
                    proprietor_status: str = "", expiry_date=None,
                    filing_date=None) -> tuple[int, list, bool]:
    """0-10 for how capable this right is of causing a real problem."""
    reasons = []
    s = (status or "").strip().lower()

    if s in DEAD_STATUSES:
        stage, months, source = expiry_stage(expiry_date, filing_date=filing_date, live=False)
        ps = (proprietor_status or "").lower()

        if stage == "renewable":
            # Recoverable by simply paying. Treated as LIVE, because in
            # practice it is - the proprietor holds an unconditional right to
            # bring it back and would exercise it if challenged.
            return 7, [f"expired {months} month(s) ago ({source}) - renewable as of right "
                       f"for {RENEWAL_GRACE_MONTHS} months; effectively still enforceable"], True

        if stage == "restorable":
            return 4, [f"expired {months} month(s) ago ({source}) - restoration still "
                       f"possible up to {RESTORATION_MONTHS} months from expiry"], False

        if stage == "lapsed":
            score, note = 1, f"expired {months} month(s) ago ({source}) - beyond restoration"
            if "active" in ps:
                score, note = 2, note + "; proprietor still trading, re-filing possible"
            return score, [note], False

        # No expiry date. We cannot tell which window applies and must not
        # imply the right is safely gone.
        return 1, [f"status '{status or 'unknown'}' - not live; no expiry date supplied, "
                   f"so recoverability could not be assessed"], False

    if s in PENDING_STATUSES:
        score = 6
        reasons.append(f"pending ({status}) - not yet enforceable, but may be opposable")
    elif s in {"registered", "protected", "renewed"}:
        score = 9
        reasons.append(f"registered ({status})")
    else:
        score = 5
        reasons.append(f"status '{status or 'unknown'}' not recognised - treated as live")

    if jurisdiction_match is True:
        reasons.append("same jurisdiction as client")
    elif jurisdiction_match is False:
        score -= 2
        reasons.append("different jurisdiction")

    # Vulnerability. A UK registration over five years old is exposed to
    # non-use revocation, and a dissolved or dormant proprietor makes that
    # materially more likely. This reduces the RIGHT's strength - it never
    # touches the similarity assessment.
    reg = _parse_date(registration_date)
    if reg and (date.today() - reg).days > 5 * 365:
        ps = (proprietor_status or "").lower()
        if any(k in ps for k in ("dissolved", "liquidation", "dormant", "struck")):
            score -= 3
            reasons.append("registered 5+ years and proprietor not trading - non-use exposure")
        else:
            reasons.append("registered 5+ years - non-use revocation possible if unused")

    return max(0, min(10, score)), reasons, True


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


# D12 - seniority. If the cited mark was filed BEFORE the client's, the client
# is not the senior party, and this is not a weaker opportunity but a
# different situation entirely: the client may be the one at risk. It is
# reported as a state, not a risk band, because a band would imply the client
# holds the stronger position.
SENIORITY_REVIEW = "Review - client may be at risk"


def client_is_senior(client_filing_date, cited_filing_date) -> bool | None:
    """True if the client filed first, False if the cited mark did.

    None where either date is missing - which must not be read as "fine".
    An unknown seniority is unknown, and D15 says an uncollected field must
    never be indistinguishable from an empty one.
    """
    a, b = _parse_date(client_filing_date), _parse_date(cited_filing_date)
    if not a or not b:
        return None
    return a <= b


# D4 - the band comes from the two LAYERS, not from the conflict/rights pair.
#
# The previous implementation derived the band from conflict and rights
# thresholds, and could never return Medium/High - one of the five required
# bands was simply unreachable. risk_model.py already carries the correct
# table (mark tier x trade tier), so it is used rather than maintaining a
# second, divergent one.
#
# Rights does NOT set the band. D5: "a registered mark is a threat regardless
# of age; what age affects is how hard it is to remove, which belongs in
# Rights, not this score." Rights stays its own number (D3). The one thing
# status decides here is whether the result carries a band at all.
LAYER_DISAGREEMENT = 3


def _priority(conflict: int, rights: int, live: bool,
              mark_tier: int | None = None, trade_tier: int | None = None,
              trading_tier: int = 0) -> str:
    if not live:
        return RESULT_ONLY

    if mark_tier is None or trade_tier is None:
        # Legacy callers that pass only conflict/rights. Kept so existing
        # fixtures and compare.py keep working.
        if conflict >= 7 and rights >= 7:
            return HIGH
        if conflict >= 7 or (conflict >= 5 and rights >= 7):
            return MEDIUM
        if conflict >= 4:
            return LOW_MEDIUM if rights >= 5 else LOW
        return LOW

    band = _BAND_MATRIX[mark_tier][trade_tier]

    # D11 - trading evidence is a TIE-BREAKER, applied asymmetrically.
    #
    # Read literally the rule is "active trading moves the band up", but
    # "active" is the normal state of a UK company: applied that way it moved
    # half the pilot's results up a band and only 5% down, which is a blanket
    # uplift rather than something that separates two otherwise-equal
    # results. So only the STRONGER signal lifts a band - tier 2, an active
    # company trading in the same field, which is the distinction
    # risk_model.TradingEvidence already draws. A merely-active proprietor
    # shifts nothing, and neither does an unmatched one.
    if trading_tier >= 2:
        band = _shift_band(band, 1)
    elif trading_tier < 0:
        band = _shift_band(band, -1)

    # D4 - the half-bands are for AMBIGUITY. Where the two layers disagree
    # sharply, a confident band is pulled back to its neighbouring half-band
    # so a human looks at it rather than the report asserting something only
    # one axis supports.
    if abs(mark_tier - trade_tier) >= LAYER_DISAGREEMENT and band in (HIGH, LOW):
        band = _shift_band(band, -1 if band == HIGH else 1)

    return band


def assess(client_mark: str, cited_mark: str, client_classes, cited_classes,
           status: str = "", client_goods: str = "", cited_goods: str = "",
           registration_date=None, jurisdiction_match=None,
           proprietor_status: str = "", visual_decision: str | None = None,
           idf: dict | None = None, expiry_date=None, filing_date=None,
           client_filing_date=None, trading_tier: int = 0,
           settings: ScoringSettings | None = None) -> Assessment:
    """Full two-layer assessment for one cited result.

    `settings` (2.3.0) is an optional ScoringSettings carrying this run's
    sensitivity. Omitted, the shipped calibration applies and the result is
    identical to every prior version — the property band_diff proves.
    """
    s = resolve(settings)
    m = mark_similarity(client_mark, cited_mark, visual_decision, s)
    t = trade_similarity(client_classes, cited_classes, client_goods, cited_goods, idf, s)
    conflict = _CONFLICT[m.tier][t.tier]
    rights, reasons, live = rights_strength(status, registration_date,
                                            jurisdiction_match, proprietor_status,
                                            expiry_date, filing_date)
    priority = _priority(conflict, rights, live, m.tier, t.tier, trading_tier)

    # D12. Seniority is decided AFTER conflict and rights, and overrides only
    # the reported priority - it never touches either number. A cited mark
    # that predates the client's is still the same factual conflict; what
    # changes is who is exposed.
    senior = client_is_senior(client_filing_date, filing_date)
    if senior is False and live and conflict >= 4:
        reasons = list(reasons) + [
            "cited mark was filed before the client's - the client is not the "
            "senior party"]
        priority = SENIORITY_REVIEW

    return Assessment(conflict, rights, priority, m, t, reasons, live, senior)
