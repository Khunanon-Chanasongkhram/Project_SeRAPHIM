"""Per-country accuracy, and calling a reservoir a reservoir."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.adapters.nwsriv import KIND_BY_PE  # noqa: E402
from seraphim.models import Admin, Observation, Station, StationState  # noqa: E402
from seraphim.publish import build_geojson  # noqa: E402
from seraphim.validate import country_of  # noqa: E402


class TestCountryOfStationId(unittest.TestCase):
    def test_every_level_source_resolves_to_its_country(self):
        for sid, expected in (("thaiwater:528052", "TH"), ("nwsriv:AAIT2", "US"),
                              ("ukea:1234", "GB"), ("rws:genemuiden", "NL")):
            self.assertEqual(country_of(sid), expected)

    def test_an_unknown_source_is_none_not_a_guess(self):
        self.assertIsNone(country_of("bogus:1"))
        self.assertIsNone(country_of("noprefix"))

    def test_it_works_on_ids_alone_so_old_archives_still_score(self):
        # Country is derived from the id prefix rather than a published field, so
        # archive files written before per-country reporting existed still resolve.
        self.assertEqual(country_of("thaiwater:1"), "TH")


class TestGaugeKind(unittest.TestCase):
    def test_shef_pool_elevation_is_a_reservoir(self):
        self.assertEqual(KIND_BY_PE.get("HP"), "reservoir")

    def test_river_stage_is_not_in_the_map_and_defaults_to_river(self):
        self.assertNotIn("HG", KIND_BY_PE)
        self.assertEqual(KIND_BY_PE.get("HG", "river"), "river")

    def test_kind_reaches_the_published_properties(self):
        st = Station(source="t", external_id="1", name="x", lat=13.4, lon=100.6,
                     bank_msl=2.0, kind="reservoir",
                     admin=Admin(country="US", province="TX"))
        from datetime import datetime, timezone
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        ob = Observation(station_id=st.id, observed_at=now, level_msl=1.0)
        props = build_geojson([StationState(station=st, observation=ob,
                                            generated_at=now)])["features"][0]["properties"]
        self.assertEqual(props["kind"], "reservoir")

    def test_an_ordinary_river_does_not_carry_a_tidal_flag(self):
        st = Station(source="t", external_id="1", name="x", lat=13.4, lon=100.6)
        from datetime import datetime, timezone
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        ob = Observation(station_id=st.id, observed_at=now, level_msl=1.0)
        props = build_geojson([StationState(station=st, observation=ob,
                                            generated_at=now)])["features"][0]["properties"]
        # Trimmed away rather than published as false for 16,000 stations.
        self.assertIsNone(props.get("tidal"))
        self.assertEqual(props["kind"], "river")


if __name__ == "__main__":
    unittest.main()
