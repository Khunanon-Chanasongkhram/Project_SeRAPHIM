"""Core safety tests. stdlib unittest, the project has no third-party dependencies.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seraphim.adapters.base import ident, num, parse_local_naive, text  # noqa: E402
from seraphim.adapters.thaiwater import ThaiWaterAdapter  # noqa: E402
from seraphim.models import Admin, Observation, Station, StationState  # noqa: E402


class TestNullDiscipline(unittest.TestCase):
    """A missing reading must never become 0.0, that reads as 'river at zero', i.e. safe."""

    def test_nullish_stays_none(self):
        for v in (None, "", "  ", "-", "null", "NaN", "n/a", float("nan"), [], {}):
            self.assertIsNone(num(v), f"{v!r} should parse as None")

    def test_real_zero_survives(self):
        self.assertEqual(num(0), 0.0)
        self.assertEqual(num("0.00"), 0.0)

    def test_numeric_strings(self):
        self.assertEqual(num("1.29"), 1.29)
        self.assertEqual(num("-7.87"), -7.87)

    def test_bool_is_not_a_number(self):
        self.assertIsNone(num(True))
        self.assertIsNone(num(False))

    def test_ident_accepts_int_and_str(self):
        self.assertEqual(ident(528052), "528052")
        self.assertEqual(ident("84"), "84")
        self.assertIsNone(ident(None))
        self.assertIsNone(ident(""))
        self.assertIsNone(ident(True))

    def test_text_rejects_non_strings(self):
        self.assertIsNone(text(123))
        self.assertEqual(text(" x "), "x")


class TestTimezone(unittest.TestCase):
    """Treating Bangkok wall-clock as UTC would shift every reading by 7 hours."""

    def test_local_naive_to_utc(self):
        got = parse_local_naive("2026-09-15 14:00", 7.0)
        self.assertEqual(got, datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc))

    def test_result_is_aware(self):
        self.assertIsNotNone(parse_local_naive("2026-09-15 14:00", 7.0).tzinfo)

    def test_unparseable_returns_none(self):
        self.assertIsNone(parse_local_naive("not a date", 7.0))
        self.assertIsNone(parse_local_naive(None, 7.0))


class TestObservationSafety(unittest.TestCase):
    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            Observation(station_id="x:1", observed_at=datetime(2026, 9, 15, 14, 0))


def _state(bank, level, observed_at=None, now=None):
    now = now or datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
    st = Station(source="t", external_id="1", name="s", lat=13.0, lon=100.0, bank_msl=bank)
    ob = Observation(station_id=st.id, observed_at=observed_at or now, level_msl=level)
    return StationState(station=st, observation=ob, generated_at=now)


class TestFreeboard(unittest.TestCase):
    """Sign convention: positive = headroom below bank, negative = overtopped."""

    def test_below_bank_is_positive(self):
        self.assertAlmostEqual(_state(2.0, 1.5).freeboard_m, 0.5)

    def test_overtopped_is_negative(self):
        self.assertAlmostEqual(_state(2.0, 2.4).freeboard_m, -0.4)

    def test_exactly_at_bank_is_zero(self):
        self.assertEqual(_state(2.0, 2.0).freeboard_m, 0.0)

    def test_missing_inputs_give_none_not_zero(self):
        self.assertIsNone(_state(None, 1.5).freeboard_m)
        self.assertIsNone(_state(2.0, None).freeboard_m)


class TestStaleness(unittest.TestCase):
    def test_age_and_stale_flag(self):
        now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        fresh = _state(2.0, 1.0, datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc), now)
        old = _state(2.0, 1.0, datetime(2026, 9, 15, 4, 0, tzinfo=timezone.utc), now)
        self.assertEqual(fresh.data_age_minutes, 60.0)
        self.assertFalse(fresh.is_stale)
        self.assertEqual(old.data_age_minutes, 480.0)
        self.assertTrue(old.is_stale)


class TestStationIdentity(unittest.TestCase):
    def test_ids_are_namespaced(self):
        a = Station(source="thaiwater", external_id="1", name="a", lat=1, lon=1)
        b = Station(source="other", external_id="1", name="b", lat=1, lon=1)
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.id, "thaiwater:1")


class TestThaiWaterParsing(unittest.TestCase):
    """Parse a fixture shaped like a real row, offline."""

    ROW = {
        "id": 1306174127,
        "waterlevel_datetime": "2026-09-15 14:00",
        "waterlevel_m": None,
        "waterlevel_msl": "1.16",
        "waterlevel_msl_previous": "0.98",
        "discharge": None,
        "storage_percent": "98.11",
        "situation_level": 4,
        "diff_wl_bank": "0.17",
        "diff_wl_bank_text": "ต่ำกว่าตลิ่ง (ม.)",
        "agency": {"agency_shortname": {"th": "กฟผ."}},
        "basin": {"basin_name": {"th": "ภาคใต้"}},
        "station": {
            "id": 1422375,
            "tele_station_name": {"th": "วัด"},
            "tele_station_lat": 9.191049,
            "tele_station_long": 99.263785,
            "min_bank": 1.33,
            "ground_level": -7.87,
            "critical_level_msl": 1.33,
        },
        "geocode": {"province_name": {"th": "สุราษฎร์ธานี"}, "province_code": "84"},
    }

    def _parse(self, row):
        import seraphim.adapters.thaiwater as tw

        adapter = ThaiWaterAdapter()
        orig = tw.fetch_json
        tw.fetch_json = lambda *a, **k: {"data": [row]}
        try:
            return adapter.fetch()
        finally:
            tw.fetch_json = orig

    def test_parses_row(self):
        stations, obs, health = self._parse(self.ROW)
        self.assertTrue(health.ok)
        self.assertEqual(len(stations), 1)
        st, ob = stations[0], obs[0]
        self.assertEqual(st.id, "thaiwater:1422375")  # station.id, not row.id
        self.assertEqual(st.bank_msl, 1.33)
        self.assertEqual(ob.level_msl, 1.16)
        self.assertEqual(ob.observed_at, datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc))
        self.assertEqual(ob.source_severity, 4)
        self.assertIsNone(ob.discharge_cms)  # null must not become 0.0

    def test_implausible_coordinates_skipped(self):
        row = {**self.ROW, "station": {**self.ROW["station"], "tele_station_lat": 0, "tele_station_long": 0}}
        stations, _, health = self._parse(row)
        self.assertEqual(len(stations), 0)
        self.assertFalse(health.ok)

    def test_sign_crosscheck_flags_disagreement(self):
        # Source text says "below bank" but the numbers say overtopped.
        row = {**self.ROW, "waterlevel_msl": "9.99"}
        _, _, health = self._parse(row)
        self.assertTrue(any("SIGN disagrees" in w for w in health.warnings), health.warnings)

    def test_dead_source_degrades_not_raises(self):
        import seraphim.adapters.thaiwater as tw

        adapter = ThaiWaterAdapter()
        orig = tw.fetch_json

        def boom(*a, **k):
            raise RuntimeError("upstream down")

        tw.fetch_json = boom
        try:
            stations, obs, health = adapter.fetch()
        finally:
            tw.fetch_json = orig
        self.assertEqual((stations, obs), ([], []))
        self.assertFalse(health.ok)
        self.assertIn("upstream down", health.error)


if __name__ == "__main__":
    unittest.main()
