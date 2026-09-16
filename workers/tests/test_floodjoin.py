"""Joining the flood climatology onto gauges, and the season label.

The join is the step where a cell's history becomes a claim about a specific river, so
the properties that matter are: a gauge only ever gets the history of its OWN cell, a
cell with no history leaves the gauge with none rather than with zeros, and a cell with
no river is skipped entirely.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from seraphim import cache, floodhist
from seraphim.cli import _flood_history, _flood_season, _grid_cells
from seraphim.models import Admin, Station


def _station(sid: str, lat: float, lon: float) -> Station:
    """`Station.id` is derived as source:external_id, so the ids below read
    "thaiwater:1" and match what the join and the published GeoJSON use."""
    return Station(source="thaiwater", external_id=sid, name=sid, lat=lat, lon=lon,
                   admin=Admin(country="TH"))


class FloodSeason(unittest.TestCase):
    def test_names_a_contiguous_season(self):
        # Peak Jul-Oct: the cut is 70% of the peak month, so July at 9 against a
        # peak of 10 is inside the season and November at 3 is not.
        m = [1, 1, 1, 1, 2, 5, 9, 10, 10, 9, 3, 1]
        self.assertEqual(_flood_season(m), "Jul-Oct")

    def test_wraps_across_the_new_year(self):
        """A Nov-Feb season must not be printed as "Jan-Dec"."""
        m = [10, 9, 1, 1, 1, 1, 1, 1, 1, 1, 9, 10]
        self.assertEqual(_flood_season(m), "Nov-Feb")

    def test_single_month(self):
        m = [1, 1, 1, 1, 1, 1, 1, 1, 10, 1, 1, 1]
        self.assertEqual(_flood_season(m), "Sep")

    def test_declines_when_there_is_no_season(self):
        """A river that runs the same all year has no high-water season to name."""
        self.assertIsNone(_flood_season([5] * 12))
        self.assertIsNone(_flood_season(None))
        self.assertIsNone(_flood_season([1, 2, 3]))
        self.assertIsNone(_flood_season([0] * 12))


class GridCells(unittest.TestCase):
    def test_nearby_gauges_share_a_cell(self):
        # Kept away from a cell boundary on purpose: at a 0.2 deg grid, 100.50 and
        # 100.51 land either side of one, so a pair chosen to look adjacent would
        # test the rounding rule rather than the grouping.
        cells, points = _grid_cells([_station("1", 13.42, 100.42),
                                     _station("2", 13.45, 100.45),
                                     _station("3", 18.00, 99.00)])
        self.assertEqual(len(cells), 2)
        self.assertEqual(len(points), 2)
        sizes = sorted(len(v) for v in cells.values())
        self.assertEqual(sizes, [1, 2])

    def test_cell_ids_match_the_centroid_points(self):
        """The id in `points` must be the key the climatology cache is stored under."""
        cells, points = _grid_cells([_station("1", 13.40, 100.50)])
        (a, b), _ = next(iter(cells.items()))
        self.assertEqual(points[0][0], f"cell:{a}:{b}")


class FloodHistoryJoin(unittest.TestCase):
    STATS = {
        "v": 2, "high_days_per_year": 7.0, "growth_ratio": 1.8,
        "years": 12.0, "max_cms": 900.0, "max_on": "2017-09-01",
        "median_cms": 10.0, "p90_cms": 60.0, "p95_cms": 90.0, "p99_cms": 300.0,
        "episodes": 14, "episodes_per_decade": 11.7, "last_episode_on": "2026-08-20",
        "return_2y_cms": 400.0, "return_5y_cms": 700.0,
        "by_month_cms": [1, 1, 1, 1, 2, 5, 9, 10, 10, 9, 3, 1],
    }

    def _root(self, clim: dict) -> Path:
        root = Path(tempfile.mkdtemp())
        cache.save(floodhist.cache_root(root), floodhist.CACHE_KEY, clim)
        return root

    def _cell_id(self, st: Station) -> str:
        cells, points = _grid_cells([st])
        return points[0][0]

    def test_joins_a_cell_to_its_own_gauges(self):
        st = _station("1", 13.40, 100.50)
        root = self._root({self._cell_id(st): self.STATS})
        fields = {"thaiwater:1": {"discharge_now_cms": 300.0,
                                  "discharge_outlook_cms": [300, 350, 500, 900]}}
        out = _flood_history([st], root, fields)

        got = fields["thaiwater:1"]
        self.assertEqual(got["flood_prone"], 2)          # 7 high days a year
        self.assertEqual(got["flood_worst_cms"], 900.0)
        self.assertEqual(got["flood_season_months"], "Jul-Oct")
        self.assertEqual(got["flood_percentile"], 99.0)  # sits exactly on p99
        # Outlook reaches the 2-year level (400) on day 2 and the 5-year (700) on
        # day 3. The headline day is the SOONEST crossing, because that is the part
        # anyone can act on; the rarer one is published beside it, and the label
        # reports the strongest level reached.
        self.assertEqual(got["flood_outlook_day"], 2)
        self.assertEqual(got["flood_outlook_5y_day"], 3)
        self.assertEqual(got["flood_outlook_period_y"], 5)
        self.assertEqual(out["stations"], 1)
        self.assertEqual(out["predicted"], 1)

    def test_a_gauge_in_an_unfetched_cell_gets_nothing(self):
        """Absent history must stay absent, not become zeros."""
        here = _station("1", 13.40, 100.50)
        elsewhere = _station("2", 7.00, 99.00)
        root = self._root({self._cell_id(here): self.STATS})
        fields: dict = {}
        _flood_history([here, elsewhere], root, fields)
        self.assertIn("flood_prone", fields.get("thaiwater:1", {}))
        self.assertNotIn("flood_prone", fields.get("thaiwater:2", {}))

    def test_a_cell_with_no_river_is_skipped(self):
        st = _station("1", 13.40, 100.50)
        root = self._root({self._cell_id(st): {"no_river": True}})
        fields: dict = {}
        out = _flood_history([st], root, fields)
        self.assertEqual(fields, {})
        self.assertEqual(out["stations"], 0)

    def test_no_percentile_without_a_current_flow(self):
        """US gauges opt out of the shared forecast grid and carry no GloFAS 'now'.

        They still deserve the history of their cell; what they must not get is a
        percentile of a flow nobody measured.
        """
        st = _station("1", 13.40, 100.50)
        root = self._root({self._cell_id(st): self.STATS})
        fields: dict = {}
        _flood_history([st], root, fields)
        got = fields["thaiwater:1"]
        self.assertIn("flood_prone", got)
        self.assertNotIn("flood_percentile", got)
        self.assertNotIn("flood_outlook_day", got)

    def test_empty_cache_is_not_an_error(self):
        root = Path(tempfile.mkdtemp())
        fields: dict = {}
        out = _flood_history([_station("1", 13.4, 100.5)], root, fields)
        self.assertEqual(out["stations"], 0)
        self.assertEqual(fields, {})

    def test_the_build_never_expires_the_climatology(self):
        """A twelve-year record must not vanish because the top-up job is overdue."""
        st = _station("1", 13.40, 100.50)
        root = Path(tempfile.mkdtemp())
        clim_root = floodhist.cache_root(root)
        clim_root.mkdir(parents=True, exist_ok=True)
        # Written a year ago.
        (clim_root / f"{floodhist.CACHE_KEY}.json").write_text(json.dumps({
            "fetched_at": datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
            "data": {self._cell_id(st): self.STATS},
        }), encoding="utf-8")
        fields: dict = {}
        _flood_history([st], root, fields)
        self.assertIn("flood_prone", fields["thaiwater:1"])

    def test_climatology_lives_outside_the_shared_cache_directory(self):
        """It is saved and restored by a different CI workflow than data/cache.

        If they shared a directory, whichever workflow saved last would clobber the
        other, and the likeliest casualty is the gauge archive every trend is fitted to.
        """
        root = Path("/tmp/x")
        self.assertNotEqual(floodhist.cache_root(root), root / "cache")


if __name__ == "__main__":
    unittest.main()
