"""Flood climatology: the statistics, and the places they must refuse to answer.

The dangerous failure here is not a wrong percentile, it is a confident one computed
from nothing: a cell with no river, a record too short to fit a return level, or an
outlook compared against a threshold that was never established. Most of these tests
are about the refusals.
"""

from __future__ import annotations

import unittest
from datetime import date

from seraphim import floodhist as fh


def _series(start: date, values: list[float]) -> tuple[list[str], list[float]]:
    times = [date.fromordinal(start.toordinal() + i).isoformat() for i in range(len(values))]
    return times, values


def _seasonal(years: int, peak_flow: float, base: float = 1.0,
              peak_month: int = 9, start_year: int = 2014) -> tuple[list[str], list[float]]:
    """A river with one flood season a year, so annual maxima are unambiguous."""
    times: list[str] = []
    vals: list[float] = []
    for y in range(start_year, start_year + years):
        for m in range(1, 13):
            for d in range(1, 29):
                times.append(date(y, m, d).isoformat())
                # A clean peak in `peak_month`, varying year to year so the fit has
                # something to work with.
                if m == peak_month:
                    vals.append(peak_flow * (0.8 + 0.05 * (y - start_year)))
                else:
                    vals.append(base)
    return times, vals


class Quantiles(unittest.TestCase):
    def test_interpolates_between_points(self):
        self.assertEqual(fh._quantile([0, 10], 0.5), 5)
        self.assertEqual(fh._quantile([0, 10, 20], 0.5), 10)

    def test_single_value(self):
        self.assertEqual(fh._quantile([7], 0.99), 7)


class GumbelReturnLevel(unittest.TestCase):
    def test_orders_by_period(self):
        maxima = [100, 120, 95, 140, 110, 160, 105, 130, 115, 200]
        r2 = fh.gumbel_return_level(maxima, 2)
        r5 = fh.gumbel_return_level(maxima, 5)
        r10 = fh.gumbel_return_level(maxima, 10)
        self.assertLess(r2, r5)
        self.assertLess(r5, r10)
        # A 2-year level should sit near the middle of the sample, not at an extreme.
        self.assertGreater(r2, min(maxima))
        self.assertLess(r2, max(maxima))

    def test_refuses_short_record(self):
        self.assertIsNone(fh.gumbel_return_level([1, 2, 3], 2))

    def test_refuses_zero_variance(self):
        """Every year identical carries no information about an unusual year."""
        self.assertIsNone(fh.gumbel_return_level([50.0] * 12, 5))

    def test_refuses_meaningless_period(self):
        self.assertIsNone(fh.gumbel_return_level([1, 5, 2, 8, 3, 9], 1))


