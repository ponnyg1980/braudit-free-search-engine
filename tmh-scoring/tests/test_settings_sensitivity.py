"""ScoringSettings and the sensitivity step table (2.3.0 / 2.4.0).

The property that matters most here is NOT that a step does something clever.
It is that doing nothing does nothing: a run with no settings, a run with
DEFAULTS, and a run with every compartment at step 0 must all produce byte-
identical results, or the Triage "clear" path does not actually restore
anything and the whole design is unsafe.
"""
import unittest

from tmh_scoring import two_layer, word_scoring
from tmh_scoring.goods_similarity import goods_similarity
from tmh_scoring.risk_model import assess_risk
from tmh_scoring.settings import (COMPARTMENT_FIELDS, COMPARTMENT_LABELS,
                                  COMPARTMENTS, DEFAULTS, ScoringSettings,
                                  active, resolve, use_settings)
from tmh_scoring.sensitivity import (AVAILABLE_STEPS, PRESETS, STEPS,
                                     SensitivityError, deltas_for, describe,
                                     normalise_steps, settings_for)

# A pair that is a near-miss on spelling, which is where the discrete
# orthographic ladder makes the gate actually mean something.
PAIR = dict(client_mark="VETSURE", cited_mark="VETSURA",
            client_classes=[44], cited_classes=[44], status="Registered")


class TestDefaultsAreInert(unittest.TestCase):
    def test_defaults_differ_from_nothing(self):
        self.assertEqual(DEFAULTS.diff_from_defaults(), {})

    def test_no_settings_equals_defaults(self):
        a = two_layer.assess(**PAIR)
        b = two_layer.assess(settings=DEFAULTS, **PAIR)
        c = two_layer.assess(settings=settings_for(None), **PAIR)
        d = two_layer.assess(settings=settings_for({k: 0 for k in COMPARTMENTS}), **PAIR)
        for other in (b, c, d):
            self.assertEqual(a.priority, other.priority)
            self.assertEqual(a.conflict, other.conflict)
            self.assertEqual(a.rights, other.rights)
            self.assertEqual(a.mark.tier, other.mark.tier)
            self.assertEqual(a.mark.reason, other.mark.reason)
            self.assertEqual(a.trade.tier, other.trade.tier)

    def test_step_zero_returns_the_base_object_itself(self):
        # Not an equal copy — the same object. "Cleared" and "never set"
        # cannot be allowed to diverge even by a float repr.
        self.assertIs(settings_for(None), DEFAULTS)
        self.assertIs(settings_for({}), DEFAULTS)
        self.assertIs(settings_for({c: 0 for c in COMPARTMENTS}), DEFAULTS)

    def test_word_scorer_unchanged_by_defaults(self):
        result = {"status": "Registered", "mark_text": "MOMENTUM MORTGAGE",
                  "mark_type": "Word", "classes": "36"}
        criteria = {"word_searches": [{"type": "Similar To", "phrase": "MOMENTUS"}],
                    "client_classes": [36]}
        a = word_scoring.score_word_result(result, criteria)
        b = word_scoring.score_word_result(result, criteria, DEFAULTS)
        self.assertEqual(a, b)


class TestSettingsActuallyReachTheScorer(unittest.TestCase):
    def test_strict_spelling_gate_drops_the_tier(self):
        loose = two_layer.assess(**PAIR)
        strict = two_layer.assess(
            settings=DEFAULTS.replace(orthographic_strong=0.99,
                                      written_strong=0.99,
                                      phonetic_strong=0.99),
            **PAIR)
        self.assertEqual(loose.mark.tier, 3)
        self.assertLess(strict.mark.tier, loose.mark.tier)

    def test_v1_band_floor_moves(self):
        self.assertEqual(word_scoring.risk_from_score(6, "Registered"), "Low/Medium")
        self.assertEqual(
            word_scoring.risk_from_score(6, "Registered",
                                         DEFAULTS.replace(word_low_medium_min=7)),
            "Low")

    def test_goods_bands_move(self):
        a = goods_similarity("bakery goods; bread; cakes", "bread; cakes; pastries")
        b = goods_similarity("bakery goods; bread; cakes", "bread; cakes; pastries",
                             settings=DEFAULTS.replace(goods_high_min=0.99,
                                                       goods_related_min=0.98,
                                                       goods_slight_min=0.97))
        self.assertNotEqual(a.band, b.band)

    def test_assess_risk_takes_settings(self):
        kw = dict(status="Registered", similarity_points=2,
                  client_mark="SWIFT COURIERS", cited_mark="RAPID COURIERS",
                  shared_classes=[39], goods_band="related")
        self.assertEqual(assess_risk(**kw).band, assess_risk(settings=DEFAULTS, **kw).band)


