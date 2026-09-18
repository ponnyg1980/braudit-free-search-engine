"""Scoring imports for the UK Watch runner, from the one scoring package.

Since 17 Sep 2026 every scoring module comes from temmy-access/tmh-scoring
(tmh_scoring 2.0.0). Before that this file stitched together
`scoring harness (experimental)/two_layer.py`, tm_monitor's goods/risk/term
modules and a synthetic `tmh_scoring` shim over vendor/; those copies are now
in _superseded_2026-09-17_scoring/. The switch was proven to change nothing:
tmh-scoring/releases/2.0.0.md.

tm_monitor stays on the path for validate.py only. No scoring lives there.
"""
from __future__ import annotations

import sys
from pathlib import Path

from config import TM_MONITOR_DIR, TMH_SCORING_DIR  # noqa: E402

_READY = False


def ensure_paths() -> None:
    global _READY
    if _READY:
        return
    for d in (str(TM_MONITOR_DIR), str(TMH_SCORING_DIR)):   # scoring ends up first
        if d not in sys.path:
            sys.path.insert(0, d)
    import tmh_scoring
    loaded = Path(tmh_scoring.__file__).resolve()
    if TMH_SCORING_DIR.resolve() not in loaded.parents:
        # Fail closed (D15). A second tmh_scoring on the path is exactly the
        # fork this package exists to end.
        raise ImportError(f"tmh_scoring was imported from {loaded}, not from "
                          f"{TMH_SCORING_DIR} - another copy is on sys.path")
    _READY = True


ensure_paths()

# Re-export the pieces the runner uses, so callers import from one place.
import tmh_scoring                                                    # noqa: E402
from tmh_scoring.term_derivation import derive_search_terms          # noqa: E402
from tmh_scoring.goods_similarity import goods_similarity, build_idf  # noqa: E402
from validate import validate_run, PASS, WARN, FAIL                   # tm_monitor  # noqa: E402
from tmh_scoring.two_layer import (                                   # noqa: E402
    assess, mark_similarity, trade_similarity, needs_enrichment,
    Assessment, MarkSimilarity, TradeSimilarity,
    DEAD_STATUSES, PENDING_STATUSES,
)

#: The version of the scoring package actually loaded. Stamped onto every
#: result we store, so a stored band can always be traced to the code that
#: produced it - until 18 Sep 2026 both version fields were hard-coded
#: strings and nothing recorded which scorer had run.
SCORING_PACKAGE_VERSION = tmh_scoring.__version__

__all__ = [
    "SCORING_PACKAGE_VERSION",
    "derive_search_terms", "goods_similarity", "build_idf",
    "validate_run", "PASS", "WARN", "FAIL",
    "assess", "mark_similarity", "trade_similarity", "needs_enrichment",
    "Assessment", "MarkSimilarity", "TradeSimilarity",
    "DEAD_STATUSES", "PENDING_STATUSES",
]
