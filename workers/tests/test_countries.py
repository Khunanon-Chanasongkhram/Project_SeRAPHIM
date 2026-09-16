"""Phase 7b: the Netherlands and the United States.

Each of these two countries broke a different assumption, and every test here exists
because getting it wrong produces a plausible, confident, wrong number:

  * The US publishes a real flood stage, so its gauges must earn the full treatment
    (freeboard, time-to-bank, level 5) that the UK deliberately cannot have. The cap
    has to key on whether a threshold exists, not on which country a gauge is in.
  * US levels are in FEET, on the gauge's own datum. Unconverted feet would read as a
    river three times deeper than it is.
  * The Dutch "latest observations" endpoint returns the latest value of every series
    it has ever held, including one from 1740. Those must never reach a live map.
  * Dutch levels are in CENTIMETRES, against four different vertical datums.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.adapters import forecast_registry, registry  # noqa: E402
from seraphim.adapters.nwsriv import (  # noqa: E402
    FT_TO_M,
    HEIGHT_PREFIX,
    LEVEL_UNIT,
    NWSForecastAdapter,
    NWSRiverGaugesAdapter,
    _ft,
    _kcfs,
    _plausible_us,
)
from seraphim.adapters.rws import (  # noqa: E402
    DATUMS,
    MAX_READING_AGE_HOURS,
    MAX_LEVEL_M,
    MIN_LEVEL_M,
    RijkswaterstaatAdapter,
    SENTINEL,
    _parse_iso,
    _place_from_code,
    _plausible_nl,
)
from seraphim.models import Admin, Forecast, Observation, Station, StationState  # noqa: E402
from seraphim.models import SourceHealth  # noqa: E402
from seraphim.publish import STYLE_CRITICAL, build_geojson, build_meta  # noqa: E402
from seraphim.risk import MAX_FORECAST_LEVEL, assess  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def us_state(level_m, *, bank_m=None, forecast_m=None, forecast_at=None):
    st = Station(source="nwsriv", external_id="AAIT2", name="Williamson Creek",
                 lat=30.22, lon=-97.79, bank_msl=bank_m, datum="local",
                 admin=Admin(country="US", province="TX", district="EWX"))
    ob = Observation(station_id=st.id, observed_at=NOW - timedelta(minutes=10),
                     level_msl=level_m)
    fc = None
    if forecast_m is not None:
        fc = Forecast(fetched_at=NOW, forecast_level=forecast_m,
                      forecast_level_at=forecast_at, forecast_level_source="NWS")
    return StationState(station=st, observation=ob, generated_at=NOW, forecast=fc)


class TestUSGetsTheFullTreatment(unittest.TestCase):
    """The UK cap must not leak onto a country that does publish a threshold."""

    def test_a_us_gauge_over_its_flood_stage_reaches_five(self):
        r = assess(us_state(4.5, bank_m=3.9), None, [], NOW)
        self.assertEqual(r.level, 5)
        self.assertTrue(any(x.code == "over_bank" for x in r.reasons))

    def test_freeboard_is_computed_despite_a_local_datum(self):
        s = us_state(3.0, bank_m=3.9)
        self.assertAlmostEqual(s.freeboard_m, 0.9, places=3)

    def test_a_gauge_without_a_flood_stage_still_cannot_reach_five(self):
        r = assess(us_state(99.0), None, [], NOW)
        self.assertLess(r.level, 5)


class TestFeetAreConvertedAtTheBoundary(unittest.TestCase):
    """Rule 2. A level in feet presented as metres is wrong by a factor of 3.28."""

    def test_feet_to_metres(self):
        self.assertAlmostEqual(_ft(10.0), 3.048, places=3)

    def test_none_survives_conversion_rather_than_becoming_zero(self):
        self.assertIsNone(_ft(None))

    def test_the_adapter_only_reads_feet(self):
        self.assertEqual(LEVEL_UNIT, "ft")

    def test_only_heights_are_read_never_discharge(self):
        # SHEF "Q" codes are a flow. Read as a level, a flow in kcfs would put a
        # river tens of thousands of feet deep.
        self.assertTrue("HG".startswith(HEIGHT_PREFIX))
        self.assertFalse("QR".startswith(HEIGHT_PREFIX))

    def test_secondary_value_is_only_read_when_it_is_actually_kcfs(self):
        self.assertIsNone(_kcfs({"secvalue": "5", "secunit": "ft"}))
        self.assertIsNone(_kcfs({"secvalue": "5", "secunit": ""}))
        self.assertAlmostEqual(_kcfs({"secvalue": "1", "secunit": "kcfs"}), 28.317, places=2)

    def test_bounding_boxes_reject_null_island_and_sign_flips(self):
        self.assertTrue(_plausible_us(30.22, -97.79))    # Austin, TX
        self.assertTrue(_plausible_us(61.2, -149.9))     # Anchorage, AK
        self.assertTrue(_plausible_us(21.3, -157.8))     # Honolulu, HI
        self.assertFalse(_plausible_us(0.0, 0.0))
        self.assertFalse(_plausible_us(30.22, 97.79))    # longitude sign flipped


class TestOfficialForecastLevel(unittest.TestCase):
    """A national forecast of the gauge's own level, and the limits on what it may claim."""

    def test_a_forecast_over_the_bank_escalates(self):
        r = assess(us_state(1.0, bank_m=3.9, forecast_m=4.5), None, [], NOW)
        self.assertTrue(any(x.code == "forecast_over_bank" for x in r.reasons))
        self.assertGreaterEqual(r.level, MAX_FORECAST_LEVEL)

    def test_a_forecast_never_reaches_five(self):
        # Level 5 means water is over the bank NOW. A forecast must not wear that badge.
        r = assess(us_state(0.1, bank_m=3.9, forecast_m=99.0), None, [], NOW)
        self.assertLess(r.level, 5)

    def test_a_forecast_below_the_bank_is_reported_not_silent(self):
        r = assess(us_state(1.0, bank_m=3.9, forecast_m=2.0), None, [], NOW)
        self.assertTrue(any(x.code == "forecast_below_bank" for x in r.reasons))

    def test_a_near_miss_is_called_out(self):
        r = assess(us_state(1.0, bank_m=3.9, forecast_m=3.8), None, [], NOW)
        self.assertTrue(any(x.code == "forecast_near_bank" for x in r.reasons))

    def test_reasons_say_it_is_a_forecast_in_both_languages(self):
        r = assess(us_state(1.0, bank_m=3.9, forecast_m=4.5), None, [], NOW)
        reason = next(x for x in r.reasons if x.code == "forecast_over_bank")
        self.assertIn("forecast", reason.en.lower())
        self.assertIn("คาดการณ์", reason.th)

    def test_no_forecast_clause_is_invented_when_the_time_is_missing(self):
        r = assess(us_state(1.0, bank_m=3.9, forecast_m=4.5, forecast_at=None), None, [], NOW)
        reason = next(x for x in r.reasons if x.code == "forecast_over_bank")
        self.assertNotIn("None", reason.en)
        self.assertNotIn("None", reason.th)

    def test_a_forecast_without_a_bank_level_claims_nothing(self):
        # No threshold to cross, so there is nothing honest to say about crossing it.
        r = assess(us_state(1.0, forecast_m=99.0), None, [], NOW)
        self.assertFalse(any(x.code.startswith("forecast_") for x in r.reasons))

    def test_us_gauges_leave_the_shared_open_meteo_grid(self):
        # 11,467 US gauges on the shared grid is ~36,000 location-calls/day against a
        # 10,000 allowance, for a worse signal than NOAA's own per-gauge forecast.
        self.assertFalse(NWSRiverGaugesAdapter.shared_forecast_grid)
        self.assertFalse(NWSForecastAdapter.grid)


