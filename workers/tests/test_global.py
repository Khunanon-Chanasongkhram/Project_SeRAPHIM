"""Phase 7: a second country, and the assumptions it exposed.

Thailand publishes a bank level. The UK does not, it publishes a typical operating
range, and its levels are in four different datums. These tests exist because treating
those as the same thing would have the map announcing that thousands of British rivers
had burst their banks whenever they ran a little high.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.adapters import registry  # noqa: E402
from seraphim.adapters.ukea import (  # noqa: E402
    LEVEL_UNITS,
    UKEnvironmentAgencyAdapter,
    _plausible_gb,
)
from seraphim.models import Admin, Observation, Station, StationState  # noqa: E402
from seraphim.risk import NO_BANK_MAX_LEVEL, assess, rollup  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def state(level, *, bank=None, typical_high=None, country="GB", district="d",
          province="p", sid="1", datum="local"):
    st = Station(source="t", external_id=sid, name="s", lat=51.5, lon=-0.1,
                 bank_msl=bank, datum=datum, typical_high=typical_high,
                 admin=Admin(country=country, province=province, district=district))
    ob = Observation(station_id=st.id, observed_at=NOW - timedelta(minutes=10),
                     level_msl=level)
    return StationState(station=st, observation=ob, generated_at=NOW)


class TestNoBankLevel(unittest.TestCase):
    """A typical range is not an overtopping threshold, and must never be shown as one."""

    def test_above_typical_is_flagged_but_capped(self):
        r = assess(state(0.9, typical_high=0.5), None, [], NOW)
        self.assertTrue(any(x.code == "above_typical" for x in r.reasons))
        self.assertLessEqual(r.level, NO_BANK_MAX_LEVEL)

    def test_never_reaches_the_levels_that_mean_the_river_is_out(self):
        for lvl in (1.0, 5.0, 50.0):
            r = assess(state(lvl, typical_high=0.5), None, [], NOW)
            self.assertLess(r.level, 4, "level 4 and 5 claim the bank is reached")

    def test_never_claims_over_bank(self):
        r = assess(state(99.0, typical_high=0.5), None, [], NOW)
        self.assertFalse(any(x.code == "over_bank" for x in r.reasons))

    def test_no_time_to_bank_without_a_bank(self):
        from seraphim.history import Trend

        rising = Trend(rate_m_per_hr=0.3, r2=0.95, points=8, span_hours=4.0)
        self.assertIsNone(assess(state(0.4, typical_high=0.5), rising, [], NOW).time_to_bank_hr)

    def test_within_typical_says_so(self):
        r = assess(state(0.3, typical_high=0.5), None, [], NOW)
        self.assertTrue(any(x.code == "within_typical" for x in r.reasons))
        self.assertEqual(r.level, 1)

    def test_no_threshold_at_all_is_stated_not_hidden(self):
        r = assess(state(0.9), None, [], NOW)
        self.assertTrue(any(x.code == "no_threshold" for x in r.reasons))

    def test_a_bank_level_still_reaches_five(self):
        r = assess(state(2.5, bank=2.0, country="TH", datum="MSL"), None, [], NOW)
        self.assertEqual(r.level, 5)

    def test_above_typical_metres_is_computed_against_the_right_field(self):
        self.assertAlmostEqual(state(0.9, typical_high=0.5).above_typical_m, 0.4)
        self.assertIsNone(state(0.9).above_typical_m)


class TestCountriesStaySeparate(unittest.TestCase):
    def test_rollup_does_not_merge_a_thai_amphoe_with_a_british_catchment(self):
        th = state(1.0, bank=9.0, country="TH", province="p", district="d", sid="a")
        gb = state(1.0, typical_high=9.0, country="GB", province="p", district="d", sid="b")
        risks = {s.station.id: assess(s, None, [], NOW) for s in (th, gb)}
        areas = rollup([th, gb], risks)
        self.assertEqual(len(areas), 2, "same names, different countries, two rows")
        self.assertEqual({a["country"] for a in areas}, {"TH", "GB"})


class TestUKAdapter(unittest.TestCase):
    def test_registered_with_its_own_country(self):
        self.assertIn("ukea", registry)
        self.assertEqual(registry["ukea"].country, "GB")
        self.assertNotEqual(registry["ukea"].country, registry["thaiwater"].country)

    def test_station_ids_cannot_collide_between_countries(self):
        a = Station(source="thaiwater", external_id="1", name="a", lat=1, lon=1)
        b = Station(source="ukea", external_id="1", name="b", lat=1, lon=1)
        self.assertNotEqual(a.id, b.id)

    def test_only_level_units_are_accepted(self):
        """The feed also carries rainfall, flow and temperature on the same endpoint."""
        for bad in ("mm", "deg_C", "m3_s", "%"):
            self.assertNotIn(bad, LEVEL_UNITS)
        for good in ("mASD", "mAOD", "m", "mBDAT"):
            self.assertIn(good, LEVEL_UNITS)

    def test_bounding_box_rejects_null_island_and_swapped_coords(self):
        self.assertTrue(_plausible_gb(51.5, -0.1))
        self.assertFalse(_plausible_gb(0, 0))
        self.assertFalse(_plausible_gb(-0.1, 51.5))
        self.assertFalse(_plausible_gb(13.7, 100.5))   # Bangkok is not in Britain

    def test_dead_source_degrades(self):
        import seraphim.adapters.ukea as uk

        orig = uk.fetch_json
        uk.fetch_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            st, ob, h = UKEnvironmentAgencyAdapter().fetch()
        finally:
            uk.fetch_json = orig
        self.assertEqual((st, ob), ([], []))
        self.assertFalse(h.ok)


class TestForecastGrid(unittest.TestCase):
    """Two countries take the naive per-gauge fetch to ~22,000 calls a day against a
    10,000 limit. Gauges inside one cell share a forecast."""

    def test_grid_collapses_nearby_gauges(self):
        from seraphim.cli import FORECAST_GRID_DEG

        # Well inside one cell. Points that straddle a cell edge land in different
        # cells no matter how close they are, which costs a few extra calls and
        # breaks nothing, so the guarantee is about cell membership, not distance.
        pts = [(13.71, 100.41), (13.72, 100.42), (13.73, 100.43)]
        cells = {(round(a / FORECAST_GRID_DEG), round(b / FORECAST_GRID_DEG)) for a, b in pts}
        self.assertEqual(len(cells), 1)

    def test_grid_gives_a_large_real_world_reduction(self):
        """The guarantee that matters is the aggregate one: measured on the live
        network it takes 4,448 gauges down to about 1,000 cells."""
        from seraphim.cli import FORECAST_GRID_DEG
        import random

        random.seed(3)
        # Gauges cluster along rivers and towns, so simulate clustering, not uniformity.
        pts = []
        for _ in range(120):
            cx, cy = random.uniform(5, 21), random.uniform(97, 106)
            for _ in range(random.randint(1, 30)):
                pts.append((cx + random.gauss(0, 0.05), cy + random.gauss(0, 0.05)))
        cells = {(round(a / FORECAST_GRID_DEG), round(b / FORECAST_GRID_DEG)) for a, b in pts}
        self.assertLess(len(cells), len(pts) / 2,
                        "grid must at least halve the number of upstream calls")

    def test_grid_keeps_distant_gauges_apart(self):
        from seraphim.cli import FORECAST_GRID_DEG

        pts = [(13.75, 100.5), (18.79, 98.98), (51.5, -0.1)]
        cells = {(round(a / FORECAST_GRID_DEG), round(b / FORECAST_GRID_DEG)) for a, b in pts}
        self.assertEqual(len(cells), 3)

    def test_cell_size_is_not_finer_than_the_model_it_samples(self):
        from seraphim.cli import FORECAST_GRID_DEG

        # GloFAS is about 5 km; sampling far below that buys nothing and costs quota.
        self.assertGreaterEqual(FORECAST_GRID_DEG * 111, 15)


if __name__ == "__main__":
    unittest.main()