class Summarise(unittest.TestCase):
    def test_refuses_short_series(self):
        times, vals = _series(date(2024, 1, 1), [5.0] * 100)
        self.assertIsNone(fh.summarise(times, vals))

    def test_flags_a_cell_with_no_river(self):
        """A dry grid cell must be recorded as such, not given a percentile of noise."""
        times, vals = _series(date(2020, 1, 1), [0.01] * 800)
        out = fh.summarise(times, vals)
        self.assertEqual(out, {"no_river": True})

    def test_real_shape(self):
        times, vals = _seasonal(years=12, peak_flow=500.0, base=2.0)
        out = fh.summarise(times, vals)
        self.assertEqual(out["annual_maxima"], 12)
        self.assertGreater(out["max_cms"], 400)
        self.assertEqual(out["max_on"][5:7], "09")
        self.assertIn("return_2y_cms", out)
        self.assertIn("return_5y_cms", out)
        self.assertIn("return_10y_cms", out)
        # The flood season is the peak month, not the whole year.
        self.assertEqual(out["by_month_cms"][8], max(v for v in out["by_month_cms"]))

    def test_duration_and_growth_are_computed(self):
        """The two statistics flood-proneness is actually built on."""
        times, vals = _seasonal(years=12, peak_flow=500.0, base=2.0)
        out = fh.summarise(times, vals)
        self.assertEqual(out["v"], fh.STATS_SCHEMA)
        self.assertIn("high_days_per_year", out)
        self.assertIn("growth_ratio", out)
        self.assertGreater(out["growth_ratio"], 1.0)
        self.assertIn("median_episode_days", out)

    def test_duration_separates_a_flashy_river_from_a_sustained_one(self):
        """The property episode COUNT does not have, and the reason for the rewrite."""
        def build(peak_days):
            times, vals = [], []
            for y in range(2014, 2026):
                for m in range(1, 13):
                    for d in range(1, 29):
                        times.append(date(y, m, d).isoformat())
                        high = (m == 9 and d <= peak_days)
                        vals.append((300.0 + 11 * (y - 2014)) if high else 1.0)
            return fh.summarise(times, vals)
        flashy, sustained = build(2), build(24)
        self.assertLess(flashy["high_days_per_year"], sustained["high_days_per_year"])
        self.assertLess(fh.prone_level(flashy), fh.prone_level(sustained))
        # ...while the episode count says the two places are identical.
        self.assertEqual(flashy["episodes"], sustained["episodes"])

    def test_no_ten_year_level_from_a_short_record(self):
        """A 10-year return level needs ten years; six is an extrapolation."""
        times, vals = _seasonal(years=6, peak_flow=300.0)
        out = fh.summarise(times, vals)
        self.assertIn("return_2y_cms", out)
        self.assertNotIn("return_10y_cms", out)

    def test_nulls_are_skipped_not_zeroed(self):
        """A gap in the record must not read as a day the river ran dry."""
        times, vals = _seasonal(years=12, peak_flow=100.0, base=10.0)
        holed = list(vals)
        for i in range(0, len(holed), 7):
            holed[i] = None
        out = fh.summarise(times, holed)
        self.assertIsNotNone(out)
        # Median is the baseline flow, not dragged toward zero by the gaps.
        self.assertAlmostEqual(out["median_cms"], 10.0, places=1)

    def test_one_long_peak_is_one_episode(self):
        """A three-week monsoon must not count as twenty separate floods."""
        times: list[str] = []
        vals: list[float] = []
        for y in range(2014, 2026):
            for m in range(1, 13):
                for d in range(1, 29):
                    times.append(date(y, m, d).isoformat())
                    # Varying peak: a record with zero variance is refused
                    # outright, which is tested separately.
                    vals.append(200.0 + 7 * (y - 2014) if (m == 9 and d <= 21) else 1.0)
        out = fh.summarise(times, vals)
        # Twelve years, one long peak each: twelve episodes, not 252.
        self.assertLessEqual(out["episodes"], 13)
        self.assertGreaterEqual(out["worst_episode_days"], 14)

    def test_water_year_boundary_follows_the_dry_season(self):
        """Picked from the data, so a monsoon spanning new year is not split in two."""
        times: list[str] = []
        vals: list[float] = []
        for y in range(2014, 2026):
            for m in range(1, 13):
                for d in range(1, 29):
                    times.append(date(y, m, d).isoformat())
                    vals.append(100.0 if m in (12, 1) else 1.0)
        out = fh.summarise(times, vals)
        # The boundary must fall in the dry half, never inside the December-January peak.
        self.assertNotIn(out["water_year_start_month"], (12, 1))


class PercentileOf(unittest.TestCase):
    def setUp(self):
        self.stats = {"median_cms": 10.0, "p90_cms": 50.0, "p95_cms": 80.0,
                      "p99_cms": 150.0, "max_cms": 400.0}

    def test_anchors_are_exact(self):
        self.assertEqual(fh.percentile_of(self.stats, 10.0), 50.0)
        self.assertEqual(fh.percentile_of(self.stats, 150.0), 99.0)

    def test_above_the_record_is_capped(self):
        self.assertEqual(fh.percentile_of(self.stats, 9999.0), 100.0)

    def test_monotonic(self):
        seen = [fh.percentile_of(self.stats, v) for v in (1, 10, 40, 80, 120, 300)]
        self.assertEqual(seen, sorted(seen))

    def test_refuses_without_a_flow_or_a_river(self):
        self.assertIsNone(fh.percentile_of(self.stats, None))
        self.assertIsNone(fh.percentile_of({"no_river": True}, 5.0))
        self.assertIsNone(fh.percentile_of({}, 5.0))


