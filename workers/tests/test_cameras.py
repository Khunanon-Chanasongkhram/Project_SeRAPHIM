"""Street cameras, Yala and Hat Yai: the ways a picture can lie about water.

Every test here exists because getting it wrong would put a picture next to a number
and invite a reader to believe they are about the same water, now.

  * **Status fields lie, in all three sources.** Yala's `signal_status` is 1 on all five
    cameras, one of which has no stream. Hat Yai's `enable` is 1 on all 35 entries, one
    of which last produced an image 1,018 days ago. Both are the `situation_level`
    mistake, which was null on 302 of 306 overtopped stations. None is read: liveness is
    measured, and a failure to *measure* is not the same as a camera being dark.
  * **Hat Yai timestamps are Thai wall-clock, and the HTTP headers lie.** `atDate` is
    UTC+7 with no offset, and the JPEGs carry `last-modified: now` whatever their age.
    Reading either naively turns a six-month-old photograph into a live one.
  * **A camera five kilometres from a gauge is not evidence about that gauge.** Pairing
    refuses rather than reaches.
  * **Not everything in a camera feed is a camera.** Hat Yai mixes in a weather radar, a
    satellite image and a synoptic chart. Drawn at a point, each would claim to be what
    that place looks like.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim.adapters import cameras as C  # noqa: E402

# The real payload, as probed on 2026-09-18.
ROWS = [
    {"signal_status": 1, "id": "BaanRom-04", "name": "บ้านร่ม 04", "zone": "Baan Rom",
     "customerCode": "03950102", "deviceCode": "C7D84457",
     "lat": 6.537641398, "lng": 101.2622171},
    {"signal_status": 1, "id": "WATER-PUMP-PA-05", "name": "สะพานท่าสาป 05",
     "zone": "WATER-PUMP-PA", "customerCode": "03950102", "deviceCode": "367D8832",
     "lat": 6.55255, "lng": 101.26785},
]


def gauge(sid, name, lat, lon):
    return types.SimpleNamespace(
        station=types.SimpleNamespace(id=sid, name=name, lat=lat, lon=lon))


#: 60 m from the PA-05 camera, and 1.8 km from BaanRom-04.
NEAR = gauge("thaiwater:1", "บ้านท่าสาบ", 6.5530, 101.2680)
FAR = gauge("thaiwater:2", "somewhere else", 6.8000, 101.5000)


def fetch_with(rows, probe_result):
    """Run the Yala fetch with the network replaced by fixed answers."""
    with mock.patch.object(C, "fetch_json", return_value=rows), \
         mock.patch.object(C, "probe_stream", side_effect=probe_result):
        feats, health = C.fetch_yala([NEAR, FAR], probe=True)
    return {"features": feats, **C.OPERATORS["yala"]}, health


class TestSignalStatusIsNotBelieved(unittest.TestCase):
    def test_a_camera_claiming_signal_can_still_be_published_as_dark(self):
        doc, _ = fetch_with(ROWS, lambda key, **kw: key.endswith("367D8832"))
        by = {f["properties"]["id"]: f["properties"] for f in doc["features"]}
        self.assertEqual(by["yala:BaanRom-04"]["stream_ok"], False)
        self.assertEqual(by["yala:WATER-PUMP-PA-05"]["stream_ok"], True)

    def test_the_disagreement_is_recorded_rather_than_quietly_resolved(self):
        _, health = fetch_with(ROWS, lambda key, **kw: key.endswith("367D8832"))
        self.assertTrue(any("signal_status" in w for w in health.warnings), health.warnings)

    def test_signal_status_is_never_published(self):
        """Publishing it would invite the UI to render it. It is proven wrong."""
        doc, _ = fetch_with(ROWS, lambda key, **kw: True)
        for f in doc["features"]:
            self.assertNotIn("signal_status", f["properties"])


class TestAFailureToCheckIsNotADarkCamera(unittest.TestCase):
    """None and False must not collapse. One is the municipality's outage, the other
    is ours, and rendering ours as theirs is a false report about public safety kit."""

    def test_unknown_is_preserved_as_none(self):
        doc, _ = fetch_with(ROWS, lambda key, **kw: None)
        for f in doc["features"]:
            self.assertIsNone(f["properties"]["stream_ok"])

    def test_unknown_is_not_counted_as_live_in_the_disagreement_warning(self):
        _, health = fetch_with(ROWS, lambda key, **kw: None)
        joined = " ".join(health.warnings)
        self.assertIn("0 live", joined)
        self.assertIn("2 unknown", joined)


class TestProbeReadsTheVendorsOddResponses(unittest.TestCase):
    """The Lambda returns its own failures inside an HTTP 200, and wraps the real
    payload as a JSON *string*. Both have to be unwrapped, and neither can be trusted
    from the status code."""

    def _probe(self, payload):
        body = __import__("json").dumps(payload).encode()
        resp = mock.MagicMock()
        resp.status = 200
        resp.read.return_value = body
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda *a: False
        with mock.patch.object(C.urllib.request, "urlopen", return_value=resp):
            return C.probe_stream("03950102-367D8832")

    def test_a_stringified_body_with_a_url_is_live(self):
        self.assertIs(self._probe(
            {"statusCode": 200,
             "body": '{"live_stream_url": "https://x.amazonaws.com/hls/v1/a.m3u8?S=1"}'}),
            True)

    def test_an_error_inside_a_200_is_dark_not_an_outage(self):
        self.assertIs(self._probe(
            {"errorMessage": "No fragments found in the stream",
             "errorType": "ResourceNotFoundException"}), False)

    def test_a_shape_we_do_not_recognise_is_unknown(self):
        self.assertIsNone(self._probe({"statusCode": 200, "body": "not json"}))

    def test_a_body_without_a_url_is_dark(self):
        self.assertIs(self._probe({"statusCode": 200, "body": '{"kvs_stream_name": "x"}'}), False)

    def test_a_network_failure_is_unknown_not_dark(self):
        with mock.patch.object(C.urllib.request, "urlopen",
                               side_effect=C.urllib.error.URLError("down")):
            self.assertIsNone(C.probe_stream("a-b"))


class TestPairingRefusesToReach(unittest.TestCase):
    def test_a_camera_on_the_gauges_bridge_is_paired(self):
        doc, _ = fetch_with(ROWS, lambda key, **kw: True)
        pa = next(f["properties"] for f in doc["features"]
                  if f["properties"]["id"] == "yala:WATER-PUMP-PA-05")
        self.assertEqual(pa["gauge_id"], "thaiwater:1")
        self.assertLess(pa["gauge_km"], 0.2)

    def test_a_camera_too_far_from_any_gauge_is_published_unpaired(self):
        far_cam = [{**ROWS[0], "lat": 6.90, "lng": 101.80}]
        doc, _ = fetch_with(far_cam, lambda key, **kw: True)
        p = doc["features"][0]["properties"]
        self.assertIsNone(p["gauge_id"])
        self.assertIsNone(p["gauge_km"])

    def test_the_limit_is_enforced_at_the_stated_distance(self):
        self.assertEqual(C.MAX_GAUGE_PAIR_KM, 1.0)
        km = C._haversine_km(6.55255, 101.26785, NEAR.station.lat, NEAR.station.lon)
        self.assertLess(km, C.MAX_GAUGE_PAIR_KM)

    def test_no_gauges_at_all_still_publishes_the_layer(self):
        with mock.patch.object(C, "fetch_json", return_value=ROWS), \
             mock.patch.object(C, "probe_stream", return_value=True):
            feats, health = C.fetch_yala(None)
        self.assertTrue(health.ok)
        self.assertEqual(len(feats), 2)
        self.assertIsNone(feats[0]["properties"]["gauge_id"])


class TestRowsThatCannotBeDrawnAreDropped(unittest.TestCase):
    def _one(self, **overrides):
        row = {**ROWS[1], **overrides}
        with mock.patch.object(C, "fetch_json", return_value=[row]), \
             mock.patch.object(C, "probe_stream", return_value=True):
            feats, health = C.fetch_yala([NEAR])
        return ({"features": feats} if feats else None), health

    def test_a_camera_with_no_stream_codes_is_dropped(self):
        doc, health = self._one(deviceCode=None)
        self.assertIsNone(doc)
        self.assertTrue(any("no_stream_key" in w for w in health.warnings), health.warnings)

    def test_a_camera_with_no_location_is_dropped(self):
        doc, health = self._one(lat=None)
        self.assertIsNone(doc)
        self.assertTrue(any("no_location" in w for w in health.warnings), health.warnings)

    def test_a_camera_outside_yala_is_dropped(self):
        """A coordinate error would move a camera onto a river it cannot see."""
        doc, health = self._one(lat=13.75, lng=100.5)   # Bangkok
        self.assertIsNone(doc)
        self.assertTrue(any("outside_yala" in w for w in health.warnings), health.warnings)

    def test_a_customer_code_keeps_its_leading_zero(self):
        """`03950102` stringified through int() becomes 3950102 and mints nothing."""
        doc, _ = self._one()
        self.assertEqual(doc["features"][0]["properties"]["customer_code"], "03950102")
        self.assertTrue(doc["features"][0]["properties"]["stream_key"].startswith("03950102-"))


class TestTheLayerIsHonestAboutItself(unittest.TestCase):
    def test_no_session_url_is_ever_published(self):
        """Session URLs expire. One baked into a CDN snapshot is dead on arrival, and
        a dead player is indistinguishable from a dead camera."""
        doc, _ = fetch_with(ROWS, lambda key, **kw: True)
        blob = __import__("json").dumps(doc)
        self.assertNotIn("SessionToken", blob)
        self.assertNotIn("live_stream_url", blob)

    def test_the_absent_licence_is_stated_not_omitted(self):
        """"unknown" on its own is a gap in a field. The finding is that there is no
        licence, no terms and no named owner, so it is said in words."""
        self.assertEqual(C.OPERATORS["yala"]["licence"], "unknown")
        self.assertIn("No licence", C.OPERATORS["yala"]["licence_note"])
        self.assertIn("no owner is named", C.OPERATORS["yala"]["licence_note"])

    def test_the_operator_is_attributed(self):
        doc, _ = fetch_with(ROWS, lambda key, **kw: True)
        self.assertIn("Yala City Municipality", doc["attribution"])

    def test_an_upstream_failure_is_optional_not_fatal(self):
        """A dead vendor must not mark the whole snapshot unhealthy: that pins the
        stale banner on for ever, and a warning always showing is one nobody reads."""
        with mock.patch.object(C, "fetch_json", side_effect=RuntimeError("HTTP 503")):
            feats, health = C.fetch_yala([NEAR])
        self.assertEqual(feats, [])
        self.assertFalse(health.ok)
        self.assertTrue(health.optional)
        self.assertIn("503", health.error)

    def test_probing_can_be_switched_off_entirely(self):
        """The build probes; a caller that only wants the list should not mint
        sessions on someone else's AWS account to get it."""
        with mock.patch.object(C, "fetch_json", return_value=ROWS), \
             mock.patch.object(C, "probe_stream") as probe:
            feats, _ = C.fetch_yala([NEAR], probe=False)
        probe.assert_not_called()
        self.assertIsNone(feats[0]["properties"]["stream_ok"])
        self.assertIsNone(feats[0]["properties"]["stream_checked_at"])


