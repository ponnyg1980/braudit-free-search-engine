"""TMH scoring module — the ONE copy (tmh-scoring 2.0.0, consolidated 17 Sep 2026).

Consumers: the audit engine, the fortnightly UK Trademark Watch runner, and —
by explicit decision only — Braudit. Do not copy these files elsewhere; import
the package. See ../README.md and ../CHANGELOG.md.

    tmh_scoring.two_layer         the settled scorer (D1-D12) — assess()
    tmh_scoring.goods_similarity  Layer 2 specification overlap, with D8 stem()
    tmh_scoring.risk_model        bands, matrix, overlap tiers
    tmh_scoring.word_scoring      v1 score_word_result, kept while callers migrate
    tmh_scoring.image_scoring     pHash + CLIP (D13)
    tmh_scoring.term_derivation   search-term derivation (recall side)
    tmh_scoring.settings          ScoringSettings — per-run thresholds (2.3.0)
    tmh_scoring.sensitivity       labelled Triage steps -> settings (2.4.0)
    (phonetics.py reconstruction is archived in docs/archive — see CHANGELOG)

Original v1.2.0 docstring follows.

TMH scoring module.

Drop-in replacement for the AI/LLM scoring layer in Braudit. Two independent
scorers, both CPU-only:

    from tmh_scoring import score_word_result, score_image_result

    word = score_word_result(
        {"status": "Registered", "mark_text": "MOMENTUM MORTGAGE",
         "mark_type": "Word", "classes": "36"},
        {"word_searches": [{"type": "Similar To", "phrase": "MOMENTUS"}],
         "client_classes": [36]},
    )
    # -> {'score': 9, 'risk_band': 'Medium', ...}

See README.md for full field mappings, JSON examples and integration notes.

Provided for use within The Trademark Helpline's Braudit instance only.
"""
from .bands import (HIGH, LOW, LOW_MEDIUM, MEDIUM, MEDIUM_HIGH,
                    NEGLIGIBLE, RESULT_ONLY, RISK_BANDS)
from .goods_similarity import build_idf, goods_similarity
from .settings import (COMPARTMENTS, COMPARTMENT_FIELDS, COMPARTMENT_LABELS,
                       DEFAULTS, ScoringSettings)
from .risk_model import TradingEvidence, assess_risk
from .word_scoring import parse_classes, risk_from_score, score_word_result

__version__ = "2.4.0"

__all__ = [
    "score_word_result",
    "risk_from_score",
    "parse_classes",
    "assess_risk",
    "TradingEvidence",
    "goods_similarity",
    "build_idf",
    "ScoringSettings",
    "DEFAULTS",
    "COMPARTMENTS",
    "COMPARTMENT_FIELDS",
    "COMPARTMENT_LABELS",
    "RISK_BANDS",
    "HIGH",
    "MEDIUM_HIGH",
    "MEDIUM",
    "LOW_MEDIUM",
    "LOW",
    "RESULT_ONLY",
    "NEGLIGIBLE",
    "__version__",
]


def __getattr__(name):
    """Lazily expose the image scorer.

    Kept out of the eager import path so that a deployment which only needs
    word scoring never imports onnxruntime, and so that a missing optional
    dependency surfaces as a clear error at call time rather than breaking
    `import tmh_scoring` outright.
    """
    if name in {
        "score_image_result",
        "score_image_batch",
        "get_scorer",
        "VisualSimilarityScorer",
        "phash_distance",
    }:
        from . import image_scoring

        return getattr(image_scoring, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
