"""Per-run scoring settings.

Every threshold the scorers consult is declared once, here, as a field on a
frozen `ScoringSettings`. The defaults are imported from `bands.py` where a
constant already existed, so `bands.py` remains the single source of truth for
the shipped calibration and this module adds no second copy of any number.

WHY THIS EXISTS
---------------
Triage needs to ask "what would this run look like if the sound axis were a
little less fussy?" without changing what every other run scores, and without
anybody in Triage touching a global default. Before 2.3.0 that was impossible:
the thresholds were module-level constants, so the only ways to vary them were
to edit the package (changing every run, past and future) or to re-score in a
subprocess with a patched module (which throws away the per-comparison
breakdown the Triage panel exists to show).

So: the scorers now read their thresholds from a settings object rather than
from module globals. Pass nothing and you get `DEFAULTS`, which is built from
the same constants the module globals were, so behaviour is unchanged — that
is the property `tools/band_diff.py` proves before this release ships.

HOW IT PROPAGATES
-----------------
Public entry points take `settings=`. Internally the active settings travel in
a `contextvars.ContextVar`, set for the duration of the call:

    from tmh_scoring.settings import ScoringSettings, active

    assess(..., settings=ScoringSettings(phonetic_strong=0.86))

The contextvar is the propagation mechanism, not the API. It is used instead
of threading an extra argument through forty private helpers for one reason:
a helper that forgets to pass settings on inherits the caller's settings,
which is correct. A helper that forgets an explicit argument silently reverts
to the defaults, which is a wrong answer that looks right. The failure mode
had to be the safe one — this is scoring code and a client report is the
output.

Contextvars are per-thread and per-task, so concurrent runs with different
settings cannot see each other's.

WHAT IS AND IS NOT A KNOB
-------------------------
Fields here are thresholds and gates — the points at which a measurement
becomes a tier. The MEASUREMENTS themselves (edit distance, syllable
splitting, the overlap coefficient, Soundex) are not configurable, and neither
is the conflict matrix in `risk_model.py`. Those are the model. Changing them
is a package release with a band_diff, not a slider in Triage.

COMPARTMENTS
------------
Fields are grouped into the six compartments staff see in the Triage panel
(Jonathan, 20 Sep 2026): classes, terms, phonetic ("sounds like"), visual
("looks like"), orthographic ("spelled like"), and fuzzy (written-form
resemblance). `COMPARTMENT_FIELDS` maps each to the fields it owns; 2.4.0's
`sensitivity.py` uses that map to turn a labelled step into a settings delta.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace

from .bands import (
    CLIP_IDENTICAL_MIN,
    CLIP_SIMILAR_MIN,
    CLIP_WEAK_MIN,
    FUZZY_STRONG_RATIO,
    FUZZY_WEAK_RATIO,
    PHASH_IDENTICAL_MAX_DISTANCE,
    PHASH_MAX_COLOUR_DISTANCE,
    SIM_CONTAINS,
    SIM_EXACT,
    SIM_FUZZY_STRONG,
    SIM_FUZZY_WEAK,
    SIM_STARTS_WITH,
    WORD_HIGH_MIN,
    WORD_MEDIUM_MIN,
)

__all__ = [
    "ScoringSettings",
    "DEFAULTS",
    "COMPARTMENTS",
    "COMPARTMENT_FIELDS",
    "COMPARTMENT_LABELS",
    "active",
    "resolve",
    "use_settings",
]


@dataclass(frozen=True)
class ScoringSettings:
    """Thresholds for one scoring run. Immutable; use `replace()` to derive."""

    # -- fuzzy: written-form resemblance between the two marks -------------
    # The tier gates in two_layer.mark_similarity. `written_strong` is the
    # 0.90 that carries a mark to tier 3 on the written axis; `similar_min`
    # and `slight_min` are the tier-2 and tier-1 floors.
    written_strong: float = 0.90
    similar_min: float = 0.78
    slight_min: float = 0.62
    # A shared token speaks for the whole mark only above this ratio AND only
    # when it covers this share of BOTH marks' letters (SMARTCARD/SMART).
    token_strong: float = 0.90
    token_coverage_for_whole: float = 0.60
    # v1 word_scoring difflib cutoffs (SIM_FUZZY_STRONG / _WEAK awards).
    fuzzy_strong_ratio: float = FUZZY_STRONG_RATIO
    fuzzy_weak_ratio: float = FUZZY_WEAK_RATIO
    # risk_model's generic-vocabulary guard: below this ratio between the two
    # marks' DISTINCTIVE cores, a measured resemblance is treated as coming
    # from shared trade wording (SWIFT COURIERS / RAPID COURIERS) and capped.
    generic_guard_ratio: float = 0.60

    # -- phonetic: "sounds like" -------------------------------------------
    # Gate at which phonetic_score carries a mark to tier 3 on its own.
    phonetic_strong: float = 0.90
    # Syllable-level agreement counted as "shared" inside phonetic_score.
    phonetic_syllable_match: float = 0.80
    # Damping applied outside the 2-3 syllable window where the ear actually
    # confuses two brands. Four syllables, then five-plus.
    phonetic_damp_4: float = 0.75
    phonetic_damp_5plus: float = 0.60
    # Marks of very different spoken shape are damped again.
    phonetic_damp_shape: float = 0.80
    # Length guard on raw Soundex equality (retained; see two_layer).
    soundex_min_length_ratio: float = 0.70

    # -- orthographic: "spelled like" --------------------------------------
    # Gate at which the spelling axis carries a mark to tier 3.
    orthographic_strong: float = 0.90
    # Scores awarded by edit count. One slip is one slip whatever the length.
    ortho_one_edit: float = 0.95
    ortho_two_edits_long: float = 0.90
    ortho_two_edits_short: float = 0.85
    ortho_three_edits_long: float = 0.85
    # "Long" for the two- and three-edit rules above.
    ortho_long_min_chars: int = 8
    ortho_very_long_min_chars: int = 12

    # -- classes: Nice-class overlap, used where specifications are missing -
    classes_for_tier3: int = 3
    classes_for_tier2: int = 1

    # -- terms: specification / goods-description overlap ------------------
    goods_high_min: float = 0.60
    goods_related_min: float = 0.30
    goods_slight_min: float = 0.10
    # Score assigned when the classes are related but there is no usable text.
    goods_class_link_score: float = 0.35

    # -- visual: "looks like" ----------------------------------------------
    phash_identical_max_distance: int = PHASH_IDENTICAL_MAX_DISTANCE
    phash_max_colour_distance: float = PHASH_MAX_COLOUR_DISTANCE
    clip_identical_min: float = CLIP_IDENTICAL_MIN
    clip_similar_min: float = CLIP_SIMILAR_MIN
    clip_weak_min: float = CLIP_WEAK_MIN

    # -- banding: v1 word_scoring totals (not a Triage compartment) --------
    word_high_min: int = WORD_HIGH_MIN
    word_medium_min: int = WORD_MEDIUM_MIN
    # The Low/Medium floor in risk_from_score. Was a bare literal 6 in the
    # function body until 2.3.0; named here so it is visible alongside the
    # other two rather than hiding inside an `if`.
    word_low_medium_min: int = 6
    sim_exact: int = SIM_EXACT
    sim_starts_with: int = SIM_STARTS_WITH
    sim_contains: int = SIM_CONTAINS
    sim_fuzzy_strong: int = SIM_FUZZY_STRONG
    sim_fuzzy_weak: int = SIM_FUZZY_WEAK

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def diff_from_defaults(self) -> dict:
        """Only the fields that differ from the shipped calibration.

        This is what gets written to the audit log — recording forty unchanged
        numbers on every run would bury the one that was actually moved.
        """
        return {f.name: getattr(self, f.name)
                for f in fields(self)
                if getattr(self, f.name) != getattr(DEFAULTS, f.name)}

    def replace(self, **kw) -> "ScoringSettings":
        unknown = set(kw) - {f.name for f in fields(self)}
        if unknown:
            raise ValueError(f"unknown scoring setting(s): {sorted(unknown)}")
        return replace(self, **kw)


#: The shipped calibration. Passing no settings anywhere gets exactly this.
DEFAULTS = ScoringSettings()


# ---------------------------------------------------------------------------
# Compartments
# ---------------------------------------------------------------------------
# The grouping staff see. Order is the order of the Triage panel.

COMPARTMENT_FIELDS: dict[str, tuple[str, ...]] = {
    "classes": ("classes_for_tier3", "classes_for_tier2"),
    "terms": ("goods_high_min", "goods_related_min", "goods_slight_min",
              "goods_class_link_score"),
    "phonetic": ("phonetic_strong", "phonetic_syllable_match",
                 "phonetic_damp_4", "phonetic_damp_5plus",
                 "phonetic_damp_shape", "soundex_min_length_ratio"),
    "orthographic": ("orthographic_strong", "ortho_one_edit",
                     "ortho_two_edits_long", "ortho_two_edits_short",
                     "ortho_three_edits_long", "ortho_long_min_chars",
                     "ortho_very_long_min_chars"),
    "fuzzy": ("written_strong", "similar_min", "slight_min", "token_strong",
              "token_coverage_for_whole", "fuzzy_strong_ratio",
              "fuzzy_weak_ratio", "generic_guard_ratio"),
    "visual": ("phash_identical_max_distance", "phash_max_colour_distance",
               "clip_identical_min", "clip_similar_min", "clip_weak_min"),
}

COMPARTMENTS = tuple(COMPARTMENT_FIELDS)

#: What each compartment is called in front of staff and clients.
COMPARTMENT_LABELS = {
    "classes": "Classes",
    "terms": "Terms & descriptions",
    "phonetic": "Sounds like",
    "orthographic": "Spelled like",
    "fuzzy": "Looks/reads alike",
    "visual": "Image similarity",
}


# ---------------------------------------------------------------------------
# Active settings
# ---------------------------------------------------------------------------

_active: contextvars.ContextVar[ScoringSettings] = contextvars.ContextVar(
    "tmh_scoring_settings", default=DEFAULTS
)


def active() -> ScoringSettings:
    """The settings in force for the current call. `DEFAULTS` if none set."""
    return _active.get()


def resolve(settings: ScoringSettings | None) -> ScoringSettings:
    """What a public entry point should use given its `settings=` argument.

    An explicit argument wins; otherwise the caller's active settings, which
    are `DEFAULTS` unless someone deliberately set them.
    """
    return settings if settings is not None else _active.get()


@contextmanager
def use_settings(settings: ScoringSettings | None):
    """Make `settings` active for the duration of the block.

    Re-entrant: the previous value is restored on exit, including on an
    exception, so a run that raises mid-scoring cannot leak its overrides into
    the next one.
    """
    if settings is None:
        yield _active.get()
        return
    token = _active.set(settings)
    try:
        yield settings
    finally:
        _active.reset(token)
