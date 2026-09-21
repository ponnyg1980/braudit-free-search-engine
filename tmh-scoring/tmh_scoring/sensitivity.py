"""Labelled sensitivity steps for Triage.

Nobody in Triage types 0.85. They move a named step on one compartment —
"Sounds like: more sensitive" — and this module says what that step means in
terms of `ScoringSettings` fields.

THE POINT OF THE INDIRECTION
----------------------------
Triage picks a step; R&D owns what the step means. Those are different
decisions belonging to different people, and separating them is what makes the
log worth keeping. A run records the STEP it was scored at and the
`scoring_version` in force at the time, so when R&D later re-tunes what "+1"
means, the history does not silently rewrite itself: run 9270f8f3 was scored
at "sounds like +1" under tmh_scoring/2.4.0, and 2.4.0 is on disk saying
exactly what that was.

It also means Triage has no path to a global default. The only thing Triage
can produce is a step per compartment; the only thing this module can produce
is a delta from DEFAULTS for one run. Moving DEFAULTS is a package release
with its own band_diff, which is R&D's, and Jonathan's.

WHAT A STEP MOVES, AND WHAT IT DOES NOT
---------------------------------------
A step moves the GATES of one compartment — the points at which a measurement
becomes a tier. It does not touch:

  * the measurements themselves (edit distance, syllables, Soundex, the
    overlap coefficient, the conflict matrix). Those are the model.
  * `generic_guard_ratio`. That guard is what stops SWIFT COURIERS and RAPID
    COURIERS resembling each other through the word they share. "More
    sensitive" must not mean "reintroduce the false positive the guard was
    written to kill", so the guard sits outside the steps.
  * `token_coverage_for_whole`. A token speaking for a whole mark is a
    structural judgement (SMARTCARD vs SMART), not a sensitivity one.
  * `goods_class_link_score`. It is a fallback SCORE assigned when there is no
    text at all, not a gate, so there is nothing for a step to loosen.
  * any other compartment. A guard at the bottom of this module asserts that
    each compartment's presets only ever set its own fields — that is what
    makes a compartment a compartment, and the reason 2.3.0 had to split
    `mark_similarity`'s tier-3 gate onto the three axes.

STEP 0 IS EXACTLY DEFAULTS
--------------------------
Not approximately. `settings_for({})` and `settings_for({c: 0 for c in
COMPARTMENTS})` both return the base object unchanged, and the module asserts
at import that every compartment's step-0 preset is empty. A "standard" run
must be bit-identical to a run with no sensitivity at all, or the clear path
does not actually restore anything.
"""
from __future__ import annotations

from .settings import COMPARTMENTS, COMPARTMENT_FIELDS, DEFAULTS, ScoringSettings

__all__ = [
    "STEPS",
    "STEP_LABELS",
    "AVAILABLE_STEPS",
    "PRESETS",
    "settings_for",
    "deltas_for",
    "describe",
    "normalise_steps",
    "SensitivityError",
]


class SensitivityError(ValueError):
    """An unknown compartment, or a step outside the allowed range."""


#: The only positions a Triage control can be in. Deliberately five and
#: deliberately small: a slider with thirty positions invites fiddling and
#: produces a log nobody can read.
STEPS = (-2, -1, 0, 1, 2)

STEP_LABELS = {
    -2: "Much stricter",
    -1: "Stricter",
    0: "Standard",
    1: "More sensitive",
    2: "Much more sensitive",
}

#: Not every compartment can offer every step. Where the shipped calibration
#: already sits at the floor or ceiling of what the underlying measure can
#: express, the missing positions are declared here and the control renders
#: them disabled. The alternative — offering a step that scores identically to
#: its neighbour — puts a change in the log that did not happen, and a log
#: nobody can trust is worse than a control with four positions.
#: Derived from PRESETS below, so the two cannot drift.
AVAILABLE_STEPS: dict[str, tuple] = {}

