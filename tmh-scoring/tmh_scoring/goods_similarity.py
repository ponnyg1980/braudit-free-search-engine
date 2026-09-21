"""Goods and services similarity from specification text.

Class-number overlap is a poor proxy for conflict. Class 35 covers
advertising, business management and retail; a clothing retailer and a
management consultancy both sit in it and do not compete. Conversely genuine
conflicts cross classes - clothing in 25 against retail of clothing in 35 is
a classic overlap that a class-number comparison scores as zero.

This module compares the actual specification text. It is deliberately
deterministic and explainable rather than semantic: the output names the
terms the two specifications share, so a client asking "why is this a
concern" gets "both cover veterinary services and pet insurance" rather than
a similarity coefficient. That matters more than marginal accuracy when the
answer goes into a report someone may have to justify.

Real IPO specification text looks like:

    "Services for providing food and drink; temporary accommodation;
     accommodation reservations(temporary -); hotel reservations"
    "Absorbent cotton wool [for medical purposes];Dressings (Surgical -);
     Pharmaceutical and veterinary preparations"

so the parser handles semicolon and comma delimiting, inverted parenthetical
qualifiers, square-bracket notes, and the boilerplate tails that carry no
distinguishing content.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

__all__ = ["goods_similarity", "normalise_specification", "GoodsSimilarity",
           "RELATED_CLASSES", "class_relationship"]


# Phrases that appear in a large share of specifications and carry no
# distinguishing information. Removed before comparison.
BOILERPLATE = [
    "information advisory and consultancy services relating to all of the aforesaid",
    "advisory and consultancy services relating to all of the aforesaid",
    "all of the aforesaid services",
    "all the aforesaid goods",
    "all of the aforesaid goods",
    "all the aforesaid services",
    "the aforesaid services",
    "the aforesaid goods",
    "none of the aforesaid",
    "included in class",
    "not included in other classes",
    "relating to all of the aforesaid",
    "and parts and fittings therefor",
    "parts and fittings for all the aforesaid",
    "namely",
]

STOPWORDS = {
    # general
    "a", "an", "and", "or", "of", "for", "the", "to", "in", "on", "with",
    "by", "from", "all", "other", "others", "any", "such", "as", "at",
    "being", "relating", "related", "connected", "aforesaid", "thereof",
    "therefor", "including", "included", "namely", "etc", "class", "classes",
    # trademark specification filler - present in a large share of specs
    "services", "service", "goods", "products", "product", "provision",
    "providing", "supply", "supplying", "apparatus", "equipment", "devices",
    "instruments", "preparations", "materials", "substances", "articles",
    "parts", "fittings", "accessories", "systems", "solutions", "purposes",
    "use", "used", "non", "not", "their", "these", "this", "those",
}

_SPLIT = re.compile(r"[;,\n]+")
_CLEAN = re.compile(r"\[[^\]]*\]|\([^)]*\)")      # [notes] and (inversions)
_NONWORD = re.compile(r"[^a-z0-9\s\-]+")
_DASHES = re.compile(r"[‐-―−]")


# Cross-class relationships recognised in practice. Not exhaustive - a
# fallback for when the specification text is thin or missing, since term
# overlap is always preferred where text exists.
RELATED_CLASSES: dict[frozenset, str] = {
    frozenset({25, 35}): "clothing and retail of clothing",
    frozenset({9, 42}): "software and software services",
    frozenset({9, 38}): "communications hardware and services",
    frozenset({29, 30}): "foodstuffs",
    frozenset({29, 43}): "foodstuffs and food services",
    frozenset({30, 43}): "foodstuffs and food services",
    frozenset({32, 33}): "beverages",
    frozenset({32, 43}): "beverages and drink services",
    frozenset({33, 43}): "beverages and drink services",
    frozenset({3, 44}): "cosmetics and beauty services",
    frozenset({5, 44}): "pharmaceuticals and medical services",
    frozenset({5, 10}): "pharmaceuticals and medical devices",
    frozenset({36, 45}): "financial and legal services",
    frozenset({35, 36}): "business and financial services",
    frozenset({41, 42}): "education and technology services",
    frozenset({16, 41}): "printed matter and education",
    frozenset({20, 35}): "furniture and retail of furniture",
    frozenset({11, 37}): "installations and installation services",
    frozenset({19, 37}): "building materials and construction",
    frozenset({37, 42}): "construction and design services",
}


def class_relationship(classes_a, classes_b) -> tuple[bool, str]:
    """True if any class pair across the two sets is a recognised relationship."""
    for a in classes_a or []:
        for b in classes_b or []:
            key = frozenset({int(a), int(b)})
            if len(key) == 2 and key in RELATED_CLASSES:
                return True, RELATED_CLASSES[key]
    return False, ""


def normalise_specification(text: str) -> list[str]:
    """Split a specification into cleaned terms."""
    if not text:
        return []
    s = _DASHES.sub("-", str(text)).lower()
    s = _CLEAN.sub(" ", s)
    for phrase in BOILERPLATE:
        s = s.replace(phrase, " ")
    parts = []
    for chunk in _SPLIT.split(s):
        chunk = _NONWORD.sub(" ", chunk)
        chunk = " ".join(chunk.split())
        if chunk and len(chunk) > 1:
            parts.append(chunk)
    return parts


# D8 - stemming is required, and it MUST CONVERGE. Before this was added
# there was no stemmer at all: "Mattresses, beds and bedding" against
# "Mattress, bed, bed linen" scored UNRELATED with zero shared terms, because
# "mattresses" and "mattress" were simply different strings. Every trade
# comparison was understated by ordinary plural/singular variation.
#
# The rule the decisions record gives is precise: words ending "ss" keep it,
# and "-ing" strips the doubled consonant so bedding -> bed. The trap it
# names is a stemmer where mattresses -> mattress but mattress -> mattres,
# so the two never meet. Every rule below is therefore idempotent: stemming
# an already-stemmed word returns it unchanged.
_SIBILANT_ENDINGS = ("s", "x", "z", "ch", "sh")


def stem(word: str) -> str:
    """Reduce a word to a form that plurals and gerunds converge on."""
    w = (word or "").lower()
    if len(w) <= 3:
        return w

    # -ing, undoubling the consonant: bedding -> bed, running -> run
    if w.endswith("ing") and len(w) > 5:
        base = w[:-3]
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in "aeiou":
            base = base[:-1]
        return base

    if w.endswith("ss"):            # mattress, glass, dress - already terminal
        return w

    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"

    if w.endswith("es") and len(w) > 3:
        base = w[:-2]
        # boxes -> box, mattresses -> mattress, watches -> watch
        if base.endswith(_SIBILANT_ENDINGS):
            return base
        # services -> service, not "servic"
        return w[:-1]

    if w.endswith("s") and not w.endswith("us") and not w.endswith("ss"):
        return w[:-1]

    return w


def _content_tokens(terms: list[str]) -> Counter:
    out: Counter = Counter()
    for term in terms:
        for tok in term.split():
            tok = tok.strip("-")
            if len(tok) > 2 and tok not in STOPWORDS and not tok.isdigit():
                out[stem(tok)] += 1
    return out


@dataclass
class GoodsSimilarity:
    score: float                      # 0.0 - 1.0
    band: str                         # identical | high | related | slight | unrelated
    shared_terms: list = field(default_factory=list)
    class_link: str = ""
    explanation: str = ""

    def as_dict(self):
        return {"score": round(self.score, 3), "band": self.band,
                "shared_terms": self.shared_terms[:12],
                "class_link": self.class_link, "explanation": self.explanation}


HIGH, RELATED, SLIGHT, UNRELATED, IDENTICAL = "high", "related", "slight", "unrelated", "identical"


def goods_similarity(spec_a: str, spec_b: str,
                     classes_a=None, classes_b=None,
                     idf: dict | None = None,
                     settings=None) -> GoodsSimilarity:
    """Compare two specifications.

    `idf` optionally maps token -> inverse document frequency weight, so that
    rare terms count for more than ubiquitous ones. Compute it from the
    candidate set of a run (see build_idf) - it costs nothing and adapts to
    the sector automatically.
    """
    from .settings import resolve

    s = resolve(settings)
    terms_a, terms_b = normalise_specification(spec_a), normalise_specification(spec_b)
    tok_a, tok_b = _content_tokens(terms_a), _content_tokens(terms_b)

    linked, link_desc = class_relationship(classes_a, classes_b)

    if not tok_a or not tok_b:
        # No usable text. Fall back to the class relationship alone, and say so.
        if linked:
            return GoodsSimilarity(s.goods_class_link_score, RELATED, [], link_desc,
                                   f"no specification text; classes are related ({link_desc})")
        return GoodsSimilarity(0.0, UNRELATED, [], "",
                               "no specification text available to compare")

    shared = set(tok_a) & set(tok_b)

    def weight(tok):
        return (idf or {}).get(tok, 1.0)

    shared_w = sum(weight(t) for t in shared)
    denom = min(sum(weight(t) for t in tok_a), sum(weight(t) for t in tok_b))
    # Overlap coefficient rather than Jaccard: specification lengths vary
    # enormously, and a narrow spec wholly inside a broad one is a real
    # conflict that Jaccard would score as small.
    score = (shared_w / denom) if denom else 0.0

    exact_same = " ".join(sorted(tok_a)) == " ".join(sorted(tok_b))
    if exact_same:
        band = IDENTICAL
    elif score >= s.goods_high_min:
        band = HIGH
    elif score >= s.goods_related_min:
        band = RELATED
    elif score >= s.goods_slight_min:
        band = SLIGHT
    else:
        band = UNRELATED

    if linked and band in (SLIGHT, UNRELATED):
        band = RELATED
        score = max(score, s.goods_class_link_score)

    ranked = sorted(shared, key=lambda t: -weight(t))[:12]
    bits = []
    if ranked:
        bits.append("shared: " + ", ".join(ranked[:6]))
    if linked:
        bits.append(f"related classes ({link_desc})")
    bits.append(f"overlap {score:.2f}")

    return GoodsSimilarity(score, band, ranked, link_desc, "; ".join(bits))


def build_idf(specifications) -> dict:
    """Inverse document frequency over a set of specifications.

    Downweights terms that appear in most specifications in the sector -
    "clothing" is not distinguishing between two clothing retailers, but
    "orthodontic" between two medical suppliers very much is.
    """
    docs = [set(_content_tokens(normalise_specification(s))) for s in specifications if s]
    n = len(docs)
    if not n:
        return {}
    df: Counter = Counter()
    for d in docs:
        df.update(d)
    return {tok: math.log((n + 1) / (count + 1)) + 1.0 for tok, count in df.items()}