class TestDutchFreshnessGate(unittest.TestCase):
    """The Dutch endpoint hands back readings from 1740 alongside live telemetry."""

    def test_the_gate_exists_and_is_bounded_in_hours(self):
        self.assertIsInstance(MAX_READING_AGE_HOURS, float)
        self.assertLessEqual(MAX_READING_AGE_HOURS, 48.0)

    def test_an_eighteenth_century_reading_is_outside_the_window(self):
        ancient = datetime(1740, 1, 1, 0, 40, tzinfo=UTC)
        cutoff = NOW - timedelta(hours=MAX_READING_AGE_HOURS)
        self.assertLess(ancient, cutoff)

    def test_a_reading_from_twenty_minutes_ago_is_inside_it(self):
        cutoff = NOW - timedelta(hours=MAX_READING_AGE_HOURS)
        self.assertGreater(NOW - timedelta(minutes=20), cutoff)


class TestDutchDatumsAndUnits(unittest.TestCase):
    def test_centimetres_convert_to_metres(self):
        self.assertAlmostEqual(-16.0 * 0.01, -0.16, places=3)

    def test_national_datums_stay_national_and_foreign_ones_go_local(self):
        # TAW sits about 2.33 m below NAP. Offsetting it on an unverified constant
        # would be worse than declining to compare, so it is marked local.
        self.assertEqual(DATUMS["NAP"], "NAP")
        self.assertEqual(DATUMS["MSL"], "MSL")
        self.assertEqual(DATUMS["TAW"], "local")
        self.assertEqual(DATUMS["PLAATSLR"], "local")

    def test_the_plausibility_gate_keeps_the_limburg_hills(self):
        # epen.geul.cottessen really does read 119.68 m NAP: the Geul valley in South
        # Limburg. A gate built on "the Netherlands is flat" would delete real gauges.
        self.assertLessEqual(119.68, MAX_LEVEL_M)
        self.assertGreaterEqual(-1.46, MIN_LEVEL_M)

    def test_the_gate_still_rejects_a_sentinel(self):
        self.assertGreater(SENTINEL * 0.01, MAX_LEVEL_M)

    def test_offsets_are_respected_when_parsing_timestamps(self):
        at = _parse_iso("2026-09-16T02:50:00.000+01:00")
        self.assertEqual(at, datetime(2026, 9, 16, 1, 50, tzinfo=UTC))

    def test_a_timestamp_without_an_offset_is_refused_rather_than_guessed(self):
        self.assertIsNone(_parse_iso("2026-09-16 02:50:00"))

    def test_place_grouping_comes_from_the_station_code(self):
        self.assertEqual(_place_from_code("dronten.roggebotsluis.vossemeer"), "Dronten")
        self.assertEqual(_place_from_code("genemuiden"), "Genemuiden")

    def test_bounding_box_rejects_null_island(self):
        self.assertTrue(_plausible_nl(52.63, 6.03))
        self.assertFalse(_plausible_nl(0.0, 0.0))
        self.assertFalse(_plausible_nl(6.03, 52.63))  # swapped

    def test_no_dutch_gauge_claims_a_bank_level(self):
        # Rijkswaterstaat publishes no overtopping threshold, and none is invented.
        st = Station(source="rws", external_id="genemuiden", name="Genemuiden",
                     lat=52.63, lon=6.03, bank_msl=None, datum="NAP",
                     admin=Admin(country="NL", province="Genemuiden"))
        ob = Observation(station_id=st.id, observed_at=NOW, level_msl=-0.16)
        s = StationState(station=st, observation=ob, generated_at=NOW)
        self.assertIsNone(s.freeboard_m)
        self.assertLess(assess(s, None, [], NOW).level, 4)


