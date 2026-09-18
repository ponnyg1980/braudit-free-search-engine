"""The jurisdiction guard: a value we could not READ must never pass as a
country we cannot REACH.

Regression cover for the defect of 16-17 Sep 2026, when nine runs searched
WIPO instead of the register the Deal asked for because the display label
"United Kingdom" arrived where the ISO code GB was expected, and WIPO — the
documented backup for a country with no direct feed — silently absorbed it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from audit_engine import runner as runner_mod          # noqa: E402
from audit_engine import sources                        # noqa: E402
from audit_engine.runner import AuditRequest, AuditResult  # noqa: E402


class _Row:
    mark_text = "Nela"


def _canaried(jurisdictions):
    req = AuditRequest(client_name="t", mark_text="Nela", classes=[9],
                       jurisdictions=jurisdictions)
    res = AuditResult(request=req, criteria=None,
                      sources=sources.resolve(jurisdictions))
    res.rows = [_Row(), _Row()]
    runner_mod._canary(res)
    return res


class TestUnrecognisedJurisdictions(unittest.TestCase):

    def test_display_names_are_flagged(self):
        self.assertEqual(sources.unrecognised(["United Kingdom"]), ["United Kingdom"])
        self.assertEqual(sources.unrecognised(["EU (EUIPO)", "United States"]),
                         ["EU (EUIPO)", "United States"])

    def test_real_codes_are_not_flagged(self):
        # GB/EM/US have direct offices; JP has none and is reached through
        # Madrid — legitimate WIPO coverage, not a fault.
        for codes in (["GB"], ["EM", "GB", "US"], ["JP"], ["WO"], []):
            self.assertEqual(sources.unrecognised(codes), [], codes)

    def test_run_is_held_when_nothing_resolved(self):
        res = _canaried(["United Kingdom"])
        self.assertEqual([s.label for s in res.sources], ["WIPO (Signa)"])
        self.assertTrue(res.held)
        self.assertIn("jurisdiction not recognised", res.hold_reason)
        self.assertIn("United Kingdom", res.hold_reason)

    def test_good_codes_are_not_held(self):
        self.assertFalse(_canaried(["GB"]).held)
        self.assertFalse(_canaried(["EM", "GB", "US"]).held)

    def test_madrid_only_country_is_not_held(self):
        """WIPO alone is correct when the country genuinely has no direct
        feed — the guard must not fire on that."""
        res = _canaried(["JP"])
        self.assertEqual([s.label for s in res.sources], ["WIPO (Signa)"])
        self.assertFalse(res.held)


if __name__ == "__main__":
    unittest.main()
