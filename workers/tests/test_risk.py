"""Phase 2 tests: history, trends, the risk engine and its explanations.

The headline number (time-to-bank) cannot be validated against live data until the cron
has been running for hours, so it is validated here against synthetic rivers with known
answers — including a full replay through a real on-disk archive.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seraphim.history import (  # noqa: E402
    MIN_SPAN_HOURS,
    Trend,
    fit_trend,
    load_history,
    merge_current,
)
from seraphim.models import Admin, Forecast, Observation, Station, StationState  # noqa: E402
from seraphim.risk import (  # noqa: E402
    MAX_TTB_HOURS,
    assess,
    haversine_km,
    rollup,
    time_to_bank,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def station(sid="1", bank=2.0, lat=13.5, lon=100.6, province="กรุงเทพ", district="เขต",
            district_code="01"):
    return Station(
        source="t", external_id=sid, name=f"st{sid}", lat=lat, lon=lon, bank_msl=bank,
        admin=Admin(country="TH", province=province, province_code="10",
                    district=district, district_code=district_code),
    )


def state(level=1.0, bank=2.0, age_min=10, forecast=None, sid="1", **kw):
    st = station(sid=sid, bank=bank, **kw)
    ob = Observation(station_id=st.id, observed_at=NOW - timedelta(minutes=age_min), level_msl=level)
    return StationState(station=st, observation=ob, generated_at=NOW, forecast=forecast)


def rising_trend(rate=0.2):
    return Trend(rate_m_per_hr=rate, r2=0.95, points=8, span_hours=4.0)


TIDE = [{
    "id": "cp", "name": "Chao Phraya Mouth", "name_th": "ปากเจ้าพระยา",
    "lat": 13.48, "lon": 100.59,
    "next": [{"kind": "high", "at": (NOW + timedelta(hours=1)).isoformat(), "height_m": 1.8}],
}]
TIDE_FAR = [{**TIDE[0], "lat": 7.0, "lon": 98.3}]
TIDE_LATER = [{**TIDE[0], "next": [
    {"kind": "high", "at": (NOW + timedelta(hours=10)).isoformat(), "height_m": 1.8}]}]


class TestTimeToBank(unittest.TestCase):
    def test_basic_arithmetic(self):
        # 1.2 m of headroom at 0.2 m/hr = 6 hours.
        self.assertEqual(time_to_bank(1.2, rising_trend(0.2)), 6.0)

    def test_withheld_when_trend_untrustworthy(self):
        self.assertIsNone(time_to_bank(1.2, Trend(0.2, 0.05, 3, 1.0)))

    def test_withheld_on_noise_level_rate(self):
        self.assertIsNone(time_to_bank(1.2, rising_trend(0.001)))

    def test_withheld_when_falling(self):
        self.assertIsNone(time_to_bank(1.2, rising_trend(-0.3)))

    def test_withheld_beyond_horizon(self):
        self.assertIsNone(time_to_bank(100.0, rising_trend(0.02)))
        self.assertIsNotNone(time_to_bank(MAX_TTB_HOURS * 0.02 * 0.9, rising_trend(0.02)))

    def test_none_when_already_over_bank(self):
        self.assertIsNone(time_to_bank(-0.5, rising_trend()))
        self.assertIsNone(time_to_bank(None, rising_trend()))


class TestRiskLevels(unittest.TestCase):
    def test_over_bank_is_critical(self):
        r = assess(state(level=2.5, bank=2.0), None, [], NOW)
        self.assertEqual(r.level, 5)
        self.assertTrue(any(x.code == "over_bank" for x in r.reasons))

    def test_imminent_overtopping_is_severe(self):
        # 0.8 m headroom at 0.2 m/hr = 4 h.
        r = assess(state(level=1.2, bank=2.0), rising_trend(0.2), [], NOW)
        self.assertEqual(r.level, 4)
        self.assertAlmostEqual(r.time_to_bank_hr, 4.0, places=1)

    def test_day_away_is_warning(self):
        # 1.0 m at 0.05 m/hr = 20 h.
        r = assess(state(level=1.0, bank=2.0), rising_trend(0.05), [], NOW)
        self.assertEqual(r.level, 3)

    def test_calm_river_is_normal(self):
        r = assess(state(level=0.0, bank=5.0), Trend(0.0, 1.0, 8, 4.0), [], NOW)
        self.assertEqual(r.level, 1)

    def test_low_freeboard_alone_raises_watch(self):
        self.assertGreaterEqual(assess(state(level=1.0, bank=2.0), None, [], NOW).level, 2)

    def test_extreme_rain_raises_level(self):
        fc = Forecast(fetched_at=NOW, rain_next_24h_mm=120.0)
        r = assess(state(level=1.0, bank=2.0, forecast=fc), None, [], NOW)
        self.assertGreaterEqual(r.level, 3)
        self.assertTrue(any(x.code == "rain_extreme" for x in r.reasons))


class TestTideCompounding(unittest.TestCase):
    """The Bangkok mechanism: discharge arriving when the tide has shut the outflow."""

    def test_nearby_high_tide_compounds_elevated_risk(self):
        s = state(level=1.6, bank=2.0)  # 0.4 m freeboard -> level 3
        base = assess(s, None, [], NOW)
        with_tide = assess(s, None, TIDE, NOW)
        self.assertGreater(with_tide.level, base.level)
        self.assertTrue(any(x.code == "tide_block" for x in with_tide.reasons))

    def test_high_tide_alone_does_not_raise_a_calm_river(self):
        s = state(level=0.0, bank=4.0)
        self.assertEqual(assess(s, None, TIDE, NOW).level, assess(s, None, [], NOW).level)

    def test_distant_coast_does_not_apply(self):
        s = state(level=1.6, bank=2.0)
        self.assertFalse(any(x.code == "tide_block" for x in assess(s, None, TIDE_FAR, NOW).reasons))

    def test_high_bank_station_is_not_tidal(self):
        s = state(level=19.6, bank=20.0)  # inland, high above sea level
        self.assertFalse(any(x.code == "tide_block" for x in assess(s, None, TIDE, NOW).reasons))

    def test_tide_outside_window_does_not_fire(self):
        s = state(level=1.6, bank=2.0)
        self.assertFalse(any(x.code == "tide_block" for x in assess(s, None, TIDE_LATER, NOW).reasons))


class TestHonesty(unittest.TestCase):
    def test_every_assessment_explains_itself(self):
        for s in (state(level=2.5, bank=2.0), state(level=0.0, bank=5.0), state(level=1.0, bank=2.0)):
            self.assertTrue(assess(s, None, [], NOW).reasons, "a score must never ship bare")

    def test_stale_station_keeps_its_level_but_loses_confidence(self):
        r = assess(state(level=2.5, bank=2.0, age_min=60 * 30), None, [], NOW)
        self.assertEqual(r.level, 5, "a silent gauge over bank is more alarming, not less")
        self.assertEqual(r.confidence, "poor")
        self.assertTrue(any(x.code == "stale" for x in r.reasons))

    def test_missing_history_is_stated_not_hidden(self):
        r = assess(state(level=1.0, bank=2.0), None, [], NOW)
        self.assertTrue(any(x.code == "no_trend" for x in r.reasons))
        self.assertEqual(r.trend_confidence, "none")

    def test_time_to_bank_labelled_an_estimate(self):
        r = assess(state(level=1.2, bank=2.0), rising_trend(0.2), [], NOW)
        ttb = next(x for x in r.reasons if x.code == "time_to_bank")
        self.assertIn("estimate", ttb.en.lower())
        self.assertIn("ประมาณการ", ttb.th)


class TestRollup(unittest.TestCase):
    def test_district_takes_the_worst_station_not_the_average(self):
        calm = state(level=0.0, bank=9.0, sid="a")
        flooded = state(level=9.9, bank=2.0, sid="b")
        risks = {s.station.id: assess(s, None, [], NOW) for s in (calm, flooded)}
        areas = rollup([calm, flooded], risks)
        self.assertEqual(len(areas), 1)
        self.assertEqual(areas[0]["level"], 5)
        self.assertEqual(areas[0]["stations"], 2)
        self.assertEqual(areas[0]["over_bank"], 1)

    def test_sorted_worst_first(self):
        a = state(level=0.0, bank=9.0, sid="a", district="calm", district_code="01")
        b = state(level=9.9, bank=2.0, sid="b", district="flooded", district_code="02")
        risks = {s.station.id: assess(s, None, [], NOW) for s in (a, b)}
        self.assertEqual(rollup([a, b], risks)[0]["district"], "flooded")


class TestHistoryReplay(unittest.TestCase):
    """End-to-end through a real on-disk archive, with a river whose rate we know."""

    def _write_archive(self, root: Path, readings):
        for observed, level in readings:
            path = root / f"{observed:%Y/%m/%d}" / f"{observed:%H%M}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [100.6, 13.5]},
                "properties": {"id": "t:1", "level_msl": level,
                               "observed_at": observed.isoformat()},
            }]}
            path.write_bytes(gzip.compress(json.dumps(payload).encode()))

    def test_replay_recovers_known_rate_and_predicts_overtopping(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            # 6 hours of readings rising a known 0.15 m/hr, ending 1.1 m below bank.
            readings = [(NOW - timedelta(hours=6 - i * 0.5), 0.0 + 0.15 * (i * 0.5))
                        for i in range(13)]
            self._write_archive(root, readings)

            hist = load_history(root, NOW, window_hours=8)
            self.assertIn("t:1", hist)
            self.assertGreaterEqual(len(hist["t:1"]), 12)

            trend = fit_trend(hist["t:1"])
            self.assertAlmostEqual(trend.rate_m_per_hr, 0.15, places=2)
            self.assertEqual(trend.confidence, "good")

            last_level = readings[-1][1]
            s = state(level=last_level, bank=last_level + 1.2)
            r = assess(s, trend, [], NOW)
            # 1.2 m of headroom at 0.15 m/hr = 8 hours.
            self.assertAlmostEqual(r.time_to_bank_hr, 8.0, delta=0.3)
            self.assertEqual(r.level, 3)
            self.assertTrue(any(x.code == "time_to_bank" for x in r.reasons))

    def test_duplicate_snapshots_do_not_flatten_the_trend(self):
        """Snapshots are taken more often than stations report, so the same reading
        recurs. If duplicates were not collapsed the fit would report a confident zero."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            readings = []
            for i in range(7):
                observed = NOW - timedelta(hours=6 - i)
                level = 0.1 * i
                for dup in range(3):  # same reading appears in 3 snapshots
                    readings.append((observed + timedelta(seconds=dup), level))
            # Write each under a distinct file name but identical observed_at.
            for n, (observed, level) in enumerate(readings):
                path = root / f"{NOW:%Y/%m/%d}" / f"f{n:03d}.json.gz"
                path.parent.mkdir(parents=True, exist_ok=True)
                obs = observed.replace(second=0, microsecond=0)
                payload = {"features": [{"properties": {
                    "id": "t:1", "level_msl": level, "observed_at": obs.isoformat()}}]}
                path.write_bytes(gzip.compress(json.dumps(payload).encode()))

            hist = load_history(root, NOW, window_hours=8)
            self.assertEqual(len(hist["t:1"]), 7, "duplicates must collapse to 7 readings")
            self.assertAlmostEqual(fit_trend(hist["t:1"]).rate_m_per_hr, 0.1, places=2)

    def test_merge_current_appends_the_freshest_reading(self):
        """Without this the trend always lags one cycle — and at the moment a river
        starts rising, that is exactly the cycle that matters."""
        s = state(level=1.5, bank=3.0)
        merged = merge_current({s.station.id: [(NOW - timedelta(hours=2), 1.0)]}, [s])
        self.assertEqual(len(merged[s.station.id]), 2)
        self.assertEqual(merged[s.station.id][-1][1], 1.5)

    def test_merge_current_dedupes_a_repeat_of_the_same_reading(self):
        s = state(level=1.5, bank=3.0)
        existing = {s.station.id: [(s.observation.observed_at, 1.5)]}
        self.assertEqual(len(merge_current(existing, [s])[s.station.id]), 1)

    def test_merge_current_handles_unknown_station(self):
        s = state(level=1.5, bank=3.0)
        self.assertIn(s.station.id, merge_current({}, [s]))

    def test_short_span_refuses_to_fit(self):
        pts = [(NOW - timedelta(minutes=30 - i * 10), 1.0 + 0.1 * i) for i in range(3)]
        t = fit_trend(pts)
        self.assertLess(t.span_hours, MIN_SPAN_HOURS)
        self.assertIsNone(t.rate_m_per_hr)
        self.assertEqual(t.confidence, "none")


