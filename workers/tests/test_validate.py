"""Tests for backtesting and the confidence tiers it corrected.

The backtest is the only thing in the project that checks whether the predictions are
true, so a bug here would hide exactly the problem it exists to find. These tests use
series with known answers.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.history import STEADY_RATE_M_PER_HR, Trend, fit_trend  # noqa: E402
from seraphim.validate import (  # noqa: E402
    MAX_BRACKET_HOURS,
    MOVING_THRESHOLD_M,
    _actual_at,
    backtest,
    load_timelines,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)


class TestSteadyTier(unittest.TestCase):
    """Added because backtesting showed 'poor' predicting better than 'fair'.

    Flat rivers have a low R2 because there is no signal, not because the fit is bad,
    and they are trivially easy to predict. They were inflating the lower tiers.
    """

    def _trend(self, rate, r2=0.9, points=8, span=4.0):
        return Trend(rate_m_per_hr=rate, r2=r2, points=points, span_hours=span)

    def test_near_zero_rate_is_steady(self):
        self.assertEqual(self._trend(0.0).confidence, "steady")
        self.assertEqual(self._trend(0.004).confidence, "steady")
        self.assertEqual(self._trend(-0.004).confidence, "steady")

    def test_real_movement_is_not_steady(self):
        self.assertNotEqual(self._trend(0.05).confidence, "steady")
        self.assertNotEqual(self._trend(-0.05).confidence, "steady")

    def test_threshold_matches_time_to_bank(self):
        """The two must agree on what counts as movement, or a station could be
        'steady' and still be given a time-to-bank."""
        from seraphim.risk import MIN_RATE_M_PER_HR

        self.assertEqual(STEADY_RATE_M_PER_HR, MIN_RATE_M_PER_HR)

    def test_a_steady_station_never_gets_a_time_to_bank(self):
        from seraphim.risk import time_to_bank

        self.assertIsNone(time_to_bank(1.0, self._trend(0.004)))

    def test_moving_tiers_still_work(self):
        self.assertEqual(self._trend(0.2, r2=0.9, points=8, span=4).confidence, "good")
        self.assertEqual(self._trend(0.2, r2=0.5, points=4, span=1).confidence, "fair")
        self.assertEqual(self._trend(0.2, r2=0.1, points=4, span=1).confidence, "poor")


class TestInterpolation(unittest.TestCase):
    """Snapping to the nearest reading silently biased the whole comparison: 738 of the
    real observation gaps are exactly 180 minutes, so origin+3h always landed on a
    reading and origin+2h never did, and each lead ended up measured on a different
    population of stations."""

    def series(self, gaps_h, start=0.0, step=0.1):
        out, t, v = [], T0, start
        for g in gaps_h:
            out.append((t, v))
            t += timedelta(hours=g)
            v += step * g
        out.append((t, v))
        return out

    def test_interpolates_between_readings(self):
        s = [(T0, 1.0), (T0 + timedelta(hours=2), 3.0)]
        self.assertAlmostEqual(_actual_at(s, T0 + timedelta(hours=1)), 2.0)

    def test_exact_hit(self):
        s = [(T0, 1.0), (T0 + timedelta(hours=2), 3.0)]
        self.assertAlmostEqual(_actual_at(s, T0), 1.0)

    def test_outside_the_series_is_none(self):
        s = [(T0, 1.0), (T0 + timedelta(hours=2), 3.0)]
        self.assertIsNone(_actual_at(s, T0 - timedelta(hours=1)))
        self.assertIsNone(_actual_at(s, T0 + timedelta(hours=3)))

    def test_refuses_to_bridge_a_long_gap(self):
        """A straight line across many hours of missing data is a guess, not data."""
        s = [(T0, 1.0), (T0 + timedelta(hours=MAX_BRACKET_HOURS + 2), 5.0)]
        self.assertIsNone(_actual_at(s, T0 + timedelta(hours=1)))

    def test_three_hourly_series_now_scores_two_hour_leads(self):
        s = self.series([3, 3, 3, 3])
        self.assertIsNotNone(_actual_at(s, T0 + timedelta(hours=2)))


def write_archive(root: Path, station: str, points, bank=10.0):
    for ts, level in points:
        p = root / f"{ts:%Y/%m/%d}" / f"{ts:%H%M}.json.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if p.exists():
            existing = json.loads(gzip.open(p, "rb").read())["features"]
        existing.append({"properties": {"id": station, "level_msl": level,
                                        "bank_msl": bank, "observed_at": ts.isoformat()}})
        p.write_bytes(gzip.compress(json.dumps({"features": existing}).encode()))


class TestBacktest(unittest.TestCase):
    def test_a_moving_river_still_beats_standing_still(self):
        """The model deliberately under-shoots a perfectly straight line.

        It damps the measured rate, so on a synthetic river that rises forever at
        exactly 0.2 m/hr it will predict low. That is the whole point: real rivers do
        not do this, and assuming they do scored -72% skill at a 6 hour lead. What must
        still hold is that predicting a rise beats predicting no change at all.
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pts = [(T0 + timedelta(hours=i), 1.0 + 0.2 * i) for i in range(12)]
            write_archive(root, "t:1", pts)
            r = backtest(root)
            self.assertGreater(r["predictions_scored"], 0)
            for lead in r["by_lead"]:
                self.assertTrue(lead["beats_persistence"],
                                "on a moving river the trend must beat standing still")
                self.assertLess(lead["median_error_m"], lead["median_persistence_error_m"])

    def test_a_flat_river_cannot_beat_persistence(self):
        """Persistence is exactly right on a flat river, so the trend can only add
        noise. Reporting that honestly is the point of the baseline."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pts = [(T0 + timedelta(hours=i), 5.0) for i in range(12)]
            write_archive(root, "t:1", pts)
            r = backtest(root)
            for lead in r["by_lead"]:
                self.assertLessEqual(lead["median_error_m"], 0.01)

    def test_moving_subset_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pts = [(T0 + timedelta(hours=i), 1.0 + 0.2 * i) for i in range(12)]
            write_archive(root, "t:1", pts)
            r = backtest(root)
            self.assertIn("moving_only", r["by_lead"][0])
            self.assertIsNotNone(r["by_lead"][0]["moving_only"])

    def test_every_eligible_origin_is_used(self):
        """No hand-picked cut points: the count must match what the series allows."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            n = 10
            pts = [(T0 + timedelta(hours=i), 1.0 + 0.2 * i) for i in range(n)]
            write_archive(root, "t:1", pts)
            r = backtest(root)
            one_hour = next(x for x in r["by_lead"] if x["lead_hours"] == 1.0)
            # origins are indices 4..n-1 whose target still falls inside the series
            self.assertEqual(one_hour["n"], n - 1 - 4)

    def test_report_carries_its_own_caveat(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pts = [(T0 + timedelta(hours=i), 1.0 + 0.1 * i) for i in range(10)]
            write_archive(root, "t:1", pts)
            r = backtest(root)
            self.assertIn("hydrolog", r["caveat"].lower())
            self.assertIn("persistence", r["method"].lower())

    def test_empty_archive_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            r = backtest(Path(d))
            self.assertEqual(r["predictions_scored"], 0)
            self.assertEqual(r["by_lead"], [])


class TestLoadTimelines(unittest.TestCase):
    def test_collapses_duplicate_observations(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ts = T0
            for k in range(3):                       # same reading in three snapshots
                p = root / f"{ts:%Y/%m/%d}" / f"f{k}.json.gz"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(gzip.compress(json.dumps({"features": [{"properties": {
                    "id": "t:1", "level_msl": 2.0, "bank_msl": 9.0,
                    "observed_at": ts.isoformat()}}]}).encode()))
            timelines, banks = load_timelines(root)
            self.assertEqual(len(timelines["t:1"]), 1)
            self.assertEqual(banks["t:1"], 9.0)


if __name__ == "__main__":
    unittest.main()