class ProneLevel(unittest.TestCase):
    def _s(self, **kw):
        return {"v": fh.STATS_SCHEMA, **kw}

    def test_scales_with_time_spent_high(self):
        """Cut points sit near the 60th/85th/97th percentiles of real cells; see
        PRONE_DAYS for the measurement and its caveat."""
        self.assertEqual(fh.prone_level(self._s(high_days_per_year=12)), 3)
        self.assertEqual(fh.prone_level(self._s(high_days_per_year=7)), 2)
        self.assertEqual(fh.prone_level(self._s(high_days_per_year=4)), 1)
        self.assertEqual(fh.prone_level(self._s(high_days_per_year=1.5)), 0)

    def test_every_class_is_reachable(self):
        """The first calibration left classes 2 and 3 permanently empty on real data."""
        seen = {fh.prone_level(self._s(high_days_per_year=d))
                for d in (0.7, 2.6, 4.3, 7.1, 11.3)}
        self.assertEqual(seen, {0, 1, 2, 3})

    def test_a_steep_growth_curve_promotes_one_class(self):
        """A river whose rare floods dwarf its ordinary ones has more room to surprise."""
        flat = self._s(high_days_per_year=7, growth_ratio=1.5)
        steep = self._s(high_days_per_year=7, growth_ratio=3.2)
        self.assertEqual(fh.prone_level(flat), 2)
        self.assertEqual(fh.prone_level(steep), 3)

    def test_a_calm_river_is_not_promoted_by_growth_alone(self):
        """Steepness modifies exposure; it does not manufacture it."""
        self.assertEqual(fh.prone_level(self._s(high_days_per_year=1.0, growth_ratio=3.9)), 0)

    def test_episode_frequency_is_not_used(self):
        """It cannot be: the 2-year level is exceeded about five times a decade by
        definition, so frequency is near-identical for every river and the classes
        built on it were sorting rivers by fitting error. Measured across 246 real
        Thai cells it ran 2.4-11.0 with a median of 5.5."""
        busy = self._s(high_days_per_year=4, episodes_per_decade=11.0)
        quiet = self._s(high_days_per_year=4, episodes_per_decade=2.4)
        self.assertEqual(fh.prone_level(busy), fh.prone_level(quiet))

    def test_an_older_summary_reads_as_unknown(self):
        """A cell cached before high_days_per_year existed must not read as calm."""
        self.assertIsNone(fh.prone_level({"episodes_per_decade": 11.7, "episodes": 14}))
        self.assertIsNone(fh.prone_level({"v": 1, "high_days_per_year": 12}))

    def test_unknown_is_not_zero(self):
        """No history must never render as 'no flood history here'."""
        self.assertIsNone(fh.prone_level({"v": fh.STATS_SCHEMA}))
        self.assertIsNone(fh.prone_level(None))
        self.assertIsNone(fh.prone_level({"no_river": True}))
        self.assertIsNone(fh.prone_level({}))

    def test_unfittable_record_reports_unknown_not_calm(self):
        """A record we could not fit a threshold to has no episode count at all.

        Found by test_one_long_peak_is_one_episode: a river with an identical peak
        every year defeats the Gumbel fit, and the summary used to fall through to
        "0 episodes", which prone_level() reads as level 0, "not flood-prone". A
        failed measurement had been rendering as a reassuring answer.
        """
        times, vals = [], []
        for y in range(2014, 2026):
            for m in range(1, 13):
                for d in range(1, 29):
                    times.append(date(y, m, d).isoformat())
                    vals.append(500.0 if m == 9 else 1.0)
        out = fh.summarise(times, vals)
        self.assertNotIn("return_2y_cms", out)
        self.assertNotIn("episodes", out)
        self.assertIsNone(fh.prone_level(out))


