"""Yala municipal CCTV: the camera layer, and the two ways it could lie.

Every test here exists because getting it wrong would put a picture next to a number
and invite a reader to believe they are about the same water.

  * `signal_status` says all five cameras are up. One has no stream at all. Believing
    the source's own status field is the `situation_level` mistake, which was null on
    302 of 306 overtopped stations. Liveness is measured, and a failure to measure is
    not the same as a camera being dark.
  * A camera five kilometres from a gauge is not evidence about that gauge. Pairing has
    to refuse rather than reach.
"""

from __future__ import annotations

import sys
import types
import unittest
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
    """Run the fetch with the network replaced by fixed answers."""
    with mock.patch.object(C, "fetch_json", return_value=rows), \
         mock.patch.object(C, "probe_stream", side_effect=probe_result):
        return C.fetch_cameras([NEAR, FAR])


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
            doc, health = C.fetch_cameras(None)
        self.assertTrue(health.ok)
        self.assertEqual(len(doc["features"]), 2)
        self.assertIsNone(doc["features"][0]["properties"]["gauge_id"])


class TestRowsThatCannotBeDrawnAreDropped(unittest.TestCase):
    def _one(self, **overrides):
        row = {**ROWS[1], **overrides}
        with mock.patch.object(C, "fetch_json", return_value=[row]), \
             mock.patch.object(C, "probe_stream", return_value=True):
            return C.fetch_cameras([NEAR])

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
        doc, _ = fetch_with(ROWS, lambda key, **kw: True)
        self.assertEqual(doc["licence"], "unknown")
        self.assertIn("No licence", doc["note"])

    def test_the_operator_is_attributed(self):
        doc, _ = fetch_with(ROWS, lambda key, **kw: True)
        self.assertIn("Yala City Municipality", doc["attribution"])

    def test_an_upstream_failure_is_optional_not_fatal(self):
        """A dead vendor must not mark the whole snapshot unhealthy: that pins the
        stale banner on for ever, and a warning always showing is one nobody reads."""
        with mock.patch.object(C, "fetch_json", side_effect=RuntimeError("HTTP 503")):
            doc, health = C.fetch_cameras([NEAR])
        self.assertIsNone(doc)
        self.assertFalse(health.ok)
        self.assertTrue(health.optional)
        self.assertIn("503", health.error)

    def test_probing_can_be_switched_off_entirely(self):
        """The build probes; a caller that only wants the list should not mint
        sessions on someone else's AWS account to get it."""
        with mock.patch.object(C, "fetch_json", return_value=ROWS), \
             mock.patch.object(C, "probe_stream") as probe:
            doc, _ = C.fetch_cameras([NEAR], probe=False)
        probe.assert_not_called()
        self.assertIsNone(doc["features"][0]["properties"]["stream_ok"])
        self.assertIsNone(doc["features"][0]["properties"]["stream_checked_at"])