class TestGeo(unittest.TestCase):
    def test_haversine(self):
        self.assertAlmostEqual(haversine_km(13.75, 100.5, 18.79, 98.98), 583, delta=10)
        self.assertEqual(haversine_km(13.0, 100.0, 13.0, 100.0), 0.0)


if __name__ == "__main__":
    unittest.main()


class TestLevelFiveMeansObserved(unittest.TestCase):
    """Level 5 must mean 'water is over the bank now', never a forecast.

    Otherwise a responder scanning the map cannot distinguish a river that is already
    out from one that might be in sixteen hours, and the top of the scale stops
    carrying information exactly when it matters most.
    """

    def test_forecast_escalation_cannot_reach_five(self):
        s = state(level=1.7, bank=2.0)  # 0.3 m freeboard, below bank
        r = assess(s, rising_trend(0.02), TIDE, NOW)
        self.assertTrue(any(x.code == "tide_block" for x in r.reasons))
        self.assertLessEqual(r.level, 4)

    def test_over_bank_still_reaches_five(self):
        r = assess(state(level=2.5, bank=2.0), None, TIDE, NOW)
        self.assertEqual(r.level, 5)
        self.assertTrue(any(x.code == "over_bank" for x in r.reasons))

    def test_a_five_always_carries_the_over_bank_reason(self):
        for st in (state(level=2.5, bank=2.0), state(level=9.0, bank=2.0)):
            r = assess(st, rising_trend(0.3), TIDE, NOW)
            if r.level == 5:
                self.assertTrue(any(x.code == "over_bank" for x in r.reasons))

    def test_escalation_never_demotes_an_over_bank_station(self):
        """A cap meant to prevent overstatement must not understate. Written as a
        regression test because the first version of the cap used min() and silently
        downgraded flooding rivers from 5 to 4."""
        s = state(level=2.5, bank=2.0)
        with_tide = assess(s, rising_trend(0.3), TIDE, NOW)
        without = assess(s, rising_trend(0.3), [], NOW)
        self.assertEqual(without.level, 5)
        self.assertGreaterEqual(with_tide.level, without.level,
                                "compounding must never reduce a risk level")