class ForecastExceedance(unittest.TestCase):
    STATS = {"return_2y_cms": 100.0, "return_5y_cms": 200.0}

    def test_finds_the_first_day_past_each_level(self):
        out = fh.forecast_exceedance(self.STATS, [10, 20, 120, 130, 250, 90])
        self.assertEqual(out["day_2y"], 2)
        self.assertEqual(out["day_5y"], 4)
        self.assertEqual(out["peak_cms"], 250.0)
        self.assertEqual(out["peak_vs_2y"], 2.5)

    def test_silent_when_nothing_crosses(self):
        self.assertIsNone(fh.forecast_exceedance(self.STATS, [1, 2, 3, 4]))

    def test_no_five_year_day_when_it_never_gets_there(self):
        out = fh.forecast_exceedance(self.STATS, [10, 150, 160])
        self.assertEqual(out["day_2y"], 1)
        self.assertNotIn("day_5y", out)

    def test_refuses_without_a_threshold(self):
        """No fitted return level means no yardstick, so no claim."""
        self.assertIsNone(fh.forecast_exceedance({}, [500, 600]))
        self.assertIsNone(fh.forecast_exceedance({"no_river": True}, [500]))

    def test_nulls_in_the_outlook_do_not_count_as_zero(self):
        out = fh.forecast_exceedance(self.STATS, [None, None, 150])
        self.assertEqual(out["day_2y"], 2)


class Window(unittest.TestCase):
    def test_ends_yesterday(self):
        start, end = fh.window(date(2026, 9, 16))
        self.assertEqual(end, date(2026, 9, 15))
        self.assertEqual(start.year, 2026 - fh.CLIMATOLOGY_YEARS)

    def test_never_starts_before_the_reanalysis(self):
        start, _ = fh.window(date(2020, 1, 2))
        self.assertGreaterEqual(start, fh.EARLIEST_START)


class FetchClimatology(unittest.TestCase):
    """The fetch loop's safety properties, with the network stubbed out."""

    def _run(self, payloads, budget=100):
        from seraphim.models import SourceHealth
        calls: list[str] = []
        seq = list(payloads)

        def fake_fetch(url, timeout=0):
            calls.append(url)
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        health = SourceHealth(source="t", ok=False)
        orig = fh.fetch_json
        fh.fetch_json = fake_fetch
        try:
            points = [(f"cell:{i}:0", 10.0 + i, 100.0) for i in range(budget)]
            out = fh.fetch_climatology(points, health, budget=budget,
                                       sleep=lambda s: None, today=date(2026, 9, 16))
        finally:
            fh.fetch_json = orig
        return out, health, calls

    def _good_cell(self):
        times, vals = _seasonal(years=12, peak_flow=300.0)
        return {"daily": {"time": times, "river_discharge": vals}}

    def test_length_mismatch_drops_the_batch(self):
        """Order is the only link between a result and a cell. Never guess."""
        out, health, _ = self._run([[self._good_cell()]], budget=3)
        self.assertEqual(out, {})
        self.assertTrue(any("misalign" in w for w in health.warnings))

    def test_retries_once_then_gives_up(self):
        out, health, calls = self._run(
            [RuntimeError("HTTP Error 429"), RuntimeError("HTTP Error 429")], budget=2)
        self.assertEqual(out, {})
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("429" in w for w in health.warnings))

    def test_recovers_after_one_rate_limit(self):
        out, health, calls = self._run(
            [RuntimeError("HTTP Error 429"), [self._good_cell(), self._good_cell()]],
            budget=2)
        self.assertEqual(len(out), 2)
        self.assertTrue(health.ok)

    def test_budget_is_respected(self):
        cells = [self._good_cell() for _ in range(3)]
        from seraphim.models import SourceHealth
        health = SourceHealth(source="t", ok=False)
        orig = fh.fetch_json
        fh.fetch_json = lambda url, timeout=0: cells
        try:
            points = [(f"cell:{i}:0", 10.0 + i, 100.0) for i in range(500)]
            out = fh.fetch_climatology(points, health, budget=3,
                                       sleep=lambda s: None, today=date(2026, 9, 16))
        finally:
            fh.fetch_json = orig
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