# --------------------------------------------------------------------- Hat Yai

#: Real records, as probed on 2026-09-18. `atDate` is Thai wall-clock (UTC+7).
HY_NOW = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)   # 17:00 in Thailand


def hy(name, title, at, lat=7.0022, lon=100.4558, **kw):
    row = {"cameraId": 1, "code": "S8", "enable": 1, "name": name, "title": title,
           "location": {"latitude": lat, "longitude": lon},
           "photo": f"https://hatyaicityclimate.org/floodphoto/last/{name}.jpg",
           "atDate": at, "sponsorName": "สำนักงานทรัพยากรน้ำภาค 8",
           "sponsorText": "credit", "sponsorUrl": "http://water8.net"}
    row.update(kw)
    return row


def fetch_hy(rows, now=HY_NOW, states=None):
    with mock.patch.object(C, "fetch_json", return_value={"count": len(rows), "items": rows}):
        return C.fetch_hatyai(states if states is not None else [HY_GAUGE], now=now)


#: 30 m from the default camera position above.
HY_GAUGE = gauge("thaiwater:9", "บ้านหาดใหญ่ใน", 7.00230, 100.45605)


class TestHatYaiTimestampsAreThaiLocal(unittest.TestCase):
    """`atDate` is naive wall-clock at UTC+7. Reading it as UTC ages every image by
    seven hours, which turns a picture taken one minute ago into one seven hours old
    and would hide exactly the images worth looking at during a flood."""

    def test_a_photo_taken_a_minute_ago_is_a_minute_old(self):
        feats, health = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:59:00")])
        self.assertTrue(health.ok, health.error)
        self.assertAlmostEqual(feats[0]["properties"]["age_minutes"], 1.0, places=1)

    def test_the_published_timestamp_is_utc(self):
        feats, _ = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:59:00")])
        self.assertEqual(feats[0]["properties"]["observed_at"],
                         "2026-09-18T09:59:00+00:00")

    def test_reading_it_as_utc_would_have_dropped_this_camera(self):
        """A live image at 17:00 Thai time read as 17:00 UTC is 7 h in the future;
        read as if it were UTC-naive it is 7 h old. Either way the offset is not
        cosmetic, so this pins the conversion rather than trusting it."""
        feats, _ = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:30:00")])
        self.assertLess(feats[0]["properties"]["age_minutes"], 60)


