"""A tide is not a trend.

Found 2026-09-16 from a Dutch North Sea platform. `rws:a12` sits in the middle of the
North Sea and measures the tide. Its readings went +0.18, +0.19, +0.36, +0.53 over three
hours, the trend fitter called that "rising 13 cm/h, fair confidence", and the map
published a projected level of **+1.22 m in twelve hours**. The tide was going to turn
within about three.

Two defences, because neither is enough alone:

  * Adapters that KNOW mark the station `tidal`. The US publishes a SHEF code (HT is a
    tidal stage, 195 gauges); the Dutch offshore platforms are the MSL-datum ones.
  * Anything else is caught by counting direction changes once the archive spans a
    tidal cycle. That is the general case, and it needs no per-network knowledge.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.history import (  # noqa: E402
    MIN_REVERSALS_TO_DOUBT,
    REVERSAL_NOISE_M,
    Trend,
    _count_reversals,
    fit_trend,
)
from seraphim.models import Admin, Observation, Station, StationState  # noqa: E402
from seraphim.publish import _level_forecast  # noqa: E402
from seraphim.risk import assess  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
GOOD = Trend(rate_m_per_hr=0.2, r2=0.95, points=8, span_hours=4.0)


def series(values):
    return [(T0 + timedelta(hours=i), v) for i, v in enumerate(values)]


def station(tidal=False, bank=2.0):
    return Station(source="t", external_id="1", name="x", lat=13.4, lon=100.6,
                   bank_msl=bank, tidal=tidal,
                   admin=Admin(country="TH", province="p"))


def state(tidal=False, bank=2.0, level=1.6):
    st = station(tidal, bank)
    ob = Observation(station_id=st.id, observed_at=T0, level_msl=level)
    return StationState(station=st, observation=ob, generated_at=T0)


class TestReversalCounting(unittest.TestCase):
    def test_a_rising_river_never_reverses(self):
        self.assertEqual(_count_reversals([1.0, 1.1, 1.2, 1.3]), 0)

    def test_a_single_turning_point_is_not_oscillation(self):
        # A river that rose then levelled off is still a river.
        self.assertEqual(_count_reversals([1.0, 1.2, 1.4, 1.2]), 1)
        self.assertFalse(fit_trend(series([1.0, 1.2, 1.4, 1.2])).oscillating)

    def test_a_tide_reverses_repeatedly(self):
        self.assertGreaterEqual(
            _count_reversals([1.0, 1.4, 1.6, 1.3, 0.9, 1.2, 1.6]), MIN_REVERSALS_TO_DOUBT)

    def test_jitter_around_a_flat_level_is_not_oscillation(self):
        # Without the noise floor, a still river reads as violent oscillation.
        tiny = REVERSAL_NOISE_M / 2
        self.assertEqual(_count_reversals([1.0, 1.0 + tiny, 1.0 - tiny, 1.0 + tiny]), 0)

    def test_an_oscillating_series_says_so_instead_of_claiming_a_rate(self):
        self.assertEqual(fit_trend(series([1.0, 1.4, 1.6, 1.3, 0.9, 1.2, 1.6])).confidence,
                         "oscillating")

    def test_a_genuine_rise_is_still_trusted(self):
        self.assertEqual(fit_trend(series([1.0, 1.1, 1.2, 1.3, 1.4])).confidence, "good")


class TestDeclaredTidalStations(unittest.TestCase):
    def test_no_projected_level_for_a_tidal_gauge(self):
        trends = {station(tidal=True).id: GOOD}
        self.assertIsNone(_level_forecast(state(tidal=True), trends))

    def test_but_an_ordinary_gauge_still_gets_one(self):
        trends = {station().id: GOOD}
        self.assertIsNotNone(_level_forecast(state(), trends))

    def test_no_time_to_bank_for_a_tidal_gauge(self):
        self.assertIsNone(assess(state(tidal=True), GOOD, [], T0).time_to_bank_hr)

    def test_an_ordinary_gauge_still_gets_a_time_to_bank(self):
        self.assertIsNotNone(assess(state(tidal=False), GOOD, [], T0).time_to_bank_hr)

    def test_a_tidal_gauge_still_produces_a_risk_assessment(self):
        # Refusing to forecast must not refuse to assess: an earlier version of this
        # guard returned None from assess() and would have erased the station.
        r = assess(state(tidal=True), GOOD, [], T0)
        self.assertIsNotNone(r)
        self.assertGreaterEqual(r.level, 1)

    def test_a_tidal_gauge_over_its_bank_is_still_reported_as_over(self):
        # Observation, not prediction. The tide being in does not make the water
        # imaginary.
        r = assess(state(tidal=True, level=2.5), GOOD, [], T0)
        self.assertEqual(r.level, 5)


class TestAdaptersDeclareIt(unittest.TestCase):
    def test_the_us_marks_tidal_stage_gauges(self):
        from seraphim.adapters.nwsriv import HEIGHT_PREFIX, TIDAL_PREFIX
        self.assertTrue(TIDAL_PREFIX.startswith(HEIGHT_PREFIX))
        self.assertTrue("HTIRG".startswith(TIDAL_PREFIX))
        self.assertFalse("HGIRG".startswith(TIDAL_PREFIX))

    def test_the_dutch_offshore_platforms_are_the_msl_ones(self):
        from seraphim.adapters.rws import DATUMS
        self.assertEqual(DATUMS["MSL"], "MSL")
        self.assertEqual(DATUMS["NAP"], "NAP")


if __name__ == "__main__":
    unittest.main()
