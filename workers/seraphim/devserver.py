"""Local development server for the SOS API.

Production runs on a Cloudflare Worker (`api/src/index.js`). That needs node and
wrangler; this needs neither, so the front end can be built and tested offline — and
in a flood project, "works without the network" is a habit worth keeping everywhere.

It executes the **real** `api/schema.sql` against SQLite, which is what D1 is, so the
schema, constraints, indexes and queries exercised here are the production ones.
Domain rules come from `seraphim.sos`, and both implementations are held to
`api/conformance/severity.json`.

Dev only: no TLS, permissive CORS, tokens printed to the console.
    python3 -m seraphim.devserver --port 8788 --seed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from seraphim.sos import NEED_BY_CODE, cluster, derive_severity, escalate_for_age, geohash

DISCLAIMER = {
    "th": "ไม่ใช่ช่องทางแจ้งเหตุฉุกเฉินทางราชการ เหตุด่วนโทร 1784 (ปภ.) หรือ 191",
    "en": "Not an official emergency channel. In an emergency call 1784 (DDPM) or 191.",
}
RETENTION_DAYS = 90
RATE_WINDOW_MS = 10 * 60_000
#: A device is the real actor. An IP is a shared resource: Thai mobile networks use
#: carrier-grade NAT, so a limit tight enough to stop an abuser would block a whole
#: neighbourhood of mobile-only users — the people least able to call 1784 instead.
RATE_MAX_DEVICE = 8
RATE_MAX_IP = 300
#: Life-threatening reports get more headroom: a real mass-casualty event in one soi
#: produces exactly the traffic shape a rate limiter mistakes for abuse.
RATE_MAX_IP_CRITICAL = 900
CRITICAL_NEEDS = frozenset({"medical", "trapped", "missing", "oxygen", "dialysis"})
OPEN = ("new", "triaged", "assigned", "in_progress")
ALLOWED_STATUS = {*OPEN, "resolved", "cancelled", "duplicate"}
RANK = {"volunteer": 1, "official": 2, "admin": 3}

SCHEMA = Path(__file__).resolve().parents[2] / "api" / "schema.sql"


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return f"{now_ms():x}{secrets.token_hex(6)}"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def connect(path: str = ":memory:") -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA.read_text(encoding="utf-8"))
    return db


def create_actor(db, role="official", name="Dev Responder", org="dev", scope=None) -> str:
    token = secrets.token_urlsafe(24)
    db.execute(
        "INSERT INTO actors (id, token_hash, role, name, org, scope_province, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (new_id(), sha256(token), role, name, org, scope, now_ms()),
    )
    db.commit()
    return token


# --- shared request handling ------------------------------------------------

def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int_or_none(v, lo, hi):
    n = _num(v if not isinstance(v, str) else (float(v) if v.strip() else None))
    if n is None:
        return None
    i = round(n)
    return i if lo <= i <= hi else None


def _clean(v, n):
    return v.strip()[:n] if isinstance(v, str) and v.strip() else None


def bump_bucket(db, bucket: str, max_count: int) -> bool:
    now = now_ms()
    row = db.execute(
        "SELECT window_start, count FROM rate_limits WHERE bucket=?", (bucket,)
    ).fetchone()
    if row is None or now - row["window_start"] > RATE_WINDOW_MS:
        db.execute(
            "INSERT INTO rate_limits (bucket, window_start, count) VALUES (?,?,1)"
            " ON CONFLICT(bucket) DO UPDATE SET window_start=?, count=1",
            (bucket, now, now))
        db.commit()
        return False
    if row["count"] >= max_count:
        return True
    db.execute("UPDATE rate_limits SET count=count+1 WHERE bucket=?", (bucket,))
    db.commit()
    return False


def rate_limited(db, ip_hash: str, device_hash: str | None = None,
                 needs: list[str] | None = None) -> bool:
    ip_max = RATE_MAX_IP_CRITICAL if set(needs or []) & CRITICAL_NEEDS else RATE_MAX_IP
    if device_hash and bump_bucket(db, f"dev:{device_hash}", RATE_MAX_DEVICE):
        return True
    return bump_bucket(db, f"ip:{ip_hash}", ip_max)


def submit(db, body: dict, ip_hash: str) -> tuple[int, dict]:
    lat, lon = _num(body.get("lat")), _num(body.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return 422, {"ok": False, "error": "bad_location"}

    needs = body.get("needs")
    needs = (
        list(dict.fromkeys(n for n in needs if isinstance(n, str) and n in NEED_BY_CODE))[:12]
        if isinstance(needs, list) else []
    )
    if not needs:
        return 422, {"ok": False, "error": "no_needs"}
    if body.get("consent") is not True:
        return 422, {"ok": False, "error": "consent_required"}
    device_hash = (sha256(str(body["device_id"])[:80] + "dev-salt")
                   if body.get("device_id") else None)
    if rate_limited(db, ip_hash, device_hash, needs):
        return 429, {"ok": False, "error": "rate_limited"}

    client_id = _clean(body.get("client_id"), 64)
    if client_id:
        existing = db.execute(
            "SELECT id, status, severity FROM sos_requests WHERE client_id=?", (client_id,)
        ).fetchone()
        if existing:
            return 200, {"ok": True, "id": existing["id"], "status": existing["status"],
                         "severity": existing["severity"], "duplicate": True}

    people = _int_or_none(body.get("people_count"), 0, 10000)
    depth = _int_or_none(body.get("water_depth_cm"), 0, 2000)
    severity = derive_severity(needs, people, depth)
    now = now_ms()
    rid = new_id()

    db.execute(
        """INSERT INTO sos_requests
           (id, created_at, updated_at, status, severity, severity_auto, lat, lon, geohash,
            province, district, people_count, needs, water_depth_cm, note, contact_name,
            contact_phone, safe_to_call, has_vulnerable, source, client_id, device_hash,
            consent_at, purge_after)
           VALUES (?,?,?,'new',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'web',?,?,?,?)""",
        (rid, now, now, severity, severity, lat, lon, geohash(lat, lon, 7),
         _clean(body.get("province"), 100), _clean(body.get("district"), 100),
         people, json.dumps(needs), depth, _clean(body.get("note"), 1000),
         _clean(body.get("contact_name"), 120), _clean(body.get("contact_phone"), 32),
         0 if body.get("safe_to_call") is False else 1,
         1 if any(NEED_BY_CODE[n].vulnerable for n in needs) else 0,
         client_id, device_hash, now, now + RETENTION_DAYS * 86400_000),
    )
    db.execute(
        "INSERT INTO sos_updates (id, request_id, at, actor_id, action, to_status)"
        " VALUES (?,?,?,NULL,'created','new')", (new_id(), rid, now))
    db.commit()
    return 201, {"ok": True, "id": rid, "severity": severity, "status": "new",
                 "retention_days": RETENTION_DAYS}


def actor_for(db, authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    row = db.execute(
        "SELECT * FROM actors WHERE token_hash=? AND active=1",
        (sha256(authorization[7:].strip()),),
    ).fetchone()
    if row and row["expires_at"] and row["expires_at"] < now_ms():
        return None
    return row


def out_of_scope(actor, row) -> bool:
    """F2: a province-scoped responder must not reach outside it.

    Callers return 404 rather than 403, so the response cannot be used to confirm that
    a request exists somewhere the caller is not allowed to look.
    """
    return bool(actor["scope_province"]) and row["province"] != actor["scope_province"]


def responder_view(row, now) -> dict:
    d = dict(row)
    d["needs"] = json.loads(d.get("needs") or "[]")
    waiting = (now - d["created_at"]) / 60000
    d["waiting_minutes"] = round(waiting)
    d["effective_severity"] = escalate_for_age(d["severity"], waiting)
    return d


def listing(db, actor, status=None, limit=200) -> list[dict]:
    sql = "SELECT * FROM sos_requests WHERE "
    binds: list = []
    if status:
        sql += "status = ?"
        binds.append(status)
    else:
        sql += f"status IN ({','.join('?' * len(OPEN))})"
        binds.extend(OPEN)
    if actor["scope_province"]:
        sql += " AND province = ?"
        binds.append(actor["scope_province"])
    sql += " ORDER BY severity DESC, created_at ASC LIMIT ?"
    binds.append(limit)
    now = now_ms()
    rows = [responder_view(r, now) for r in db.execute(sql, binds).fetchall()]
    rows.sort(key=lambda r: (-r["effective_severity"], r["created_at"]))
    db.execute(
        "INSERT INTO access_log (id, at, actor_id, request_id, fields, ip_hash)"
        " VALUES (?,?,?,NULL,'list:contact_name,contact_phone','dev')",
        (new_id(), now, actor["id"]))
    db.commit()
    return rows


def update_request(db, actor, rid, body) -> tuple[int, dict]:
    row = db.execute(
        "SELECT status, province FROM sos_requests WHERE id=?", (rid,)).fetchone()
    if row is None or out_of_scope(actor, row):
        return 404, {"ok": False, "error": "not_found"}
    status = body.get("status") if body.get("status") in ALLOWED_STATUS else None
    severity = _int_or_none(body.get("severity"), 1, 5)
    assigned = _clean(body.get("assigned_to"), 120)
    if status is None and severity is None and assigned is None:
        return 422, {"ok": False, "error": "nothing_to_update"}
    now = now_ms()
    db.execute(
        """UPDATE sos_requests SET
             status = COALESCE(?, status),
             severity = COALESCE(?, severity),
             assigned_to = COALESCE(?, assigned_to),
             resolved_at = CASE WHEN ?='resolved' THEN ? ELSE resolved_at END,
             updated_at = ?
           WHERE id = ?""",
        (status, severity, assigned, status, now, now, rid))
    db.execute(
        "INSERT INTO sos_updates (id, request_id, at, actor_id, action, from_status, to_status, note)"
        " VALUES (?,?,?,?,'update',?,?,?)",
        (new_id(), rid, now, actor["id"], row["status"], status or row["status"],
         _clean(body.get("note"), 1000)))
    db.commit()
    return 200, {"ok": True, "id": rid, "status": status or row["status"]}


def public_summary(db) -> list[dict]:
    rows = db.execute(
        f"""SELECT province, district, COUNT(*) AS open_requests, MAX(severity) AS max_severity
            FROM sos_requests WHERE status IN ({','.join('?' * len(OPEN))})
            GROUP BY province, district
            ORDER BY max_severity DESC, open_requests DESC LIMIT 300""", OPEN).fetchall()
    return [dict(r) for r in rows]


def purge_expired(db) -> int:
    """PDPA storage limitation. access_log deliberately outlives the data it describes:
    it is the evidence that access was controlled."""
    cur = db.execute("DELETE FROM sos_requests WHERE purge_after < ?", (now_ms(),))
    db.commit()
    return cur.rowcount


# --- HTTP -------------------------------------------------------------------

def make_handler(db):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

        def _send(self, status, payload):
            body = json.dumps({**payload, "disclaimer": DISCLAIMER},
                              ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return None

        def _actor(self):
            return actor_for(db, self.headers.get("Authorization"))

        def do_OPTIONS(self):
            self._send(204, {})

        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/")
            query = parse_qs(urlparse(self.path).query)
            if path == "/api/health":
                return self._send(200, {"ok": True, "service": "seraphim-sos-dev"})
            if path == "/api/needs":
                return self._send(200, {"ok": True, "needs": [
                    {"code": n.code, "th": n.th, "en": n.en, "base": n.base,
                     "vulnerable": n.vulnerable} for n in NEED_BY_CODE.values()]})
            if path == "/api/public/summary":
                return self._send(200, {"ok": True, "areas": public_summary(db)})

            actor = self._actor()
            if actor is None or RANK.get(actor["role"], 0) < 1:
                return self._send(401, {"ok": False, "error": "unauthorised"})
            if path == "/api/sos":
                rows = listing(db, actor, (query.get("status") or [None])[0])
                return self._send(200, {"ok": True, "count": len(rows), "requests": rows})
            if path == "/api/clusters":
                rows = listing(db, actor)
                return self._send(200, {"ok": True, "clusters": cluster(rows)})
            if path.startswith("/api/sos/"):
                rid = path.rsplit("/", 1)[-1]
                row = db.execute("SELECT * FROM sos_requests WHERE id=?", (rid,)).fetchone()
                if row is None or out_of_scope(actor, row):
                    return self._send(404, {"ok": False, "error": "not_found"})
                db.execute(
                    "INSERT INTO access_log (id, at, actor_id, request_id, fields, ip_hash)"
                    " VALUES (?,?,?,?,'contact_name,contact_phone,note,lat,lon','dev')",
                    (new_id(), now_ms(), actor["id"], rid))
                db.commit()
                hist = [dict(h) for h in db.execute(
                    "SELECT * FROM sos_updates WHERE request_id=? ORDER BY at", (rid,))]
                return self._send(200, {"ok": True,
                                        "request": responder_view(row, now_ms()),
                                        "history": hist})
            self._send(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            body = self._body()
            if body is None:
                return self._send(400, {"ok": False, "error": "bad_json"})
            if path == "/api/sos":
                ip = self.client_address[0] if self.client_address else "0.0.0.0"
                status, payload = submit(db, body, sha256(ip + "dev-salt"))
                return self._send(status, payload)
            self._send(404, {"ok": False, "error": "not_found"})

        def do_PATCH(self):
            path = urlparse(self.path).path.rstrip("/")
            actor = self._actor()
            if actor is None:
                return self._send(401, {"ok": False, "error": "unauthorised"})
            body = self._body()
            if body is None:
                return self._send(400, {"ok": False, "error": "bad_json"})
            if path.startswith("/api/sos/"):
                status, payload = update_request(db, actor, path.rsplit("/", 1)[-1], body)
                return self._send(status, payload)
            self._send(404, {"ok": False, "error": "not_found"})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(prog="seraphim.devserver")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--db", default=":memory:", help="SQLite path (default: in-memory)")
    ap.add_argument("--seed", action="store_true", help="insert demo requests")
    ap.add_argument("--token-file", type=Path,
                    help="also write the responder token here (handy for scripts)")
    args = ap.parse_args()

    db = connect(args.db)
    token = create_actor(db, role="official", name="Dev Responder")
    if args.seed:
        from seraphim.sos_seed import seed
        seed(db, submit)

    if args.token_file:
        args.token_file.write_text(token, encoding="utf-8")

    # flush=True: stdout is block-buffered when this is piped or backgrounded, which
    # would swallow the token and leave no way to sign in to the ops console.
    print("SeRAPHIM SOS dev server", flush=True)
    print(f"  http://127.0.0.1:{args.port}/api/health", flush=True)
    print(f"  responder token: {token}", flush=True)
    if args.token_file:
        print(f"  token written to: {args.token_file}", flush=True)
    print("  DEV ONLY — no TLS, permissive CORS, not an official emergency channel.",
          flush=True)
    HTTPServer(("127.0.0.1", args.port), make_handler(db)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