#: What each step does, in the compartment's own terms. `{}` means "no change
#: from the shipped calibration"; step 0 is empty everywhere, by assertion.
#:
#: Written as explicit VALUES rather than arithmetic offsets on purpose. An
#: offset table looks tidier and is unreadable in an audit: "+1 means -0.04"
#: tells nobody what the threshold actually became. These are the numbers, and
#: they can be tuned asymmetrically where the underlying measure is lumpy —
#: see `orthographic`, where they have to be.
PRESETS: dict[str, dict[int, dict]] = {

    # -- classes ------------------------------------------------------------
    # Only reached where one or both specifications are missing. Sensitivity
    # here is "how many shared Nice classes before we call it a trade
    # overlap", and class 35 alone covering advertising, business management
    # and retail is why the standard answer is not 1.
    "classes": {
        -2: {"classes_for_tier3": 5, "classes_for_tier2": 3},
        -1: {"classes_for_tier3": 4, "classes_for_tier2": 2},
        0: {},
        1: {"classes_for_tier3": 2},
        # No +2. The standard already treats ONE shared class as a trade
        # overlap, so there is nowhere below it to go: the only remaining
        # loosening would be to call a single shared class a full tier-3
        # overlap, which class 35 on its own makes indefensible. See
        # AVAILABLE_STEPS — the control is shown with that position disabled
        # rather than offering a step that would score identically to +1 and
        # log as though something had changed.
    },

    # -- terms & descriptions ----------------------------------------------
    # The overlap coefficient between the two specifications. Bands only.
    "terms": {
        -2: {"goods_high_min": 0.70, "goods_related_min": 0.40,
             "goods_slight_min": 0.20},
        -1: {"goods_high_min": 0.65, "goods_related_min": 0.35,
             "goods_slight_min": 0.15},
        0: {},
        1: {"goods_high_min": 0.55, "goods_related_min": 0.25,
            "goods_slight_min": 0.08},
        2: {"goods_high_min": 0.50, "goods_related_min": 0.20,
            "goods_slight_min": 0.05},
    },

    # -- sounds like --------------------------------------------------------
    # `phonetic_strong` is the gate at which sound alone carries a mark to
    # tier 3. `phonetic_syllable_match` is how close two syllables have to be
    # to count as shared, and it moves with it — loosening the gate while
    # holding the syllable test would let long near-misses through without
    # helping the short rhyming pairs the gate is actually for.
    "phonetic": {
        -2: {"phonetic_strong": 0.95, "phonetic_syllable_match": 0.88},
        -1: {"phonetic_strong": 0.93, "phonetic_syllable_match": 0.84},
        0: {},
        1: {"phonetic_strong": 0.86, "phonetic_syllable_match": 0.76},
        2: {"phonetic_strong": 0.82, "phonetic_syllable_match": 0.72},
    },

    # -- spelled like -------------------------------------------------------
    # NOT a smooth scale, and the steps have to respect that. The
    # orthographic score is DISCRETE — one edit scores 0.95, two edits on a
    # long mark 0.90, two on a short one or three on a very long one 0.85,
    # and anything worse falls through to a sequence ratio. So the gate only
    # means something when it lands between those values:
    #
    #     0.96  identical only
    #     0.92  one edit                     (VETSURA / VETSURE)
    #     0.90  + two edits on a long mark   <- standard
    #     0.85  + two on a short mark, three on a very long one
    #     0.80  + whatever the raw ratio gives
    #
    # Setting 0.86 or 0.88 here would change nothing at all while appearing in
    # the log as a change, which is worse than not offering the step.
    "orthographic": {
        -2: {"orthographic_strong": 0.96},
        -1: {"orthographic_strong": 0.92},
        0: {},
        1: {"orthographic_strong": 0.85},
        2: {"orthographic_strong": 0.80},
    },

    # -- looks/reads alike (written form) -----------------------------------
    # The written-form axis and the v1 difflib cutoffs, which measure the same
    # thing for the older scorer and must move together or the two scorers
    # disagree about the same pair of marks within one report.
    "fuzzy": {
        -2: {"written_strong": 0.95, "similar_min": 0.86, "slight_min": 0.70,
             "token_strong": 0.95, "fuzzy_strong_ratio": 0.91,
             "fuzzy_weak_ratio": 0.86},
        -1: {"written_strong": 0.93, "similar_min": 0.82, "slight_min": 0.66,
             "token_strong": 0.93, "fuzzy_strong_ratio": 0.88,
             "fuzzy_weak_ratio": 0.82},
        0: {},
        1: {"written_strong": 0.87, "similar_min": 0.74, "slight_min": 0.58,
            "token_strong": 0.87, "fuzzy_strong_ratio": 0.82,
            "fuzzy_weak_ratio": 0.74},
        2: {"written_strong": 0.84, "similar_min": 0.70, "slight_min": 0.54,
            "token_strong": 0.84, "fuzzy_strong_ratio": 0.79,
            "fuzzy_weak_ratio": 0.70},
    },

    # -- image similarity ---------------------------------------------------
    # Present so the compartment set is complete and the panel can show it
    # disabled. The image scorer is not wired into any run yet (phase 2), so
    # these steps currently move nothing. The CLIP floors are the ones
    # calibrated on real client trademark images; widening them is the step
    # most likely to need re-measuring once the scorer is live, because the
    # synthetic-shape sweep is known to mislead in exactly this region.
    "visual": {
        -2: {"clip_identical_min": 0.95, "clip_similar_min": 0.90,
             "clip_weak_min": 0.84, "phash_identical_max_distance": 2},
        -1: {"clip_identical_min": 0.94, "clip_similar_min": 0.88,
             "clip_weak_min": 0.81, "phash_identical_max_distance": 3},
        0: {},
        1: {"clip_identical_min": 0.90, "clip_similar_min": 0.82,
            "clip_weak_min": 0.75, "phash_identical_max_distance": 6},
        2: {"clip_identical_min": 0.88, "clip_similar_min": 0.79,
            "clip_weak_min": 0.72, "phash_identical_max_distance": 8},
    },
}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def normalise_steps(steps: dict | None) -> dict:
    """Validate a {compartment: step} mapping and drop the no-ops.

    Raises on an unknown compartment or an out-of-range step rather than
    ignoring it: a typo that silently scores at standard and logs a change is
    the one failure this whole design exists to prevent.
    """
    out = {}
    for comp, step in (steps or {}).items():
        if comp not in PRESETS:
            raise SensitivityError(
                f"unknown compartment {comp!r}; expected one of {list(COMPARTMENTS)}")
        try:
            step = int(step)
        except (TypeError, ValueError):
            raise SensitivityError(f"{comp}: step must be an integer, got {step!r}")
        if step not in STEPS:
            raise SensitivityError(f"{comp}: step {step} outside {STEPS}")
        if step not in AVAILABLE_STEPS[comp]:
            raise SensitivityError(
                f"{comp}: step {step} is not offered for this compartment "
                f"(available: {AVAILABLE_STEPS[comp]}) — the shipped "
                f"calibration is already at the limit of what this measure "
                f"can express in that direction")
        if step != 0:
            out[comp] = step
    return out


