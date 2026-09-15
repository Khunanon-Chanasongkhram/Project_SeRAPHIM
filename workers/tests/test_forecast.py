"""Phase 1 tests: tide analysis, forecast batching, and cache behaviour."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seraphim import cache  # noqa: E402
from seraphim.adapters import openmeteo  # noqa: E402
from seraphim.models import Forecast, TideSeries  # noqa: E402
from seraphim.tide import (  # noqa: E402
    coincidence_window,
    daily_ranges,
    find_extremes,
    moon_illumination,
    moon_phase,
    range_regime,
    tide_state,
)

UTC = timezone.utc


def synthetic_tide(hours=72, period=12.42, amp=1.0, start=None):
    """A clean semi-diurnal curve with a known answer, so the maths is checked
    against something exact rather than against the API that produced it."""
    start = start or datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    times = [start + timedelta(hours=h) for h in range(hours)]
    heights = [amp * math.sin(2 * math.pi * h / period) for h in range(hours)]
    return times, heights


class TestExtremes(unittest.TestCase):
    def test_finds_alternating_highs_and_lows(self):
        t, h = synthetic_tide()
        ex = find_extremes(t, h)
        self.assertGreater(len(ex), 8)
        kinds = [e.kind for e in ex]
        for a, b in zip(kinds, kinds[1:]):
            self.assertNotEqual(a, b, "highs and lows must alternate")

    def test_parabolic_refinement_beats_hourly_rounding(self):
        # First peak of sin(2*pi*h/12.42) is at h = 12.42/4 = 3.105.
        t, h = synthetic_tide()
        first_high = next(e for e in find_extremes(t, h) if e.kind == "high")
        hours = (first_high.at - t[0]).total_seconds() / 3600
        self.assertAlmostEqual(hours, 3.105, delta=0.15)
        # An unrefined result would land exactly on an integer hour.
        self.assertNotAlmostEqual(hours, round(hours), places=3)

    def test_peak_height_near_amplitude(self):
        t, h = synthetic_tide(amp=1.5)
        high = next(e for e in find_extremes(t, h) if e.kind == "high")
        self.assertAlmostEqual(high.height_m, 1.5, delta=0.05)

    def test_handles_gaps_and_short_series(self):
        t, h = synthetic_tide(hours=24)
        holed = [None if i % 5 == 0 else v for i, v in enumerate(h)]
        self.assertIsInstance(find_extremes(t, holed), list)
        self.assertEqual(find_extremes(t[:2], h[:2]), [])
        self.assertEqual(find_extremes([], []), [])

    def test_flat_series_has_no_extremes(self):
        t = [datetime(2026, 9, 15, tzinfo=UTC) + timedelta(hours=i) for i in range(10)]
        self.assertEqual(find_extremes(t, [1.0] * 10), [])


class TestTideState(unittest.TestCase):
    def test_direction(self):
        t, h = synthetic_tide()
        rising, rate = tide_state(t, h, t[0] + timedelta(minutes=30))
        self.assertEqual(rising, "rising")
        self.assertGreater(rate, 0)
        falling, rate2 = tide_state(t, h, t[4] + timedelta(minutes=30))
        self.assertEqual(falling, "falling")
        self.assertLess(rate2, 0)

    def test_outside_series_is_unknown(self):
        t, h = synthetic_tide()
        self.assertEqual(tide_state(t, h, t[0] - timedelta(days=5))[0], "unknown")


class TestMoon(unittest.TestCase):
    def test_phase_wraps_in_unit_interval(self):
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for d in range(0, 120, 7):
            p = moon_phase(base + timedelta(days=d))
            self.assertGreaterEqual(p, 0.0)
            self.assertLess(p, 1.0)

    def test_illumination_bounds_and_symmetry(self):
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for d in range(0, 60):
            self.assertGreaterEqual(moon_illumination(base + timedelta(days=d)), 0.0)
            self.assertLessEqual(moon_illumination(base + timedelta(days=d)), 1.0)

    def test_cycle_repeats_after_a_synodic_month(self):
        a = datetime(2026, 3, 1, tzinfo=UTC)
        self.assertAlmostEqual(moon_phase(a), moon_phase(a + timedelta(days=29.530588853)), places=4)


class TestRangeRegime(unittest.TestCase):
    """Range is measured against the location's own history, not inferred from the moon."""

    def test_classifies_by_percentile(self):
        hist = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8]
        self.assertEqual(range_regime(2.8, hist)[0], "large")
        self.assertEqual(range_regime(1.0, hist)[0], "small")
        self.assertEqual(range_regime(1.9, hist)[0], "average")

    def test_refuses_to_guess_without_enough_history(self):
        label, pct = range_regime(2.0, [1.0, 2.0])
        self.assertEqual(label, "unknown")
        self.assertEqual(pct, -1)

    def test_daily_ranges_drops_partial_days(self):
        start = datetime(2026, 9, 15, 0, tzinfo=UTC)
        times = [start + timedelta(hours=i) for i in range(24)] + [
            start + timedelta(days=1, hours=i) for i in range(3)
        ]
        heights = [float(i % 5) for i in range(27)]
        got = daily_ranges(times, heights)
        self.assertIn("2026-09-15", got)
        self.assertNotIn("2026-09-16", got, "a 3-hour day would report a false range")


