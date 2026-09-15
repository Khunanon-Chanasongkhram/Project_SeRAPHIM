"""Phase 4 tests: SOS schema, validation, privacy and triage.

These cover a system that people may rely on in a flood, so the tests are weighted
toward the failures that would hurt someone: a request silently lost, a duplicate
consuming a second boat, personal data leaking, or a queue that ignores the person
who has waited longest.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seraphim import devserver as ds  # noqa: E402
from seraphim.sos import (  # noqa: E402
    NEED_BY_CODE,
    cluster,
    derive_severity,
    escalate_for_age,
    geohash,
    has_vulnerable,
    needs_are_valid,
)

CONFORMANCE = ROOT.parent / "api" / "conformance" / "severity.json"
WORKER_JS = ROOT.parent / "api" / "src" / "index.js"

BASE = {"lat": 13.6, "lon": 100.7, "needs": ["food"], "consent": True}


def db():
    return ds.connect(":memory:")


def post(conn, **over):
    body = {**BASE, **over}
    ip = over.pop("_ip", None) or f"ip-{time.time_ns()}"
    return ds.submit(conn, body, ip_hash=ip)


class TestConformance(unittest.TestCase):
    """The Python spec and the JS Worker must agree. This file is the referee."""

    def setUp(self):
        self.spec = json.loads(CONFORMANCE.read_text(encoding="utf-8"))

    def test_severity_cases(self):
        for c in self.spec["cases"]:
            got = derive_severity(c["needs"], c.get("people_count"), c.get("water_depth_cm"))
            self.assertEqual(got, c["expect"], c["why"])

    def test_age_cases(self):
        for c in self.spec["age_cases"]:
            self.assertEqual(
                escalate_for_age(c["severity"], c["waiting_minutes"]), c["expect"], c["why"])

    def test_geohash_cases(self):
        for c in self.spec["geohash_cases"]:
            self.assertEqual(geohash(c["lat"], c["lon"], c["precision"]), c["expect"], c["why"])

    def test_worker_declares_the_same_needs(self):
        """A need present in one implementation and not the other would score
        differently depending on which server took the request."""
        js = WORKER_JS.read_text(encoding="utf-8")
        block = js.split("const NEEDS = {", 1)[1].split("};", 1)[0]
        js_codes = {ln.split(":", 1)[0].strip() for ln in block.splitlines()
                    if ":" in ln and "base" in ln}
        self.assertEqual(js_codes, set(NEED_BY_CODE), "need codes diverged between Python and JS")

    def test_worker_shares_the_thresholds(self):
        js = WORKER_JS.read_text(encoding="utf-8")
        for token in ("DEPTH_LIFE_THREATENING_CM = 150", "DEPTH_DANGEROUS_CM = 100",
                      "LARGE_GROUP = 10", "AGE_ESCALATE_MINUTES = 360",
                      "RATE_MAX_DEVICE = 8", "RATE_MAX_IP = 300",
                      "RATE_MAX_IP_CRITICAL = 900"):
            self.assertIn(token, js, f"threshold drift: {token}")


class TestSchema(unittest.TestCase):
    def test_constraints_reject_impossible_rows(self):
        conn = db()
        bad = [
            ("severity out of range", "INSERT INTO sos_requests (id,created_at,updated_at,status,"
             "severity,severity_auto,lat,lon,geohash,needs,consent_at,purge_after)"
             " VALUES ('a',1,1,'new',9,9,13,100,'x','[]',1,1)"),
            ("latitude out of range", "INSERT INTO sos_requests (id,created_at,updated_at,status,"
             "severity,severity_auto,lat,lon,geohash,needs,consent_at,purge_after)"
             " VALUES ('b',1,1,'new',3,3,999,100,'x','[]',1,1)"),
            ("unknown status", "INSERT INTO sos_requests (id,created_at,updated_at,status,"
             "severity,severity_auto,lat,lon,geohash,needs,consent_at,purge_after)"
             " VALUES ('c',1,1,'wat',3,3,13,100,'x','[]',1,1)"),
        ]
        for why, sql in bad:
            with self.assertRaises(sqlite3.IntegrityError, msg=why):
                conn.execute(sql)

    def test_queue_index_exists(self):
        names = {r[0] for r in db().execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("idx_sos_queue", names)
        self.assertIn("idx_sos_client", names)


class TestSubmission(unittest.TestCase):
    def test_accepts_a_valid_request(self):
        status, body = post(db())
        self.assertEqual(status, 201)
        self.assertTrue(body["ok"])
        self.assertEqual(body["severity"], 2)

    def test_consent_is_mandatory(self):
        status, body = post(db(), consent=False)
        self.assertEqual(status, 422)
        self.assertEqual(body["error"], "consent_required")

    def test_needs_are_mandatory(self):
        self.assertEqual(post(db(), needs=[])[1]["error"], "no_needs")

    def test_unknown_needs_are_discarded(self):
        self.assertEqual(post(db(), needs=["definitely_not_a_need"])[1]["error"], "no_needs")

    def test_location_is_mandatory_and_validated(self):
        for bad in ({"lat": None}, {"lat": 999}, {"lon": "somewhere"}):
            self.assertEqual(post(db(), **bad)[1]["error"], "bad_location")

    def test_severity_is_stored_and_derived(self):
        conn = db()
        post(conn, needs=["medical"])
        row = conn.execute("SELECT severity, severity_auto, has_vulnerable FROM sos_requests").fetchone()
        self.assertEqual(row["severity"], 5)
        self.assertEqual(row["severity_auto"], 5)

    def test_vulnerable_flag_is_denormalised(self):
        conn = db()
        post(conn, needs=["elderly"])
        self.assertEqual(conn.execute("SELECT has_vulnerable FROM sos_requests").fetchone()[0], 1)

    def test_retention_deadline_is_set_on_every_row(self):
        conn = db()
        post(conn)
        row = conn.execute("SELECT consent_at, purge_after FROM sos_requests").fetchone()
        self.assertGreater(row["purge_after"], row["consent_at"])
        days = (row["purge_after"] - row["consent_at"]) / 86400_000
        self.assertAlmostEqual(days, 90, delta=1)

    def test_oversized_note_is_truncated_not_rejected(self):
        conn = db()
        post(conn, note="x" * 5000)
        self.assertEqual(len(conn.execute("SELECT note FROM sos_requests").fetchone()[0]), 1000)


class TestOfflineIdempotency(unittest.TestCase):
    """An offline phone retries as the connection flickers. One family must not
    become twelve pins, and twelve boats."""

    def test_same_client_id_lands_once(self):
        conn = db()
        first = post(conn, client_id="abc")
        again = post(conn, client_id="abc")
        self.assertEqual(first[0], 201)
        self.assertEqual(again[0], 200)
        self.assertTrue(again[1]["duplicate"])
        self.assertEqual(again[1]["id"], first[1]["id"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sos_requests").fetchone()[0], 1)

    def test_distinct_client_ids_are_distinct_requests(self):
        conn = db()
        post(conn, client_id="a")
        post(conn, client_id="b")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sos_requests").fetchone()[0], 2)

    def test_requests_without_client_id_are_not_collapsed(self):
        conn = db()
        post(conn)
        post(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sos_requests").fetchone()[0], 2)


class TestAbuseResistance(unittest.TestCase):
    """A fake request diverts a boat from someone real."""

    def test_one_device_spamming_is_stopped(self):
        conn = db()
        codes = [ds.submit(conn, dict(BASE, client_id=f"c{i}", device_id="same-device"),
                           "ip-a")[0] for i in range(ds.RATE_MAX_DEVICE + 6)]
        self.assertEqual(codes.count(201), ds.RATE_MAX_DEVICE)
        self.assertEqual(codes.count(429), 6)

    def test_carrier_nat_does_not_block_a_neighbourhood(self):
        """Thai mobile networks put thousands of subscribers behind one address.
        A per-IP limit tight enough to stop an abuser would silently block the
        people least able to call 1784 instead."""
        conn = db()
        codes = [ds.submit(conn, dict(BASE, client_id=f"n{i}", device_id=f"dev-{i}"),
                           "one-shared-nat")[0] for i in range(250)]
        self.assertEqual(codes.count(201), 250, "distinct devices on one NAT must get through")

    def test_life_threatening_reports_get_extra_headroom(self):
        """A real mass-casualty event in one soi produces exactly the traffic shape
        a rate limiter mistakes for abuse. A false accept costs a triage review;
        a false reject can cost a life."""
        conn = db()
        codes = [ds.submit(conn, dict(BASE, needs=["medical"], client_id=f"m{i}",
                                      device_id=f"dev-{i}"), "one-shared-nat")[0]
                 for i in range(ds.RATE_MAX_IP + 100)]
        self.assertEqual(codes.count(429), 0)

    def test_non_critical_traffic_still_has_an_ip_ceiling(self):
        conn = db()
        codes = [ds.submit(conn, dict(BASE, client_id=f"f{i}", device_id=f"dev-{i}"),
                           "nat")[0] for i in range(ds.RATE_MAX_IP + 20)]
        self.assertEqual(codes.count(201), ds.RATE_MAX_IP)
        self.assertEqual(codes.count(429), 20)

    def test_rate_limit_is_per_source(self):
        conn = db()
        for i in range(ds.RATE_MAX_IP):
            ds.submit(conn, dict(BASE, client_id=f"x{i}", device_id=f"d{i}"), "ip-a")
        status, _ = ds.submit(conn, dict(BASE, client_id="other", device_id="dz"), "ip-b")
        self.assertEqual(status, 201, "one abuser must not block everyone else")


class TestPrivacy(unittest.TestCase):
    """PDPA. These are the tests that matter most if this is ever real."""

    def test_public_summary_contains_no_personal_data(self):
        conn = db()
        post(conn, contact_name="สมชาย", contact_phone="0812345678",
             note="secret", province="สมุทรปราการ", district="บางพลี")
        blob = json.dumps(ds.public_summary(conn), ensure_ascii=False)
        for leak in ("สมชาย", "0812345678", "secret"):
            self.assertNotIn(leak, blob)
        self.assertIn("บางพลี", blob, "district-level demand should still be visible")

    def test_public_summary_has_no_coordinates(self):
        conn = db()
        post(conn)
        row = ds.public_summary(conn)[0]
        self.assertNotIn("lat", row)
        self.assertNotIn("lon", row)

    def test_listing_by_a_responder_is_logged(self):
        conn = db()
        token = ds.create_actor(conn)
        post(conn)
        ds.listing(conn, ds.actor_for(conn, "Bearer " + token))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0], 1)

    def test_tokens_are_never_stored_in_the_clear(self):
        conn = db()
        token = ds.create_actor(conn)
        stored = conn.execute("SELECT token_hash FROM actors").fetchone()[0]
        self.assertNotEqual(stored, token)
        self.assertEqual(len(stored), 64)

    def test_expired_rows_purge_themselves(self):
        conn = db()
        post(conn)
        conn.execute("UPDATE sos_requests SET purge_after = 1")
        conn.commit()
        self.assertEqual(ds.purge_expired(conn), 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sos_requests").fetchone()[0], 0)

    def test_audit_log_outlives_the_data_it_describes(self):
        conn = db()
        token = ds.create_actor(conn)
        post(conn)
        ds.listing(conn, ds.actor_for(conn, "Bearer " + token))
        conn.execute("UPDATE sos_requests SET purge_after = 1")
        conn.commit()
        ds.purge_expired(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM access_log").fetchone()[0], 1,
                         "the evidence that access was controlled must survive the purge")


class TestAuthorisation(unittest.TestCase):
    def test_no_token_is_no_actor(self):
        conn = db()
        self.assertIsNone(ds.actor_for(conn, None))
        self.assertIsNone(ds.actor_for(conn, "Bearer wrong"))

    def test_deactivated_actor_is_rejected(self):
        conn = db()
        token = ds.create_actor(conn)
        conn.execute("UPDATE actors SET active = 0")
        conn.commit()
        self.assertIsNone(ds.actor_for(conn, "Bearer " + token))

    def test_expired_actor_is_rejected(self):
        conn = db()
        token = ds.create_actor(conn)
        conn.execute("UPDATE actors SET expires_at = 1")
        conn.commit()
        self.assertIsNone(ds.actor_for(conn, "Bearer " + token))

    def test_scoped_responder_sees_only_their_province(self):
        conn = db()
        post(conn, province="สมุทรปราการ", client_id="a")
        post(conn, province="เชียงใหม่", client_id="b")
        token = ds.create_actor(conn, scope="สมุทรปราการ")
        rows = ds.listing(conn, ds.actor_for(conn, "Bearer " + token))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["province"], "สมุทรปราการ")


class TestTriage(unittest.TestCase):
    def test_queue_is_ordered_worst_first(self):
        conn = db()
        post(conn, needs=["food"], client_id="a")
        post(conn, needs=["medical"], client_id="b")
        post(conn, needs=["rescue_boat"], client_id="c")
        token = ds.create_actor(conn)
        rows = ds.listing(conn, ds.actor_for(conn, "Bearer " + token))
        self.assertEqual([r["severity"] for r in rows], [5, 4, 2])

    def test_waiting_promotes_a_request(self):
        conn = db()
        post(conn, needs=["food"], client_id="old")
        conn.execute("UPDATE sos_requests SET created_at = created_at - ?",
                     (13 * 3600_000,))
        conn.commit()
        token = ds.create_actor(conn)
        row = ds.listing(conn, ds.actor_for(conn, "Bearer " + token))[0]
        self.assertEqual(row["severity"], 2)
        self.assertEqual(row["effective_severity"], 4, "nobody should be forgotten in the queue")

    def test_status_change_is_audited(self):
        conn = db()
        _, body = post(conn)
        token = ds.create_actor(conn)
        actor = ds.actor_for(conn, "Bearer " + token)
        ds.update_request(conn, actor, body["id"], {"status": "assigned"})
        hist = conn.execute(
            "SELECT action, from_status, to_status, actor_id FROM sos_updates"
            " WHERE request_id=? ORDER BY at", (body["id"],)).fetchall()
        self.assertEqual([h["action"] for h in hist], ["created", "update"])
        self.assertEqual(hist[1]["to_status"], "assigned")
        self.assertEqual(hist[1]["actor_id"], actor["id"])

    def test_invalid_status_is_rejected(self):
        conn = db()
        _, body = post(conn)
        token = ds.create_actor(conn)
        actor = ds.actor_for(conn, "Bearer " + token)
        status, out = ds.update_request(conn, actor, body["id"], {"status": "banana"})
        self.assertEqual(status, 422)

    def test_resolved_requests_leave_the_open_queue(self):
        conn = db()
        _, body = post(conn)
        token = ds.create_actor(conn)
        actor = ds.actor_for(conn, "Bearer " + token)
        ds.update_request(conn, actor, body["id"], {"status": "resolved"})
        self.assertEqual(len(ds.listing(conn, actor)), 0)


class TestClustering(unittest.TestCase):
    """Fourteen requests from one soi should send one boat, not fourteen."""

    def test_nearby_requests_cluster(self):
        reqs = [{"id": str(i), "lat": 13.6012 + i * 0.0001, "lon": 100.7015,
                 "geohash": geohash(13.6012 + i * 0.0001, 100.7015, 7),
                 "severity": 3, "people_count": 2, "needs": ["food"]} for i in range(4)]
        out = cluster(reqs)
        self.assertTrue(out)
        self.assertGreaterEqual(out[0]["count"], 2)
        self.assertEqual(out[0]["people"], out[0]["count"] * 2)

    def test_a_lone_request_is_not_a_cluster(self):
        one = [{"id": "1", "lat": 13.6, "lon": 100.7,
                "geohash": geohash(13.6, 100.7, 7), "severity": 3, "needs": []}]
        self.assertEqual(cluster(one), [])

    def test_distant_requests_do_not_cluster(self):
        far = [{"id": "1", "lat": 13.6, "lon": 100.7, "geohash": geohash(13.6, 100.7, 7),
                "severity": 3, "needs": []},
               {"id": "2", "lat": 18.8, "lon": 99.0, "geohash": geohash(18.8, 99.0, 7),
                "severity": 3, "needs": []}]
        self.assertEqual(cluster(far), [])

    def test_clusters_are_ordered_by_urgency(self):
        reqs = []
        for i in range(2):
            reqs += [{"id": f"a{i}", "lat": 13.60, "lon": 100.70,
                      "geohash": geohash(13.60, 100.70, 7), "severity": 2, "needs": []}]
        for i in range(2):
            reqs += [{"id": f"b{i}", "lat": 13.70, "lon": 100.80,
                      "geohash": geohash(13.70, 100.80, 7), "severity": 5, "needs": []}]
        self.assertEqual(cluster(reqs)[0]["severity"], 5)


class TestNeedHelpers(unittest.TestCase):
    def test_validation(self):
        self.assertTrue(needs_are_valid(["food"]))
        self.assertFalse(needs_are_valid([]))
        self.assertFalse(needs_are_valid(["food", "nope"]))

    def test_vulnerability_detection(self):
        self.assertTrue(has_vulnerable(["elderly"]))
        self.assertFalse(has_vulnerable(["food"]))
        self.assertFalse(has_vulnerable(["unknown"]))


if __name__ == "__main__":
    unittest.main()


class TestSecurityFixes(unittest.TestCase):
    """Regression tests for docs/SECURITY.md. Each one fails if a fix is reverted."""

    def test_f2_scoped_responder_cannot_read_outside_its_province(self):
        conn = db()
        _, mine = post(conn, province="สมุทรปราการ", client_id="a")
        _, theirs = post(conn, province="เชียงใหม่", client_id="b")
        actor = ds.actor_for(conn, "Bearer " + ds.create_actor(conn, scope="สมุทรปราการ"))
        inside = conn.execute("SELECT * FROM sos_requests WHERE id=?", (mine["id"],)).fetchone()
        outside = conn.execute("SELECT * FROM sos_requests WHERE id=?", (theirs["id"],)).fetchone()
        self.assertFalse(ds.out_of_scope(actor, inside))
        self.assertTrue(ds.out_of_scope(actor, outside))

    def test_f2_scoped_responder_cannot_modify_outside_its_province(self):
        conn = db()
        _, theirs = post(conn, province="เชียงใหม่", client_id="b")
        actor = ds.actor_for(conn, "Bearer " + ds.create_actor(conn, scope="สมุทรปราการ"))
        status, body = ds.update_request(conn, actor, theirs["id"], {"status": "resolved"})
        self.assertEqual(status, 404, "must not leak existence via 403")
        self.assertEqual(
            conn.execute("SELECT status FROM sos_requests WHERE id=?",
                         (theirs["id"],)).fetchone()[0], "new")

    def test_f2_unscoped_responder_still_reaches_everything(self):
        conn = db()
        _, r = post(conn, province="เชียงใหม่")
        actor = ds.actor_for(conn, "Bearer " + ds.create_actor(conn))
        row = conn.execute("SELECT * FROM sos_requests WHERE id=?", (r["id"],)).fetchone()
        self.assertFalse(ds.out_of_scope(actor, row))

    def test_f3_worker_refuses_the_placeholder_salt(self):
        js = WORKER_JS.read_text(encoding="utf-8")
        self.assertIn("PLACEHOLDER_SALT", js)
        self.assertIn('fail(503, "misconfigured"', js)

    def test_f1_every_page_escapes_before_innerhtml(self):
        for name in ("ops.html", "sos.html", "index.html"):
            html = (ROOT.parent / "web" / name).read_text(encoding="utf-8")
            self.assertIn("const esc =", html, f"{name} must define an escaper")

    def test_f1_sensitive_fields_are_escaped_in_the_ops_console(self):
        import re
        html = (ROOT.parent / "web" / "ops.html").read_text(encoding="utf-8")
        risky = re.compile(r"\b(r|c|h)\.(note|contact_name|district|province|status|action|to_status)\b")
        unescaped = [m for m in re.findall(r"\$\{([^}]*)\}", html)
                     if risky.search(m) and "esc(" not in m]
        self.assertEqual(unescaped, [], f"unescaped user data reaches innerHTML: {unescaped}")

    def test_f4_untrusted_api_override_is_ignored(self):
        html = (ROOT.parent / "web" / "sos.html").read_text(encoding="utf-8")
        self.assertIn("resolveApi", html)
        self.assertIn("Ignoring untrusted", html)

    def test_f5_csp_is_configured(self):
        headers = (ROOT.parent / "web" / "_headers").read_text(encoding="utf-8")
        for token in ("Content-Security-Policy", "frame-ancestors 'none'",
                      "X-Content-Type-Options", "form-action 'self'"):
            self.assertIn(token, headers)