def deltas_for(steps: dict | None) -> dict:
    """The `ScoringSettings` fields these steps change, and their new values.

    This is what gets written to the audit log alongside the steps themselves:
    the steps say what a human chose, the deltas say what the scorer actually
    did, and a release that re-tunes a step can be read against both.
    """
    fields: dict = {}
    for comp, step in normalise_steps(steps).items():
        fields.update(PRESETS[comp][step])
    return fields


def settings_for(steps: dict | None,
                 base: ScoringSettings | None = None) -> ScoringSettings:
    """Build the `ScoringSettings` for a run from its Triage steps.

    `base` defaults to the shipped calibration. No steps, or all steps at 0,
    returns `base` itself — not a copy with the same values, the same object —
    so "cleared" and "never set" cannot possibly diverge.
    """
    base = base if base is not None else DEFAULTS
    fields = deltas_for(steps)
    return base.replace(**fields) if fields else base


def describe(steps: dict | None) -> list[str]:
    """One plain line per non-standard compartment, for the UI and the log."""
    from .settings import COMPARTMENT_LABELS

    out = []
    for comp, step in normalise_steps(steps).items():
        changed = ", ".join(f"{k} {v}" for k, v in sorted(PRESETS[comp][step].items()))
        out.append(f"{COMPARTMENT_LABELS.get(comp, comp)}: "
                   f"{STEP_LABELS[step]} ({changed})")
    return out


# ---------------------------------------------------------------------------
# Guards — checked at import, because a broken preset table must not reach a
# client report and there is no cheaper place to find out.
# ---------------------------------------------------------------------------

def _validate() -> None:
    valid = {f for fields in COMPARTMENT_FIELDS.values() for f in fields}
    for comp, table in PRESETS.items():
        if comp not in COMPARTMENT_FIELDS:
            raise SensitivityError(f"preset for unknown compartment {comp!r}")
        if 0 not in table:
            raise SensitivityError(f"{comp}: presets must include step 0")
        if not set(table) <= set(STEPS):
            raise SensitivityError(
                f"{comp}: presets outside {STEPS}: {sorted(set(table) - set(STEPS))}")
        AVAILABLE_STEPS[comp] = tuple(sorted(table))
        if table[0]:
            raise SensitivityError(
                f"{comp}: step 0 must be empty — standard has to be identical "
                f"to no sensitivity at all, or clearing restores nothing")
        own = set(COMPARTMENT_FIELDS[comp])
        for step, fields in table.items():
            stray = set(fields) - own
            if stray:
                raise SensitivityError(
                    f"{comp} step {step} sets field(s) belonging to another "
                    f"compartment: {sorted(stray)}")
            unknown = set(fields) - valid
            if unknown:
                raise SensitivityError(
                    f"{comp} step {step} sets unknown field(s): {sorted(unknown)}")
            # Catches a preset that was left equal to the default after a
            # calibration change moved DEFAULTS underneath it.
            unchanged = [k for k, v in fields.items() if getattr(DEFAULTS, k) == v]
            if unchanged:
                raise SensitivityError(
                    f"{comp} step {step} sets {unchanged} to the default value — "
                    f"it would log as a change and score as standard")
    missing = set(COMPARTMENTS) - set(PRESETS)
    if missing:
        raise SensitivityError(f"no presets for compartment(s): {sorted(missing)}")


_validate()
