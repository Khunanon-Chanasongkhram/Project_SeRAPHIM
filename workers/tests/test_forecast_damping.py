"""The damped rate of rise, and the claims it is allowed to make.

Straight-line extrapolation scored **-72% skill at a 6 hour lead**: worse than predicting
no change at all. These tests pin the shape of the replacement and, more importantly, the
things it must refuse to say.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.forecast import (  # noqa: E402
    LEAD_HOURS,
    RATE_DECAY_HOURS,
    displacement,
    hours_to_level,
    max_displacement,
    project,
)
from seraphim.history import Trend  # noqa: E402

GOOD = Trend(rate_m_per_hr=0.10, r2=0.95, points=8, span_hours=4.0)
STEADY = Trend(rate_m_per_hr=0.001, r2=0.9, points=8, span_hours=4.0)
NOISY = Trend(rate_m_per_hr=0.10, r2=0.05, points=3, span_hours=1.0)


class TestShape(unittest.TestCase):
    def test_short_leads_are_still_essentially_a_straight_line(self):
        # This is what preserves the +12-15% skill at one hour.
        self.assertAlmostEqual(displacement(0.10, 0.5), 0.10 * 0.5, delta=0.005)

    def test_it_always_predicts_less_than_a_straight_line(self):
        for lead in (1, 3, 6, 12, 24):
            self.assertLess(displacement(0.10, lead), 0.10 * lead)

    def test_it_never_exceeds_the_ceiling(self):
        # At absurd leads the exponential underflows and the displacement sits exactly
        # on the asymptote, which is the correct answer rather than a bug.
        ceiling = max_displacement(0.10)
        for lead in (12, 24, 100, 10_000):
            self.assertLessEqual(displacement(0.10, lead), ceiling)
        self.assertLess(displacement(0.10, 12), ceiling)

    def test_the_ceiling_is_rate_times_the_decay_constant(self):
        self.assertAlmostEqual(max_displacement(0.2), 0.2 * RATE_DECAY_HOURS)

    def test_it_is_monotonic_in_lead(self):
        prev = -1.0
        for lead in range(0, 48):
            d = displacement(0.10, lead)
            self.assertGreaterEqual(d, prev)
            prev = d

    def test_a_falling_river_falls(self):
        self.assertLess(displacement(-0.10, 3), 0)

    def test_zero_lead_moves_nothing(self):
        self.assertEqual(displacement(0.10, 0), 0.0)


class TestWhatItRefusesToSay(unittest.TestCase):
    def test_no_projection_without_a_trustworthy_trend(self):
        self.assertIsNone(project(10.0, NOISY, 3))
        self.assertIsNone(project(10.0, None, 3))
        self.assertIsNone(project(None, GOOD, 3))

    def test_a_steady_river_gets_no_projection(self):
        # Its best forecast is its current level, which persistence already gives.
        # Dressing that up as a prediction adds confidence without adding information.
        self.assertEqual(STEADY.confidence, "steady")
        self.assertIsNone(project(10.0, STEADY, 3))

    def test_a_target_beyond_the_ceiling_is_not_reached(self):
        ceiling = max_displacement(0.10)
        self.assertIsNone(hours_to_level(10.0, 10.0 + ceiling * 2, GOOD))

    def test_a_target_just_inside_the_ceiling_is_refused_as_arithmetic(self):
        # The inverse blows up near the asymptote: 99.99% of the way there solves to
        # past the 48 h horizon. That is arithmetic, not a forecast.
        ceiling = max_displacement(0.10)
        self.assertIsNone(hours_to_level(10.0, 10.0 + ceiling * 0.9999, GOOD))

    def test_a_target_inside_the_ceiling_is_reached(self):
        ceiling = max_displacement(0.10)
        hours = hours_to_level(10.0, 10.0 + ceiling * 0.5, GOOD)
        self.assertIsNotNone(hours)
        self.assertGreater(hours, 0)

    def test_a_target_already_passed_is_not_a_forecast(self):
        self.assertIsNone(hours_to_level(10.0, 9.5, GOOD))
        self.assertIsNone(hours_to_level(10.0, 10.0, GOOD))

    def test_a_falling_river_never_reaches_a_level_above_it(self):
        falling = Trend(rate_m_per_hr=-0.10, r2=0.95, points=8, span_hours=4.0)
        self.assertIsNone(hours_to_level(10.0, 10.5, falling))


class TestRoundTrip(unittest.TestCase):
    def test_hours_to_level_inverts_displacement(self):
        for gap_fraction in (0.1, 0.3, 0.5, 0.9):
            gap = max_displacement(0.10) * gap_fraction
            hours = hours_to_level(10.0, 10.0 + gap, GOOD)
            self.assertIsNotNone(hours)
            self.assertAlmostEqual(displacement(0.10, hours), gap, places=6)

    def test_project_agrees_with_displacement(self):
        self.assertAlmostEqual(project(10.0, GOOD, 6), 10.0 + displacement(0.10, 6), places=3)


class TestPublishedLeads(unittest.TestCase):
    def test_leads_stop_where_skill_does(self):
        # Nothing beyond 12 h has measurable skill; the weeks-ahead outlook is a
        # different model built on a different source.
        self.assertEqual(max(LEAD_HOURS), 12.0)
        self.assertEqual(sorted(LEAD_HOURS), list(LEAD_HOURS))


if __name__ == "__main__":
    unittest.main()