class TestEnableIsNotBelievedEither(unittest.TestCase):
    """Third source, third lying status field. `enable` is 1 on all 35 entries,
    including one whose last image is 1,018 days old."""

    def test_a_long_dead_camera_is_dropped_despite_enable_1(self):
        feats, health = fetch_hy([hy("dead", "สะพาน", "2023-12-05 09:00:00", enable=1)])
        self.assertEqual(feats, [])
        self.assertTrue(any("stale" in w for w in health.warnings), health.warnings)

    def test_the_disagreement_is_recorded(self):
        rows = [hy("live", "สะพาน", "2026-09-18 16:59:00"),
                hy("dead", "สะพาน", "2023-12-05 09:00:00")]
        _, health = fetch_hy(rows)
        self.assertTrue(any("`enable`" in w for w in health.warnings), health.warnings)

    def test_enable_is_never_published(self):
        feats, _ = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:59:00")])
        self.assertNotIn("enable", feats[0]["properties"])

    def test_the_cutoff_matches_the_dutch_precedent(self):
        self.assertEqual(C.MAX_PHOTO_AGE_HOURS, 24.0)


class TestPicturesOfTheSkyAreNotCameras(unittest.TestCase):
    """The feed mixes in a weather radar, a satellite image and a synoptic chart.
    Drawing one at a point would claim it is what that place looks like."""

    def test_radar_satellite_and_weather_charts_are_dropped(self):
        rows = [hy("radartmd", "เรดาร์สทิงพระ(สงขลา)", "2026-09-18 16:59:00"),
                hy("weather", "ภาพถ่ายดาวเทียม", "2026-09-18 16:59:00"),
                hy("wc", "แผนที่อากาศ", "2026-09-18 16:59:00")]
        feats, health = fetch_hy(rows)
        self.assertEqual(feats, [])
        self.assertTrue(any("not_a_camera" in w for w in health.warnings), health.warnings)

    def test_a_real_camera_beside_them_survives(self):
        rows = [hy("radartmd", "เรดาร์สทิงพระ(สงขลา)", "2026-09-18 16:59:00"),
                hy("bangsala", "สะพานบางศาลา", "2026-09-18 16:59:00")]
        feats, _ = fetch_hy(rows)
        self.assertEqual([f["properties"]["id"] for f in feats], ["hatyai:bangsala"])


