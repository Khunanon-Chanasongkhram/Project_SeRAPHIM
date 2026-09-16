"""The historical flood archive, and the response cap that makes it non-obvious.

GDACS caps a SEARCH response at 100 features and offers no paging parameter, so a
single wide query returns the newest 100 events and looks exactly like a complete
archive. Probed 2026-09-16: a 2005-2026 query returned 100 events, all from 2021
onward, and did not contain the 2011 Thailand flood; a query scoped to 2011 returns it
normally. Hence one request per year, and hence these tests.
"""

from __future__ import annotations

import unittest

from seraphim.adapters import hazards


def _event(eid: int, frm: str, to: str, alert: str = "Orange",
           country: str = "Thailand", lon: float = 100.5, lat: float = 13.7) -> dict:
    return {
        "geometry": {"coordinates": [lon, lat]},
        "properties": {
            "eventid": eid, "alertlevel": alert, "eventname": "", "name": "Flood",
            "country": country, "iso3": "THA",
            "affectedcountries": [{"countryname": country}],
            "fromdate": frm, "todate": to,
            "url": {"report": f"https://example.invalid/{eid}"},
        },
    }


class FetchPastFloods(unittest.TestCase):
    def _run(self, by_year: dict, years: list[int]):
        seen: list[str] = []

        def fake(url, timeout=0):
            seen.append(url)
            for y, payload in by_year.items():
                if f"fromDate={y}-01-01" in url:
                    if isinstance(payload, Exception):
                        raise payload
                    return {"features": payload}
            return {"features": []}

        orig = hazards.fetch_json
        hazards.fetch_json = fake
        try:
            return (*hazards.fetch_past_floods(years=years), seen)
        finally:
            hazards.fetch_json = orig

    def test_one_request_per_year(self):
        """The whole reason this is not a single wide query."""
        _doc, _h, seen = self._run({}, [2011, 2012, 2013])
        self.assertEqual(len(seen), 3)
        self.assertTrue(any("fromDate=2011-01-01" in u for u in seen))
        self.assertTrue(any("toDate=2013-12-31" in u for u in seen))

    def test_only_orange_and_red_are_requested(self):
        """Green covers a great many events nobody would call a flood."""
        _doc, _h, seen = self._run({}, [2020])
        self.assertIn("alertlevel=Orange;Red", seen[0])

    def test_shape_of_a_published_event(self):
        doc, health, _ = self._run(
            {2011: [_event(3850, "2011-08-05T00:00:00", "2012-01-09T23:59:59")]}, [2011])
        self.assertTrue(health.ok)
        p = doc["features"][0]["properties"]
        self.assertEqual(p["from"], "2011-08-05")
        self.assertEqual(p["to"], "2012-01-09")
        self.assertEqual(p["days"], 158)      # the real 2011 Thailand flood's length
        self.assertEqual(p["countries"], "Thailand")
        self.assertEqual(p["year"], 2011)

    def test_duplicate_events_across_years_are_collapsed(self):
        """A flood spanning new year is returned by both years' queries."""
        e = _event(3850, "2011-08-05T00:00:00", "2012-01-09T23:59:59")
        doc, _h, _ = self._run({2011: [e], 2012: [e]}, [2011, 2012])
        self.assertEqual(len(doc["features"]), 1)

    def test_a_clipped_year_is_reported_not_hidden(self):
        """Hitting the cap means that year is incomplete, and silence would hide it."""
        rows = [_event(i, f"2020-01-01T00:00:00", "2020-01-02T00:00:00") for i in range(100)]
        _doc, health, _ = self._run({2020: rows}, [2020])
        self.assertTrue(any("cap" in w for w in health.warnings))

    def test_one_failed_year_does_not_lose_the_others(self):
        doc, health, _ = self._run({
            2011: RuntimeError("boom"),
            2012: [_event(1, "2012-06-01T00:00:00", "2012-06-05T00:00:00")],
        }, [2011, 2012])
        self.assertTrue(health.ok)
        self.assertEqual(len(doc["features"]), 1)
        self.assertTrue(any("2011" in w for w in health.warnings))

    def test_events_without_coordinates_are_dropped(self):
        bad = _event(9, "2020-01-01T00:00:00", "2020-01-02T00:00:00")
        bad["geometry"]["coordinates"] = []
        _doc, health, _ = self._run({2020: [bad]}, [2020])
        self.assertFalse(health.ok)

    def test_newest_first(self):
        doc, _h, _ = self._run({
            2011: [_event(1, "2011-08-05T00:00:00", "2011-09-01T00:00:00")],
            2025: [_event(2, "2025-12-03T00:00:00", "2025-12-06T00:00:00")],
        }, [2011, 2025])
        order = [f["properties"]["from"] for f in doc["features"]]
        self.assertEqual(order, ["2025-12-03", "2011-08-05"])

    def test_nothing_found_is_a_failure_not_an_empty_layer(self):
        """An empty archive is a broken fetch, not a world with no floods in it."""
        doc, health, _ = self._run({}, [2020])
        self.assertIsNone(doc)
        self.assertFalse(health.ok)

    def test_a_malformed_date_does_not_crash_the_batch(self):
        e = _event(1, "not-a-date", "also-not")
        doc, health, _ = self._run({2020: [e]}, [2020])
        self.assertTrue(health.ok)
        self.assertIsNone(doc["features"][0]["properties"]["days"])


if __name__ == "__main__":
    unittest.main()