class TestCoincidence(unittest.TestCase):
    """High tide blocking drainage is the coastal compounding test."""

    def test_detects_high_water_proximity(self):
        t, h = synthetic_tide()
        ex = find_extremes(t, h)
        high = next(e for e in ex if e.kind == "high")
        self.assertTrue(coincidence_window(ex, high.at, hours=1))
        self.assertTrue(coincidence_window(ex, high.at + timedelta(hours=2), hours=3))

    def test_low_water_does_not_count(self):
        low_only = [e for e in find_extremes(*synthetic_tide()) if e.kind == "low"]
        self.assertFalse(coincidence_window(low_only, low_only[0].at, hours=1))


class TestDischargeRatio(unittest.TestCase):
    def test_ratio(self):
        f = Forecast(fetched_at=datetime.now(UTC), discharge_now_cms=2.0, discharge_max_7d_cms=10.0)
        self.assertEqual(f.discharge_rise_ratio, 5.0)

    def test_near_zero_baseline_returns_none_not_infinity(self):
        f = Forecast(fetched_at=datetime.now(UTC), discharge_now_cms=0.01, discharge_max_7d_cms=50.0)
        self.assertIsNone(f.discharge_rise_ratio)

    def test_missing_data_is_none(self):
        self.assertIsNone(Forecast(fetched_at=datetime.now(UTC)).discharge_rise_ratio)


class TestBatchAlignment(unittest.TestCase):
    """Results are matched to stations purely by request order, so a length mismatch
    must drop the batch rather than silently pair the wrong forecast to the wrong river."""

    def test_mismatched_length_is_rejected(self):
        health_holder = []

        def fake(url, timeout=90):
            return [{"hourly": {}}]  # one result for three points

        orig = openmeteo.fetch_json
        openmeteo.fetch_json = fake
        try:
            from seraphim.models import SourceHealth

            h = SourceHealth(source="t", ok=False)
            out = openmeteo._batched("http://x", [("a", 1, 1), ("b", 2, 2), ("c", 3, 3)], "p", h)
            health_holder.append(h)
        finally:
            openmeteo.fetch_json = orig
        self.assertEqual(out, {})
        self.assertTrue(any("misaligning" in w for w in health_holder[0].warnings))

    def test_single_result_object_is_normalised(self):
        def fake(url, timeout=90):
            return {"hourly": {"time": [], "precipitation": []}}

        orig = openmeteo.fetch_json
        openmeteo.fetch_json = fake
        try:
            from seraphim.models import SourceHealth

            h = SourceHealth(source="t", ok=False)
            out = openmeteo._batched("http://x", [("a", 1, 1)], "p", h)
        finally:
            openmeteo.fetch_json = orig
        self.assertIn("a", out)


class TestWindowSum(unittest.TestCase):
    def test_none_when_no_data_not_zero(self):
        now = datetime(2026, 9, 15, tzinfo=UTC)
        self.assertIsNone(openmeteo._sum_window([], [], now, now + timedelta(hours=24)))
        self.assertIsNone(
            openmeteo._sum_window(["2026-09-15T00:00"], [None], now, now + timedelta(hours=24))
        )

    def test_sums_only_inside_window(self):
        times = [f"2026-09-15T{h:02d}:00" for h in range(6)]
        vals = [1.0] * 6
        start = datetime(2026, 9, 15, 1, tzinfo=UTC)
        self.assertEqual(openmeteo._sum_window(times, vals, start, start + timedelta(hours=3)), 3.0)


class TestCache(unittest.TestCase):
    def test_roundtrip_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache.save(root, "k", {"a": 1})
            self.assertEqual(cache.load(root, "k", 6)["data"], {"a": 1})
            # Zero max-age forces a miss, which is how --refresh-scale 0 works.
            self.assertIsNone(cache.load(root, "k", 0))

    def test_missing_and_corrupt_are_misses(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertIsNone(cache.load(root, "absent", 6))
            (root / "bad.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(cache.load(root, "bad", 6))


class TestTideSeriesModel(unittest.TestCase):
    def test_range_and_next_extremes(self):
        t, h = synthetic_tide()
        s = TideSeries(
            point_id="p", name="P", name_th="พ", lat=13, lon=100,
            fetched_at=datetime.now(UTC), times=t, heights_m=h,
            extremes=find_extremes(t, h),
        )
        self.assertAlmostEqual(s.range_m, 2.0, delta=0.05)
        self.assertLessEqual(len(s.next_extremes(t[0], limit=3)), 3)


if __name__ == "__main__":
    unittest.main()