class TestHatYaiRowsThatCannotBeDrawn(unittest.TestCase):
    def test_a_missing_location_is_dropped(self):
        row = hy("a", "สะพาน", "2026-09-18 16:59:00")
        row["location"] = {}
        feats, health = fetch_hy([row])
        self.assertEqual(feats, [])
        self.assertTrue(any("no_location" in w for w in health.warnings), health.warnings)

    def test_a_camera_outside_songkhla_is_dropped(self):
        feats, health = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:59:00",
                                     lat=13.75, lon=100.5)])   # Bangkok
        self.assertEqual(feats, [])
        self.assertTrue(any("outside_songkhla" in w for w in health.warnings), health.warnings)

    def test_an_unparseable_timestamp_is_dropped_not_assumed_fresh(self):
        feats, health = fetch_hy([hy("a", "สะพาน", "")])
        self.assertEqual(feats, [])
        self.assertTrue(any("no_timestamp" in w for w in health.warnings), health.warnings)

    def test_a_non_https_photo_is_dropped(self):
        row = hy("a", "สะพาน", "2026-09-18 16:59:00")
        row["photo"] = "http://hatyaicityclimate.org/x.jpg"
        feats, _ = fetch_hy([row])
        self.assertEqual(feats, [])


class TestHatYaiCarriesItsCredit(unittest.TestCase):
    def test_the_per_camera_image_credit_travels_with_the_camera(self):
        """The images are supplied to SCCCRN by third parties. A site-level footer
        credit would drop that, and attribution is mandatory under CC BY-SA."""
        feats, _ = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:59:00")])
        p = feats[0]["properties"]
        self.assertEqual(p["sponsor_name"], "สำนักงานทรัพยากรน้ำภาค 8")
        self.assertEqual(p["sponsor_url"], "http://water8.net")

    def test_the_named_licence_is_published(self):
        self.assertEqual(C.OPERATORS["hatyai"]["licence"], "CC BY-SA 3.0")
        self.assertIn("creativecommons.org", C.OPERATORS["hatyai"]["licence_url"])

    def test_the_camera_is_paired_with_the_gauge_on_its_bridge(self):
        feats, _ = fetch_hy([hy("a", "สะพาน", "2026-09-18 16:59:00")])
        self.assertEqual(feats[0]["properties"]["gauge_id"], "thaiwater:9")
        self.assertLess(feats[0]["properties"]["gauge_km"], 0.1)


