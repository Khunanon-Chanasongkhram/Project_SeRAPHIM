"""Google Flood Hub adapter.

⚠️ These fixtures are built from Google's published reference, NOT from a live
response, because the API is gated behind a pilot waitlist and no key was available
when this was written. They prove the adapter's *logic* — what it refuses, what it
never invents, how it degrades — and they do not prove the field names are right.
`seraphim.cli googlefloods --probe` is what settles that, against a real payload.

So most of what is tested here is refusal: an unknown severity must not become "no
flooding", a missing location must not become Null Island, and a missing key must not
become a failed build.
"""

from __future__ import annotations

import unittest

from seraphim.adapters import googlefloods as gf


def _status(gid="HYBAS_1", severity="SEVERE", lat=13.7, lon=100.5, **kw):
    row = {
        "gaugeId": gid,
        "gaugeLocation": {"latitude": lat, "longitude": lon},
        "severity": severity,
        "forecastTrend": "RISE",
        "qualityVerified": True,
        "issuedTime": "2026-09-16T06:00:00Z",
        "forecastTimeRange": {"start": "2026-09-16T06:00:00Z",
                              "end": "2026-09-23T06:00:00Z"},
        "forecastChange": {"valueChange": {"lowerBound": 0.4, "upperBound": 1.2}},
        "inundationMapSet": {"inundationMaps": [
            {"level": "HIGH", "serializedPolygonId": "p1"},
            {"level": "MEDIUM", "serializedPolygonId": "p2"}]},
        "mapInferenceType": "MODEL",
        "source": "HYBAS",
    }
    row.update(kw)
    return row


class Helpers(unittest.TestCase):
    def test_pick_accepts_both_json_spellings(self):
        """REST emits lowerCamelCase; the reference documents snake_case. Read both."""
        self.assertEqual(gf._pick({"gaugeId": "a"}, "gaugeId", "gauge_id"), "a")
        self.assertEqual(gf._pick({"gauge_id": "b"}, "gaugeId", "gauge_id"), "b")
        self.assertIsNone(gf._pick({"other": 1}, "gaugeId", "gauge_id"))
        self.assertIsNone(gf._pick("not a dict", "gaugeId"))

    def test_pick_treats_explicit_null_as_absent(self):
        self.assertEqual(gf._pick({"a": None, "b": 2}, "a", "b"), 2)

    def test_latlng_handles_the_omitted_zero(self):
        """LatLng drops a zero field on the wire, so a gauge on the equator arrives
        with only a longitude. Dropping it would lose real points."""
        self.assertEqual(gf._latlng({"longitude": 100.5}), (0.0, 100.5))
        self.assertEqual(gf._latlng({"latitude": 13.7}), (13.7, 0.0))

    def test_latlng_refuses_null_island_and_nonsense(self):
        self.assertIsNone(gf._latlng({}))
        self.assertIsNone(gf._latlng({"latitude": 0, "longitude": 0}))
        self.assertIsNone(gf._latlng({"latitude": 999, "longitude": 1}))
        self.assertIsNone(gf._latlng({"latitude": 1, "longitude": -999}))
        self.assertIsNone(gf._latlng(None))

    def test_iso_never_invents_a_time(self):
        self.assertEqual(gf._iso("2026-09-16T06:00:00Z"), "2026-09-16T06:00:00+00:00")
        self.assertIsNone(gf._iso("whenever"))
        self.assertIsNone(gf._iso(None))
        self.assertIsNone(gf._iso(""))

    def test_naive_timestamps_are_read_as_utc(self):
        self.assertEqual(gf._iso("2026-09-16T06:00:00"), "2026-09-16T06:00:00+00:00")


class SeverityMapping(unittest.TestCase):
    def test_known_severities(self):
        self.assertEqual(gf.SEVERITY_LEVEL["EXTREME"], 5)
        self.assertEqual(gf.SEVERITY_LEVEL["SEVERE"], 4)
        self.assertEqual(gf.SEVERITY_LEVEL["ABOVE_NORMAL"], 3)
        self.assertEqual(gf.SEVERITY_LEVEL["NO_FLOODING"], 1)

    def test_unknown_is_absent_not_calm(self):
        """The whole point: "we do not know" must never render as "no flooding"."""
        self.assertNotIn("UNKNOWN", gf.SEVERITY_LEVEL)
        self.assertNotIn("SEVERITY_UNSPECIFIED", gf.SEVERITY_LEVEL)