class TestPropagation(unittest.TestCase):
    def test_active_defaults_to_defaults(self):
        self.assertIs(active(), DEFAULTS)

    def test_resolve_prefers_the_explicit_argument(self):
        s = DEFAULTS.replace(phonetic_strong=0.5)
        self.assertIs(resolve(s), s)
        self.assertIs(resolve(None), DEFAULTS)

    def test_use_settings_restores_on_exception(self):
        s = DEFAULTS.replace(phonetic_strong=0.5)
        with self.assertRaises(RuntimeError):
            with use_settings(s):
                self.assertIs(active(), s)
                raise RuntimeError("boom")
        self.assertIs(active(), DEFAULTS)

    def test_use_settings_nests(self):
        a = DEFAULTS.replace(phonetic_strong=0.5)
        b = DEFAULTS.replace(phonetic_strong=0.6)
        with use_settings(a):
            with use_settings(b):
                self.assertIs(active(), b)
            self.assertIs(active(), a)
        self.assertIs(active(), DEFAULTS)


class TestReplaceValidates(unittest.TestCase):
    def test_unknown_field_raises(self):
        with self.assertRaises(ValueError):
            DEFAULTS.replace(sensitivity_of_vibes=1.0)

    def test_diff_reports_only_what_moved(self):
        s = DEFAULTS.replace(phonetic_strong=0.86)
        self.assertEqual(s.diff_from_defaults(), {"phonetic_strong": 0.86})


class TestSensitivityTable(unittest.TestCase):
    def test_every_compartment_has_presets(self):
        self.assertEqual(set(PRESETS), set(COMPARTMENTS))
        self.assertEqual(set(COMPARTMENT_LABELS), set(COMPARTMENTS))

    def test_presets_only_touch_their_own_compartment(self):
        for comp, table in PRESETS.items():
            own = set(COMPARTMENT_FIELDS[comp])
            for step, fields in table.items():
                self.assertTrue(set(fields) <= own,
                                f"{comp} {step:+d} reaches outside its compartment")

    def test_no_preset_equals_the_default(self):
        # A step that sets a field to the value it already has would log as a
        # change and score as standard.
        for comp, table in PRESETS.items():
            for step, fields in table.items():
                for k, v in fields.items():
                    self.assertNotEqual(getattr(DEFAULTS, k), v,
                                        f"{comp} {step:+d}: {k} is already {v}")

    def test_available_steps_derived_and_within_range(self):
        for comp, steps in AVAILABLE_STEPS.items():
            self.assertIn(0, steps)
            self.assertTrue(set(steps) <= set(STEPS))
            self.assertEqual(set(steps), set(PRESETS[comp]))

    def test_classes_offers_no_plus_two(self):
        # One shared class is already treated as a trade overlap, so there is
        # nowhere below it to go. Documented, asserted, not quietly offered.
        self.assertNotIn(2, AVAILABLE_STEPS["classes"])

    def test_monotonic_direction(self):
        # More sensitive must not be stricter than standard on any gate it
        # touches, and vice versa. Catches a transposed preset row.
        lower_is_looser = {"classes_for_tier3", "classes_for_tier2",
                           "goods_high_min", "goods_related_min",
                           "goods_slight_min", "phonetic_strong",
                           "phonetic_syllable_match", "orthographic_strong",
                           "written_strong", "similar_min", "slight_min",
                           "token_strong", "fuzzy_strong_ratio",
                           "fuzzy_weak_ratio", "clip_identical_min",
                           "clip_similar_min", "clip_weak_min"}
        for comp, table in PRESETS.items():
            for step, fields in table.items():
                for k, v in fields.items():
                    base = getattr(DEFAULTS, k)
                    if k in lower_is_looser:
                        looser = v < base
                    else:                       # phash distance: higher is looser
                        looser = v > base
                    self.assertEqual(looser, step > 0,
                                     f"{comp} {step:+d}: {k} {base} -> {v} "
                                     f"moves the wrong way")


class TestSensitivityApi(unittest.TestCase):
    def test_deltas_match_the_resulting_settings(self):
        steps = {"phonetic": 1, "orthographic": 2}
        self.assertEqual(deltas_for(steps),
                         settings_for(steps).diff_from_defaults())

    def test_unknown_compartment_raises(self):
        with self.assertRaises(SensitivityError):
            normalise_steps({"vibes": 1})

    def test_out_of_range_step_raises(self):
        with self.assertRaises(SensitivityError):
            normalise_steps({"phonetic": 3})

    def test_unavailable_step_raises(self):
        with self.assertRaises(SensitivityError):
            normalise_steps({"classes": 2})

    def test_non_integer_step_raises(self):
        with self.assertRaises(SensitivityError):
            normalise_steps({"phonetic": "loose"})

    def test_zero_steps_are_dropped(self):
        self.assertEqual(normalise_steps({"phonetic": 0, "fuzzy": 1}), {"fuzzy": 1})

    def test_describe_names_the_compartment_in_staff_words(self):
        lines = describe({"phonetic": 1})
        self.assertEqual(len(lines), 1)
        self.assertIn("Sounds like", lines[0])
        self.assertIn("More sensitive", lines[0])

    def test_describe_is_silent_at_standard(self):
        self.assertEqual(describe({"phonetic": 0}), [])
        self.assertEqual(describe(None), [])


if __name__ == "__main__":
    unittest.main()
