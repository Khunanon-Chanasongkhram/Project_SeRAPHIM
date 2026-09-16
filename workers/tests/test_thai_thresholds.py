"""The zero-bank trap, and the dam feed's version of the same disease.

Every test here exists because ThaiWater uses a literal 0 to mean "not published", and
taking that at face value produced confident, plausible, dangerous numbers:

  * 302 of 1,118 gauges were announced as OVERTOPPED for the offence of sitting above
    sea level, because their bank level is the sentinel 0 and `msl - 0 > 0`.
  * The source makes the same mistake in its own `diff_wl_bank_text`, which is why the
    original "1,121/1,121 signs agreed" cross-check passed: both sides were computed
    from the same sentinel, so they were wrong together.
  * Readings of -875.7 m MSL and -9.99 m (23 m below the station's own bed) were
    published as facts, each producing a large fake freeboard, i.e. "looks safe".
  * The dam feed mixes rows from 2021, 2022 and the 1970 epoch in with today's, and
    sorting fullest-first put a reservoir last read in 2021 at the top of the map.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.adapters import thaidam  # noqa: E402
from seraphim.adapters.thaidam import (  # noqa: E402
    GROUPS,
    HIGH_PERCENT,
    MAX_READING_AGE_DAYS,
    SPILLING_PERCENT,
    _dam_time,
    _reading,
)
from seraphim.adapters.thaiwater import (  # noqa: E402
    MAX_BELOW_BED_M,
    MAX_LEVEL_MSL,
    MIN_LEVEL_MSL,
    threshold,
)


class TestZeroIsNotABankLevel(unittest.TestCase):
    def test_zero_reads_as_not_published(self):
        self.assertIsNone(threshold(0))
        self.assertIsNone(threshold("0"))
        self.assertIsNone(threshold(0.0))

    def test_a_real_threshold_survives(self):
        self.assertEqual(threshold("166.25"), 166.25)
        self.assertEqual(threshold(1.19), 1.19)

    def test_negative_thresholds_survive(self):
        # Delta stations genuinely sit below MSL; only exact zero is the sentinel.
        self.assertEqual(threshold(-1.63), -1.63)

    def test_missing_stays_missing(self):
        for v in (None, "", "-", "null"):
            self.assertIsNone(threshold(v))

    def test_a_gauge_above_sea_level_is_not_overtopped_by_its_own_altitude(self):
        # The exact shape of the bug: a station reading 164.89 m MSL whose bank level
        # is the sentinel. Freeboard must be unavailable, not 164.89 m of overtopping.
        bank = threshold(0)
        self.assertIsNone(bank, "a sentinel bank must not become a number")


class TestImpossibleReadingsAreRefused(unittest.TestCase):
    def test_the_range_covers_thailand_but_not_a_broken_sensor(self):
        self.assertLess(MIN_LEVEL_MSL, -2.0)       # the delta really does go negative
        self.assertGreater(MAX_LEVEL_MSL, 2565.0)  # Doi Inthanon
        self.assertLess(-875.7, MIN_LEVEL_MSL)     # the observed fault value

    def test_a_reading_far_below_the_bed_is_impossible(self):
        # สถานีคลองหวะ: level -9.99 with its own bed at +13.42.
        level, ground = -9.99, 13.42
        self.assertLess(level, ground - MAX_BELOW_BED_M)

    def test_a_channel_drying_slightly_below_its_surveyed_bed_is_kept(self):
        # The worst genuine case measured, and the source publishes a negative
        # storage_percent for it quite deliberately.
        level, ground = 138.03, 139.867
        self.assertGreaterEqual(level, ground - MAX_BELOW_BED_M)

    def test_the_allowance_separates_the_two_cleanly(self):
        # Sentinels sit 7.7 m or more below bed; the worst real one 1.84 m.
        self.assertGreater(MAX_BELOW_BED_M, 1.84)
        self.assertLess(MAX_BELOW_BED_M, 7.69)


class TestDamSentinelsAndFreshness(unittest.TestCase):
    def test_zero_is_not_a_reading(self):
        self.assertIsNone(_reading(0))
        self.assertIsNone(_reading("0"))
        self.assertEqual(_reading("88.39"), 88.39)

    def test_only_mappable_groups_are_published(self):
        # dam_small_tele has no coordinates; dam_hourly reports 0% for every row.
        self.assertEqual(set(GROUPS), {"dam_daily", "dam_medium"})
        self.assertNotIn("dam_small_tele", GROUPS)
        self.assertNotIn("dam_hourly", GROUPS)

    def test_a_bare_date_parses_as_bangkok_local(self):
        at = _dam_time("2026-09-16")
        self.assertIsNotNone(at)
        self.assertEqual(at.isoformat(), "2026-09-15T17:00:00+00:00")  # midnight ICT

    def test_a_timestamped_row_parses_too(self):
        at = _dam_time("2026-09-16 14:00")
        self.assertEqual(at.isoformat(), "2026-09-16T07:00:00+00:00")

    def test_rubbish_dates_are_refused_not_guessed(self):
        for v in (None, "", "not a date"):
            self.assertIsNone(_dam_time(v))

    def test_the_freshness_gate_sits_in_the_real_gap(self):
        # Measured: 448 rows within 1 day, then nothing until 365 days out.
        self.assertGreater(MAX_READING_AGE_DAYS, 2)
        self.assertLess(MAX_READING_AGE_DAYS, 365)

    def test_a_2021_reservoir_is_outside_the_window(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 9, 16, tzinfo=timezone.utc)
        self.assertLess(_dam_time("2021-10-26"), now - timedelta(days=MAX_READING_AGE_DAYS))

    def test_storage_thresholds_are_ordered_and_named_for_a_reservoir(self):
        # Above 100% means above normal full level, NOT a dam failing.
        self.assertGreater(SPILLING_PERCENT, HIGH_PERCENT)
        self.assertEqual(SPILLING_PERCENT, 100.0)


class TestDamFetchDegrades(unittest.TestCase):
    def test_a_dead_dam_feed_returns_health_not_an_exception(self):
        original = thaidam.fetch_json
        thaidam.fetch_json = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("503"))
        try:
            gj, h = thaidam.fetch_dams()
        finally:
            thaidam.fetch_json = original
        self.assertIsNone(gj)
        self.assertFalse(h.ok)
        self.assertIn("503", h.error)

    def test_an_unexpected_shape_is_refused(self):
        original = thaidam.fetch_json
        thaidam.fetch_json = lambda *_a, **_k: {"nope": 1}
        try:
            gj, h = thaidam.fetch_dams()
        finally:
            thaidam.fetch_json = original
        self.assertIsNone(gj)
        self.assertFalse(h.ok)


if __name__ == "__main__":
    unittest.main()
