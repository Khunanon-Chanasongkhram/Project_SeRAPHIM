"""Phase 3 tests: astronomy and calm-mode scoring.

The astronomy is validated two ways, because there is no single authority to hand:
  * SOLAR against captured Open-Meteo sunrise/sunset (an independent implementation),
    stored as fixtures so the test runs offline
  * LUNAR against physical invariants, the moon's transit must drift ~50 min a day,
    transit and antitransit must sit half a lunar day apart, and a full moon must rise
    near sunset. No ephemeris needed; the sky is the oracle.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.astro import (  # noqa: E402
    civil_twilight,
    moon_altitude,
    moon_events,
    moon_position,
    sun_altitude,
    sun_events,
    sun_position,
)
from seraphim.fishing import (  # noqa: E402
    MAJOR_HALF_WIDTH_MIN,
    PROFILES,
    TIDELESS,
    clarity_advice,
    day_plan,
    effective_weights,
    score_hour,
    solunar_windows,
    tide_movement_score,
    tide_rates_for_day,
)
from seraphim.tide import moon_illumination  # noqa: E402

UTC = timezone.utc
BKK = (13.7563, 100.5018)
FIXTURES = json.loads((ROOT / "tests" / "fixtures_sun.json").read_text(encoding="utf-8"))


class TestSolarAgainstIndependentSource(unittest.TestCase):
    """Captured from Open-Meteo. Their values are floored to the minute, so we centre
    by 30 s before comparing and then require agreement within a minute."""

    def test_sunrise_and_sunset_match(self):
        worst = 0.0
        for place in FIXTURES["places"]:
            lat, lon = place["lat"], place["lon"]
            ours = []
            base = date.fromisoformat(place["sunrise"][0][:10])
            for k in range(-1, 9):
                ev = sun_events(lat, lon, base + timedelta(days=k), tz_offset_hours=0.0)
                ours += [t for t in (ev.rise, ev.set) if t]
            for stamp in place["sunrise"] + place["sunset"]:
                theirs = datetime.fromisoformat(stamp).replace(tzinfo=UTC) + timedelta(seconds=30)
                gap = min(abs((o - theirs).total_seconds()) for o in ours)
                worst = max(worst, gap)
        self.assertLess(worst, 60.0, f"solar disagrees with an independent source by {worst:.0f}s")

    def test_covers_the_country(self):
        self.assertGreaterEqual(len(FIXTURES["places"]), 5)


class TestLunarInvariants(unittest.TestCase):
    def test_transit_drifts_about_fifty_minutes_a_day(self):
        lat, lon = BKK
        prev, drifts = None, []
        for k in range(14):
            t = moon_events(lat, lon, date(2026, 9, 10) + timedelta(days=k)).transit
            if prev and t:
                drifts.append((t - prev).total_seconds() / 60 - 1440)
            prev = t
        mean = sum(drifts) / len(drifts)
        self.assertGreater(mean, 40.0)
        self.assertLess(mean, 60.0)

    def test_transit_and_antitransit_are_half_a_lunar_day_apart(self):
        lat, lon = BKK
        for k in range(8):
            ev = moon_events(lat, lon, date(2026, 9, 10) + timedelta(days=k))
            if ev.transit and ev.antitransit:
                gap = abs((ev.transit - ev.antitransit).total_seconds()) / 3600
                gap = min(gap, 24.84 - gap)
                self.assertAlmostEqual(gap, 12.42, delta=1.0)

    def test_full_moon_rises_near_sunset(self):
        lat, lon = BKK
        best = max(
            (date(2026, 9, 1) + timedelta(days=k) for k in range(60)),
            key=lambda d: moon_illumination(datetime(d.year, d.month, d.day, 12, tzinfo=UTC)),
        )
        mev, sev = moon_events(lat, lon, best), sun_events(lat, lon, best)
        self.assertIsNotNone(mev.rise)
        diff = ((mev.rise - sev.set).total_seconds() / 3600 + 12) % 24 - 12
        self.assertLess(abs(diff), 1.5, "a full moon rises as the sun sets")

    def test_altitude_is_bounded(self):
        lat, lon = BKK
        for h in range(0, 48, 3):
            when = datetime(2026, 9, 15, tzinfo=UTC) + timedelta(hours=h)
            for alt in (sun_altitude(lat, lon, when), moon_altitude(lat, lon, when)):
                self.assertGreaterEqual(alt, -90.0)
                self.assertLessEqual(alt, 90.0)

    def test_moon_distance_is_plausible(self):
        for k in range(0, 30, 3):
            _, _, dist = moon_position(datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=k))
            self.assertGreater(dist, 355_000)
            self.assertLess(dist, 410_000)

    def test_sun_declination_follows_the_season(self):
        # Northern summer solstice positive, winter negative.
        _, jun = sun_position(datetime(2026, 6, 21, 12, tzinfo=UTC))
        _, dec = sun_position(datetime(2026, 12, 21, 12, tzinfo=UTC))
        self.assertGreater(jun, 22.0)
        self.assertLess(dec, -22.0)

    def test_civil_twilight_brackets_sunrise_and_sunset(self):
        lat, lon = BKK
        day = date(2026, 9, 16)
        sun, twi = sun_events(lat, lon, day), civil_twilight(lat, lon, day)
        self.assertLess(twi.rise, sun.rise, "civil dawn precedes sunrise")
        self.assertGreater(twi.set, sun.set, "civil dusk follows sunset")


class TestSolunarWindows(unittest.TestCase):
    def test_four_windows_two_major_two_minor(self):
        w = solunar_windows(*BKK, date(2026, 9, 16))
        self.assertEqual(sum(1 for x in w if x.kind == "major"), 2)
        self.assertLessEqual(sum(1 for x in w if x.kind == "minor"), 2)

    def test_major_windows_are_two_hours_wide(self):
        for w in solunar_windows(*BKK, date(2026, 9, 16)):
            if w.kind == "major":
                minutes = (w.end - w.start).total_seconds() / 60
                self.assertAlmostEqual(minutes, MAJOR_HALF_WIDTH_MIN * 2, delta=1)

    def test_windows_are_sorted(self):
        w = solunar_windows(*BKK, date(2026, 9, 16))
        self.assertEqual([x.start for x in w], sorted(x.start for x in w))


class TestBiteScoring(unittest.TestCase):
    def test_moving_water_beats_slack_water(self):
        """The whole point: a height-based score would rate slack high tide best."""
        self.assertEqual(tide_movement_score(0.0), 0.0)
        self.assertEqual(tide_movement_score(0.35), 1.0)
        self.assertGreater(tide_movement_score(0.2), tide_movement_score(0.05))

    def test_direction_does_not_matter_only_rate(self):
        self.assertEqual(tide_movement_score(-0.3), tide_movement_score(0.3))

    def test_missing_tide_is_not_scored_as_slack(self):
        self.assertEqual(tide_movement_score(None), 0.0)

    def test_score_is_bounded_and_explained(self):
        when = datetime(2026, 9, 16, 10, tzinfo=UTC)
        b = score_hour(when, *BKK, "sea", tide_rate=0.3,
                       windows=solunar_windows(*BKK, date(2026, 9, 16)),
                       sun=sun_events(*BKK, date(2026, 9, 16)))
        self.assertGreaterEqual(b.score, 0)
        self.assertLessEqual(b.score, 100)
        self.assertTrue(b.factors, "a score must never ship without its factors")

    def test_rough_wind_penalises_and_says_why(self):
        args = dict(profile="sea", tide_rate=0.3)
        when = datetime(2026, 9, 16, 10, tzinfo=UTC)
        calm = score_hour(when, *BKK, **args, wind_kmh=5)
        rough = score_hour(when, *BKK, **args, wind_kmh=45)
        self.assertLess(rough.score, calm.score)
        self.assertTrue(any("rough" in f.en for f in rough.factors))

    def test_falling_pressure_helps(self):
        when = datetime(2026, 9, 16, 10, tzinfo=UTC)
        flat = score_hour(when, *BKK, "sea", tide_rate=0.2, pressure_change_hpa=0.0)
        falling = score_hour(when, *BKK, "sea", tide_rate=0.2, pressure_change_hpa=-3.0)
        self.assertGreater(falling.score, flat.score)


class TestTidelessFairness(unittest.TestCase):
    """A non-tidal water must not forfeit points it can never earn, or the scale
    measures 'is it near the sea' rather than 'is it worth going'."""

    def test_tideless_weights_are_redistributed(self):
        self.assertEqual(effective_weights("river", has_tide=False), TIDELESS)
        self.assertEqual(effective_weights("sea", has_tide=False), TIDELESS)
        self.assertEqual(effective_weights("sea", has_tide=True), PROFILES["sea"])

    def test_reservoir_never_scores_a_tide_factor(self):
        plan = day_plan(17.24, 98.97, date(2026, 9, 16), "reservoir")
        codes = {f["code"] for h in plan["hours"] for f in h["factors"]}
        self.assertNotIn("tide_movement", codes)

    def test_inland_river_is_competitive_with_the_coast(self):
        inland = day_plan(16.82, 100.26, date(2026, 9, 16), "river")
        self.assertGreater(inland["peak_score"], 30,
                           "a tideless spot must not be structurally near-zero")

    def test_tide_modelled_flag_is_honest(self):
        self.assertFalse(day_plan(17.24, 98.97, date(2026, 9, 16), "reservoir")["tide_modelled"])
        tidal = day_plan(13.48, 100.59, date(2026, 9, 16), "sea",
                         tide_rates={h: 0.2 for h in range(24)})
        self.assertTrue(tidal["tide_modelled"])


class TestDayPlan(unittest.TestCase):
    def test_shape(self):
        p = day_plan(*BKK, date(2026, 9, 16), "sea")
        self.assertEqual(len(p["hours"]), 24)
        self.assertLessEqual(len(p["best"]), 3)
        self.assertIn("moon_illumination", p)
        self.assertEqual(p["peak_score"], max(h["score"] for h in p["hours"]))

    def test_best_hours_are_actually_the_best(self):
        p = day_plan(*BKK, date(2026, 9, 16), "sea",
                     tide_rates={h: 0.3 if h in (4, 5) else 0.01 for h in range(24)})
        top = max(h["score"] for h in p["hours"])
        self.assertEqual(p["best"][0]["score"], top)

    def test_tide_rates_from_a_published_curve(self):
        summary = {"curve_start": "2026-09-16T00:00:00+00:00",
                   "curve_m": [float(i % 5) for i in range(60)]}
        rates = tide_rates_for_day(summary, date(2026, 9, 16), tz_offset=0.0)
        self.assertTrue(rates)
        self.assertTrue(all(isinstance(v, float) for v in rates.values()))

    def test_missing_curve_yields_no_rates(self):
        self.assertEqual(tide_rates_for_day({}, date(2026, 9, 16)), {})


class TestClarityAdvice(unittest.TestCase):
    """Turbidity is tactics, not score: muddy water changes how you fish, not whether."""

    def test_heavy_rain_is_turbid(self):
        self.assertEqual(clarity_advice(80.0, None)["state"], "turbid")

    def test_high_discharge_is_turbid(self):
        self.assertEqual(clarity_advice(0.0, 3.0)["state"], "turbid")

    def test_dry_is_clear(self):
        self.assertEqual(clarity_advice(1.0, 1.0)["state"], "clear")

    def test_unknown_stays_silent(self):
        self.assertIsNone(clarity_advice(None, None))


if __name__ == "__main__":
    unittest.main()