class Fetch(unittest.TestCase):
    def _run(self, pages, regions=("TH",), **kw):
        """Stub the network. `pages` is a list of response dicts, served in order."""
        seq = list(pages)
        sent: list[tuple[str, dict]] = []

        def fake_post(url, body, timeout=0):
            sent.append((url, body))
            if not seq:
                return {"floodStatuses": []}
            item = seq.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        orig = gf.post_json
        gf.post_json = fake_post
        try:
            doc, health = gf.fetch_google_floods(key="k", regions=regions, **kw)
        finally:
            gf.post_json = orig
        return doc, health, sent

    def test_no_key_is_not_a_failure(self):
        doc, health = gf.fetch_google_floods(key="")
        self.assertIsNone(doc)
        self.assertTrue(health.optional)
        self.assertIn("GOOGLE_FLOOD_API_KEY", health.error)

    def test_happy_path(self):
        doc, health, sent = self._run([{"floodStatuses": [_status()]}])
        self.assertTrue(health.ok)
        self.assertEqual(len(doc["features"]), 1)
        p = doc["features"][0]["properties"]
        self.assertEqual(p["level"], 4)
        self.assertEqual(p["severity"], "SEVERE")
        self.assertEqual(p["id"], "gfh:HYBAS_1")
        self.assertEqual(p["trend"], "RISE")
        self.assertEqual(p["inundation_maps"], 2)
        self.assertEqual(p["change_low"], 0.4)
        self.assertEqual(p["change_high"], 1.2)
        self.assertEqual(doc["features"][0]["geometry"]["coordinates"], [100.5, 13.7])
        self.assertIn("CC BY 4.0", doc["attribution"])

    def test_the_key_is_sent_and_the_region_asked_for(self):
        _doc, _h, sent = self._run([{"floodStatuses": [_status()]}])
        url, body = sent[0]
        self.assertIn("key=k", url)
        self.assertIn(gf.SEARCH_STATUS, url)
        self.assertEqual(body["regionCode"], "TH")
        self.assertFalse(body["includeNonQualityVerified"])

    def test_unknown_severity_is_dropped_and_counted(self):
        doc, health, _ = self._run([{"floodStatuses": [
            _status(gid="a", severity="UNKNOWN"),
            _status(gid="b", severity="SEVERITY_UNSPECIFIED"),
            _status(gid="c", severity="EXTREME"),
        ]}])
        self.assertEqual(len(doc["features"]), 1)
        self.assertTrue(any("no_severity" in w for w in health.warnings))

    def test_a_new_severity_value_is_dropped_not_guessed(self):
        """If Google adds a class, we must not colour it by accident."""
        doc, health, _ = self._run([{"floodStatuses": [
            _status(gid="a", severity="CATASTROPHIC_SOMETHING"),
            _status(gid="b", severity="SEVERE")]}])
        self.assertEqual(len(doc["features"]), 1)
        self.assertTrue(any("no_severity" in w for w in health.warnings))

    def test_missing_location_is_dropped(self):
        row = _status(gid="a")
        del row["gaugeLocation"]
        doc, health, _ = self._run([{"floodStatuses": [row, _status(gid="b")]}])
        self.assertEqual(len(doc["features"]), 1)
        self.assertTrue(any("no_location" in w for w in health.warnings))

    def test_duplicate_gauge_ids_collapse(self):
        doc, health, _ = self._run([{"floodStatuses": [_status(gid="x"), _status(gid="x")]}])
        self.assertEqual(len(doc["features"]), 1)
        self.assertTrue(any("duplicate" in w for w in health.warnings))

    def test_snake_case_payload_parses_identically(self):
        """Whichever spelling the live API turns out to emit, this must work."""
        row = {
            "gauge_id": "HYBAS_9",
            "gauge_location": {"latitude": 13.7, "longitude": 100.5},
            "severity": "EXTREME",
            "forecast_trend": "FALL",
            "quality_verified": True,
            "issued_time": "2026-09-16T06:00:00Z",
            "forecast_time_range": {"start": "2026-09-16T06:00:00Z"},
            "forecast_change": {"value_change": {"lower_bound": 1.0, "upper_bound": 2.0}},
            "inundation_map_set": {"inundation_maps": [{"level": "HIGH"}]},
        }
        doc, _h, _ = self._run([{"flood_statuses": [row]}])
        p = doc["features"][0]["properties"]
        self.assertEqual(p["level"], 5)
        self.assertEqual(p["trend"], "FALL")
        self.assertEqual(p["change_high"], 2.0)
        self.assertEqual(p["inundation_maps"], 1)

    def test_worst_first(self):
        doc, _h, _ = self._run([{"floodStatuses": [
            _status(gid="a", severity="NO_FLOODING", lat=1.0),
            _status(gid="b", severity="EXTREME", lat=2.0),
            _status(gid="c", severity="ABOVE_NORMAL", lat=3.0)]}])
        self.assertEqual([f["properties"]["level"] for f in doc["features"]], [5, 3, 1])

    def test_pagination_follows_the_token(self):
        doc, _h, sent = self._run([
            {"floodStatuses": [_status(gid="a")], "nextPageToken": "t1"},
            {"floodStatuses": [_status(gid="b")]},
        ])
        self.assertEqual(len(doc["features"]), 2)
        self.assertEqual(sent[1][1]["pageToken"], "t1")

    def test_paging_is_bounded(self):
        """An upstream paging bug must not spend somebody else's quota indefinitely."""
        forever = [{"floodStatuses": [_status(gid=f"g{i}")], "nextPageToken": "t"}
                   for i in range(gf.MAX_PAGES + 5)]
        _doc, health, sent = self._run(forever)
        self.assertEqual(len(sent), gf.MAX_PAGES)
        self.assertTrue(any("ceiling" in w for w in health.warnings))

    def test_one_dead_region_does_not_lose_the_others(self):
        doc, health, _ = self._run(
            [RuntimeError("500 from upstream"), {"floodStatuses": [_status(gid="gb")]}],
            regions=("TH", "GB"))
        self.assertTrue(health.ok)
        self.assertEqual(len(doc["features"]), 1)
        self.assertTrue(any("TH:" in w for w in health.warnings))

    def test_empty_everywhere_is_reported_as_an_error(self):
        doc, health, _ = self._run([{"floodStatuses": []}])
        self.assertIsNone(doc)
        self.assertFalse(health.ok)
        self.assertIn("no usable flood statuses", health.error)

    def test_a_non_list_payload_does_not_crash(self):
        doc, health, _ = self._run([{"floodStatuses": {"unexpected": "object"}}])
        self.assertIsNone(doc)
        self.assertTrue(any("expected a list" in w for w in health.warnings))

    def test_lower_confidence_points_are_opt_in(self):
        _doc, _h, sent = self._run([{"floodStatuses": [_status()]}],
                                   include_unverified=True)
        self.assertTrue(sent[0][1]["includeNonQualityVerified"])


if __name__ == "__main__":
    unittest.main()