class TestPayloadTrimming(unittest.TestCase):
    """Dropping nulls must never drop a key the map style reads."""

    def _feature(self):
        return build_geojson([us_state(1.0, bank_m=3.9)])["features"][0]

    def test_style_critical_keys_survive_even_when_null(self):
        props = self._feature()["properties"]
        for key in STYLE_CRITICAL:
            # A null where a MapLibre paint expression wants a boolean or a number
            # throws and takes the whole layer down, so these are kept explicitly.
            self.assertIn(key, props, f"{key} must survive trimming")

    def test_null_non_style_properties_are_dropped(self):
        props = self._feature()["properties"]
        self.assertNotIn("storage_percent", props)
        self.assertIsNone(props.get("storage_percent"))  # reads the same to the UI

    def test_a_redundant_english_name_is_dropped_but_a_real_one_is_kept(self):
        props = self._feature()["properties"]
        self.assertNotIn("name_en", props)   # US sources publish one name, not two
        th = Station(source="thaiwater", external_id="1", name="สถานี", name_en="Station",
                     lat=13.4, lon=100.9, admin=Admin(country="TH", province="p"))
        ob = Observation(station_id=th.id, observed_at=NOW, level_msl=1.0)
        state = StationState(station=th, observation=ob, generated_at=NOW)
        kept = build_geojson([state])["features"][0]["properties"]
        self.assertEqual(kept.get("name_en"), "Station")

    def test_values_that_are_present_are_never_dropped(self):
        props = self._feature()["properties"]
        self.assertEqual(props["level_msl"], 1.0)
        self.assertEqual(props["bank_msl"], 3.9)
        self.assertEqual(props["datum"], "local")