class TestOneOperatorFailingDoesNotTakeTheOther(unittest.TestCase):
    def test_a_dead_hatyai_still_publishes_yala(self):
        with mock.patch.object(C, "fetch_yala",
                               return_value=([{"type": "Feature", "properties": {
                                   "operator": "yala", "gauge_id": None}}],
                                   __import__("seraphim.models", fromlist=["x"])
                                   .SourceHealth(source="yala_cctv", ok=True))), \
             mock.patch.object(C, "fetch_hatyai",
                               return_value=([], __import__("seraphim.models",
                                   fromlist=["x"]).SourceHealth(
                                       source="hatyai_cctv", ok=False, error="HTTP 500"))):
            doc, health = C.fetch_cameras([])
        self.assertIsNotNone(doc)
        self.assertEqual(len(doc["features"]), 1)
        self.assertEqual([h.source for h in health], ["yala_cctv", "hatyai_cctv"])

    def test_both_dead_publishes_nothing_but_still_reports_both(self):
        M = __import__("seraphim.models", fromlist=["x"])
        with mock.patch.object(C, "fetch_yala",
                               return_value=([], M.SourceHealth(source="yala_cctv", ok=False))), \
             mock.patch.object(C, "fetch_hatyai",
                               return_value=([], M.SourceHealth(source="hatyai_cctv", ok=False))):
            doc, health = C.fetch_cameras([])
        self.assertIsNone(doc)
        self.assertEqual(len(health), 2)


class TestTheTwoOperatorsBoundsDoNotOverlap(unittest.TestCase):
    """Each operator's bounds exist to catch a coordinate error in *that* feed. If they
    overlapped, a Yala coordinate appearing in the Hat Yai feed would sail through the
    very check meant to stop it."""

    def test_a_yala_coordinate_is_outside_the_hatyai_bounds(self):
        self.assertTrue(C._in_bounds(6.552552, 101.267847, C.YALA_BOUNDS))
        self.assertFalse(C._in_bounds(6.552552, 101.267847, C.HATYAI_BOUNDS))

    def test_a_hatyai_coordinate_is_outside_the_yala_bounds(self):
        self.assertTrue(C._in_bounds(7.002231, 100.455775, C.HATYAI_BOUNDS))
        self.assertFalse(C._in_bounds(7.002231, 100.455775, C.YALA_BOUNDS))
