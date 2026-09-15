"""Phase 5: terrain.

Three approaches were tried and two were wrong in ways that would have produced
confident, incorrect flood extents. These tests pin down why the third is the one that
shipped, so nobody optimises it back into the others.
"""

from __future__ import annotations

import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.models import Admin, Observation, Station, StationState  # noqa: E402
from seraphim.terrain import (  # noqa: E402
    BEARINGS,
    LOW_GROUND_DROP_M,
    RANGES_M,
    SAMPLES_PER_GAUGE,
    Profile,
    build_layer,
    build_profiles,
    offset,
    sample_points,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def state(level, bank, sid="1", datum="MSL", lat=13.6, lon=100.7):
    st = Station(source="t", external_id=sid, name=f"g{sid}", lat=lat, lon=lon,
                 bank_msl=bank, datum=datum, admin=Admin(country="TH", province="p"))
    ob = Observation(station_id=st.id, observed_at=NOW - timedelta(minutes=5), level_msl=level)
    return StationState(station=st, observation=ob, generated_at=NOW)


class TestGeometry(unittest.TestCase):
    def test_offset_distance_is_right(self):
        lat, lon = 13.6, 100.7
        for bearing in (0, 90, 180, 270):
            a, b = offset(lat, lon, bearing, 1000)
            dy = (a - lat) * 111320
            dx = (b - lon) * 111320 * math.cos(math.radians(lat))
            self.assertAlmostEqual(math.hypot(dx, dy), 1000, delta=20)

    def test_north_is_north_and_east_is_east(self):
        a, _ = offset(13.6, 100.7, 0, 1000)
        self.assertGreater(a, 13.6)
        _, b = offset(13.6, 100.7, 90, 1000)
        self.assertGreater(b, 100.7)

    def test_sample_count(self):
        self.assertEqual(SAMPLES_PER_GAUGE, len(BEARINGS) * len(RANGES_M))
        self.assertEqual(len(sample_points(13.6, 100.7)), SAMPLES_PER_GAUGE + 1)


class TestNoDepthClaim(unittest.TestCase):
    """The rejected approach, kept as a test so it cannot come back.

    Gauges usually sit on the high ground. At one Samut Prakan canal gate every sampled
    point within a kilometre was 1 to 7 m below the gauge, because the canal is embanked
    above a delta. Subtracting elevations there would have turned a 0.4 m overtopping
    into a claim of 3.4 m of water, which is not how overtopping works: depth is set by
    volume and time, and this has neither.
    """

    EMBANKED = Profile("t:1", 8.0, [-3.0, -5.0, -2.0] * 8)

    def test_output_carries_no_depth_field(self):
        s = state(level=2.4, bank=2.0)
        layer = build_layer([s], {"t:1": {"base": 8.0, "rel": [-3.0, -5.0, -2.0] * 8}}, NOW)
        self.assertTrue(layer["features"])
        props = layer["features"][0]["properties"]
        self.assertNotIn("depth_m", props)
        self.assertIn("below_gauge_m", props)

    def test_low_ground_is_independent_of_how_far_over_bank(self):
        """Because it describes the ground, not the water."""
        a = self.EMBANKED.low_ground()
        self.assertEqual(len(a), SAMPLES_PER_GAUGE)
        for _, _, below in a:
            self.assertGreater(below, 0)

    def test_caveat_says_where_not_how_deep(self):
        s = state(level=2.4, bank=2.0)
        layer = build_layer([s], {"t:1": {"base": 8.0, "rel": [-3.0] * 24}}, NOW)
        self.assertIn("WHERE", layer["caveat"])
        self.assertIn("HOW DEEP", layer["caveat"])


class TestLowGround(unittest.TestCase):
    def test_flat_terrain_yields_nothing(self):
        flat = Profile("t:1", 5.0, [0.2, -0.3, 0.1] * 8)
        self.assertEqual(flat.low_ground(), [])

    def test_threshold_is_respected(self):
        just_under = Profile("t:1", 5.0, [-(LOW_GROUND_DROP_M - 0.1)] * 24)
        just_over = Profile("t:1", 5.0, [-(LOW_GROUND_DROP_M + 0.1)] * 24)
        self.assertEqual(just_under.low_ground(), [])
        self.assertEqual(len(just_over.low_ground()), 24)

    def test_missing_samples_are_skipped_not_treated_as_zero(self):
        holed = Profile("t:1", 5.0, [None, -4.0, None] * 8)
        self.assertEqual(len(holed.low_ground()), 8)


class TestLayerGating(unittest.TestCase):
    """Low ground beside a river that is well within its banks is just geography."""

    PROFILE = {"t:1": {"base": 8.0, "rel": [-3.0] * 24}}

    def test_nothing_shown_when_the_channel_is_not_spilling(self):
        layer = build_layer([state(level=1.0, bank=2.0)], self.PROFILE, NOW)
        self.assertEqual(layer["features"], [])

    def test_shown_when_over_bank(self):
        layer = build_layer([state(level=2.4, bank=2.0)], self.PROFILE, NOW)
        self.assertEqual(len(layer["features"]), 24)

    def test_no_profile_means_no_claim(self):
        self.assertIsNone(build_layer([state(level=2.4, bank=2.0)], {}, NOW))

    def test_points_land_around_the_gauge_not_on_it(self):
        layer = build_layer([state(level=2.4, bank=2.0)], self.PROFILE, NOW)
        for f in layer["features"]:
            lon, lat = f["geometry"]["coordinates"]
            self.assertNotEqual((round(lat, 5), round(lon, 5)), (13.6, 100.7))


class TestProfileBuilding(unittest.TestCase):
    def test_skips_networks_without_a_bank_level(self):
        """The UK publishes no bank level, so 'over bank' has no meaning there and
        terrain cannot be anchored to anything."""
        uk = Station(source="ukea", external_id="1", name="uk", lat=51.5, lon=-0.1,
                     bank_msl=None, datum="local")
        calls = []
        build_profiles([uk], {}, budget=10, log=calls.append)
        self.assertTrue(any("nothing new" in c for c in calls))

    def test_skips_stations_already_profiled(self):
        th = Station(source="thaiwater", external_id="1", name="th", lat=13.6, lon=100.7,
                     bank_msl=2.0, datum="MSL")
        calls = []
        build_profiles([th], {th.id: {"base": 1.0, "rel": [0.0] * 24}}, budget=10,
                       log=calls.append)
        self.assertTrue(any("nothing new" in c for c in calls))

    def test_budget_bounds_the_work(self):
        import seraphim.terrain as t

        sts = [Station(source="thaiwater", external_id=str(i), name="s", lat=13.6 + i * 0.01,
                       lon=100.7, bank_msl=2.0, datum="MSL") for i in range(10)]
        orig = t._fetch
        t._fetch = lambda pts, timeout=60: [5.0] + [4.0] * (len(pts) - 1)
        t.PAUSE_SECONDS = 0
        try:
            out = build_profiles(sts, {}, budget=3, log=lambda *_: None)
        finally:
            t._fetch = orig
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