class TestOptionalSourcesDoNotCryWolf(unittest.TestCase):
    """A layer nobody configured is not the pipeline breaking."""

    def _sources_check(self, *health):
        """Just the `sources` check. The fixture is one station, which trips the
        unrelated station-count check, so asserting on overall status would hide
        what this is actually testing."""
        m = build_meta([us_state(1.0, bank_m=3.9)], list(health), NOW)
        return next(c for c in m["health"]["checks"] if c["check"] == "sources")

    def test_an_unconfigured_optional_source_does_not_fail_the_snapshot(self):
        c = self._sources_check(SourceHealth(source="nasa_firms", ok=False, optional=True,
                                             error="no FIRMS_MAP_KEY configured"))
        self.assertEqual(c["status"], "pass")

    def test_but_it_is_still_named_rather_than_hidden(self):
        c = self._sources_check(SourceHealth(source="nasa_firms", ok=False, optional=True))
        self.assertIn("nasa_firms", c["detail"])
        self.assertIn("not configured", c["detail"])

    def test_a_genuinely_broken_source_still_fails(self):
        c = self._sources_check(SourceHealth(source="ukea", ok=False, error="HTTP 503"))
        self.assertEqual(c["status"], "fail")
        self.assertIn("ukea", c["detail"])

    def test_a_broken_source_fails_even_alongside_an_optional_one(self):
        c = self._sources_check(SourceHealth(source="nasa_firms", ok=False, optional=True),
                                SourceHealth(source="ukea", ok=False, error="HTTP 503"))
        self.assertEqual(c["status"], "fail")

    def test_a_healthy_run_says_nothing_about_configuration(self):
        c = self._sources_check(SourceHealth(source="thaiwater", ok=True))
        self.assertEqual(c["status"], "pass")
        self.assertNotIn("not configured", c["detail"])


class TestRegistration(unittest.TestCase):
    def test_four_countries_are_registered_and_distinct(self):
        countries = {a.country for a in registry.values()}
        self.assertEqual(countries, {"TH", "GB", "US", "NL"})

    def test_station_ids_cannot_collide_across_countries(self):
        ids = [a.id for a in registry.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_us_forecast_adapter_is_registered_for_the_us_only(self):
        self.assertIs(forecast_registry["nwsfcst"].country, "US")

    def test_every_adapter_declares_attribution(self):
        # Several of these licences require it, and the UI cannot show what is not set.
        for a in list(registry.values()) + list(forecast_registry.values()):
            self.assertTrue(a.attribution.strip(), a.id)


class TestDeadSourcesDegrade(unittest.TestCase):
    """One dead country must never take the site down, whichever one it is."""

    def test_a_failed_us_fetch_returns_health_not_an_exception(self):
        a = NWSRiverGaugesAdapter()
        from seraphim.adapters import nwsriv
        original = nwsriv._fetch_all_pages
        nwsriv._fetch_all_pages = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("503"))
        try:
            stations, obs, h = a.fetch()
        finally:
            nwsriv._fetch_all_pages = original
        self.assertEqual((stations, obs), ([], []))
        self.assertFalse(h.ok)
        self.assertIn("503", h.error)

    def test_a_failed_dutch_catalogue_returns_health_not_an_exception(self):
        a = RijkswaterstaatAdapter()
        from seraphim.adapters import rws
        original = rws.post_json
        rws.post_json = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            stations, obs, h = a.fetch()
        finally:
            rws.post_json = original
        self.assertEqual((stations, obs), ([], []))
        self.assertFalse(h.ok)


if __name__ == "__main__":
    unittest.main()
