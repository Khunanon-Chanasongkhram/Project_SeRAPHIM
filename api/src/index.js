/**
 * SeRAPHIM SOS API — Cloudflare Worker + D1.
 *
 * This is the only always-on server in the system. Everything else is static files on
 * a CDN, so this handles exactly one job: accepting and triaging requests for help.
 *
 * It holds SENSITIVE PERSONAL DATA under Thailand's PDPA — precise location, health
 * needs, and phone numbers of people in danger. Consequences, enforced below:
 *   - personal fields are never returned to unauthenticated callers
 *   - every read of personal data is written to access_log
 *   - every row carries its own retention deadline and is purged on a schedule
 *   - raw IPs are hashed, never stored
 *
 * Severity and geohash rules mirror workers/seraphim/sos.py. Both implementations are
 * held to api/conformance/severity.json — if they ever disagree, that file is right.
 *
 * NOT AN OFFICIAL EMERGENCY CHANNEL. Every response carries the 1784 / 191 notice.
 */

const DISCLAIMER = {
  th: "ไม่ใช่ช่องทางแจ้งเหตุฉุกเฉินทางราชการ เหตุด่วนโทร 1784 (ปภ.) หรือ 191",
  en: "Not an official emergency channel. In an emergency call 1784 (DDPM) or 191.",
};

// --- domain rules (mirror of seraphim/sos.py) -------------------------------
const NEEDS = {
  medical:    { base: 5, vulnerable: false },
  missing:    { base: 5, vulnerable: false },
  trapped:    { base: 5, vulnerable: false },
  oxygen:     { base: 5, vulnerable: true  },
  dialysis:   { base: 5, vulnerable: true  },
  rescue_boat:{ base: 4, vulnerable: false },
  evacuation: { base: 4, vulnerable: false },
  insulin:    { base: 4, vulnerable: true  },
  medicine:   { base: 4, vulnerable: true  },
  infant:     { base: 3, vulnerable: true  },
  elderly:    { base: 3, vulnerable: true  },
  disabled:   { base: 3, vulnerable: true  },
  pregnant:   { base: 3, vulnerable: true  },
  water:      { base: 3, vulnerable: false },
  food:       { base: 2, vulnerable: false },
  shelter:    { base: 2, vulnerable: false },
  power:      { base: 2, vulnerable: false },
  toilet:     { base: 2, vulnerable: false },
  pets:       { base: 1, vulnerable: false },
  livestock:  { base: 1, vulnerable: false },
};

const DEPTH_LIFE_THREATENING_CM = 150;
const DEPTH_DANGEROUS_CM = 100;
const LARGE_GROUP = 10;
const AGE_ESCALATE_MINUTES = 360;

const MAX_NOTE = 1000;
const MAX_NEEDS = 12;
const RETENTION_DAYS = 90;          // PDPA storage limitation
const RATE_WINDOW_MS = 10 * 60_000; // 10 minutes
const RATE_MAX = 12;                // submissions per IP per window

function deriveSeverity(needs, peopleCount, waterDepthCm) {
  const known = needs.map((n) => NEEDS[n]).filter(Boolean);
  if (!known.length) return 1;
  let severity = Math.max(...known.map((n) => n.base));
  const vulnerable = known.some((n) => n.vulnerable);
  if (waterDepthCm != null) {
    if (waterDepthCm >= DEPTH_LIFE_THREATENING_CM) severity += 1;
    else if (waterDepthCm >= DEPTH_DANGEROUS_CM && vulnerable) severity += 1;
  }
  if (peopleCount != null && peopleCount >= LARGE_GROUP) severity += 1;
  return Math.max(1, Math.min(5, severity));
}

function escalateForAge(severity, waitingMinutes) {
  if (waitingMinutes >= AGE_ESCALATE_MINUTES * 2) return Math.min(5, severity + 2);
  if (waitingMinutes >= AGE_ESCALATE_MINUTES) return Math.min(5, severity + 1);
  return severity;
}

const B32 = "0123456789bcdefghjkmnpqrstuvwxyz";
function geohash(lat, lon, precision = 7) {
  let latLo = -90, latHi = 90, lonLo = -180, lonHi = 180;
  let out = "", bit = 0, ch = 0, even = true;
  while (out.length < precision) {
    if (even) {
      const mid = (lonLo + lonHi) / 2;
      // Boundary goes HIGH, matching Redis/Elasticsearch: (0,0) -> "s0000".
      if (lon >= mid) { ch = (ch << 1) | 1; lonLo = mid; } else { ch <<= 1; lonHi = mid; }
    } else {
      const mid = (latLo + latHi) / 2;
      if (lat >= mid) { ch = (ch << 1) | 1; latLo = mid; } else { ch <<= 1; latHi = mid; }
    }
    even = !even;
    if (++bit === 5) { out += B32[ch]; bit = 0; ch = 0; }
  }
  return out;
}

// --- helpers ----------------------------------------------------------------
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,PATCH,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type,Authorization",
  "Access-Control-Max-Age": "86400",
};

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify({ ...body, disclaimer: DISCLAIMER }), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS, ...extra },
  });
}
const fail = (status, error, detail) => json({ ok: false, error, detail }, status);

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Time-ordered id: sorts by creation, no collisions, no external dependency. */
function newId() {
  const t = Date.now().toString(36).padStart(9, "0");
  const r = [...crypto.getRandomValues(new Uint8Array(8))]
    .map((b) => b.toString(36).padStart(2, "0")).join("");
  return `${t}${r}`.slice(0, 26);
}

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
function intOrNull(v, lo, hi) {
  const n = num(typeof v === "string" ? Number(v) : v);
  if (n === null) return null;
  const i = Math.round(n);
  return i >= lo && i <= hi ? i : null;
}
const clean = (v, max) =>
  typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null;

async function actorFor(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) return null;
  const hash = await sha256(token);
  const row = await env.DB.prepare(
    `SELECT id, role, name, org, scope_province, expires_at FROM actors
     WHERE token_hash = ?1 AND active = 1`
  ).bind(hash).first();
  if (!row) return null;
  if (row.expires_at && row.expires_at < Date.now()) return null;
  return row;
}

const RANK = { volunteer: 1, official: 2, admin: 3 };
const atLeast = (actor, role) => !!actor && (RANK[actor.role] || 0) >= RANK[role];

async function rateLimited(env, ipHash) {
  const now = Date.now();
  const row = await env.DB.prepare(
    `SELECT window_start, count FROM rate_limits WHERE ip_hash = ?1`
  ).bind(ipHash).first();
  if (!row || now - row.window_start > RATE_WINDOW_MS) {
    await env.DB.prepare(
      `INSERT INTO rate_limits (ip_hash, window_start, count) VALUES (?1, ?2, 1)
       ON CONFLICT(ip_hash) DO UPDATE SET window_start = ?2, count = 1`
    ).bind(ipHash, now).run();
    return false;
  }
  if (row.count >= RATE_MAX) return true;
  await env.DB.prepare(
    `UPDATE rate_limits SET count = count + 1 WHERE ip_hash = ?1`
  ).bind(ipHash).run();
  return false;
}

/** Strip every personal field. Used for anything a non-responder can see. */
function publicView(r) {
  return {
    id: r.id,
    created_at: r.created_at,
    status: r.status,
    severity: r.severity,
    lat: Math.round(r.lat * 100) / 100,   // ~1 km — enough to show demand, not a doorstep
    lon: Math.round(r.lon * 100) / 100,
    province: r.province,
    district: r.district,
    needs: safeParse(r.needs),
    people_count: r.people_count,
  };
}

function responderView(r, now) {
  const waiting = (now - r.created_at) / 60000;
  return {
    ...r,
    needs: safeParse(r.needs),
    waiting_minutes: Math.round(waiting),
    effective_severity: escalateForAge(r.severity, waiting),
  };
}

function safeParse(s) { try { return JSON.parse(s) || []; } catch { return []; } }

// --- routes -----------------------------------------------------------------

async function submit(request, env) {
  let body;
  try { body = await request.json(); } catch { return fail(400, "bad_json"); }

  const lat = num(body.lat), lon = num(body.lon);
  if (lat === null || lon === null || lat < -90 || lat > 90 || lon < -180 || lon > 180)
    return fail(422, "bad_location", "A valid lat/lon is required so help can find you.");

  const needs = Array.isArray(body.needs)
    ? [...new Set(body.needs.filter((n) => typeof n === "string" && NEEDS[n]))].slice(0, MAX_NEEDS)
    : [];
  if (!needs.length)
    return fail(422, "no_needs", "Select at least one thing you need.");

  if (body.consent !== true)
    return fail(422, "consent_required",
      "Consent to share this information with responders is required (PDPA).");

  const ipHash = await sha256(
    (request.headers.get("CF-Connecting-IP") || "0.0.0.0") + (env.IP_SALT || "seraphim")
  );
  if (await rateLimited(env, ipHash))
    return fail(429, "rate_limited",
      "Too many submissions from this connection. If this is an emergency call 1784 or 191.");

  const peopleCount = intOrNull(body.people_count, 0, 10000);
  const depth = intOrNull(body.water_depth_cm, 0, 2000);
  const severity = deriveSeverity(needs, peopleCount, depth);
  const now = Date.now();
  const clientId = clean(body.client_id, 64);

  // An offline phone retries as connectivity flickers. Without this, one family
  // becomes twelve pins and twelve boats.
  if (clientId) {
    const existing = await env.DB.prepare(
      `SELECT id, status, severity FROM sos_requests WHERE client_id = ?1`
    ).bind(clientId).first();
    if (existing)
      return json({ ok: true, id: existing.id, status: existing.status,
                    severity: existing.severity, duplicate: true }, 200);
  }

  const id = newId();
  await env.DB.prepare(
    `INSERT INTO sos_requests
      (id, created_at, updated_at, status, severity, severity_auto, lat, lon, geohash,
       province, district, people_count, needs, water_depth_cm, note, contact_name,
       contact_phone, safe_to_call, has_vulnerable, source, client_id, consent_at, purge_after)
     VALUES (?1,?2,?2,'new',?3,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,'web',?17,?2,?18)`
  ).bind(
    id, now, severity, lat, lon, geohash(lat, lon, 7),
    clean(body.province, 100), clean(body.district, 100),
    peopleCount, JSON.stringify(needs), depth, clean(body.note, MAX_NOTE),
    clean(body.contact_name, 120), clean(body.contact_phone, 32),
    body.safe_to_call === false ? 0 : 1,
    needs.some((n) => NEEDS[n].vulnerable) ? 1 : 0,
    clientId, now + RETENTION_DAYS * 86400_000
  ).run();

  await env.DB.prepare(
    `INSERT INTO sos_updates (id, request_id, at, actor_id, action, to_status)
     VALUES (?1,?2,?3,NULL,'created','new')`
  ).bind(newId(), id, now).run();

  return json({ ok: true, id, severity, status: "new",
                retention_days: RETENTION_DAYS }, 201);
}

async function list(request, env, actor) {
  const url = new URL(request.url);
  const status = url.searchParams.get("status");
  const limit = Math.min(Number(url.searchParams.get("limit")) || 200, 500);
  const openOnly = "('new','triaged','assigned','in_progress')";

  let sql = `SELECT * FROM sos_requests WHERE ${status ? "status = ?1" : `status IN ${openOnly}`}`;
  const binds = status ? [status] : [];
  // A district responder has no business reading the whole country.
  if (actor.scope_province) { sql += ` AND province = ?${binds.length + 1}`; binds.push(actor.scope_province); }
  sql += ` ORDER BY severity DESC, created_at ASC LIMIT ?${binds.length + 1}`;
  binds.push(limit);

  const { results } = await env.DB.prepare(sql).bind(...binds).all();
  const now = Date.now();
  const rows = (results || []).map((r) => responderView(r, now));
  // Re-sort by the age-escalated severity, so people who have waited move up.
  rows.sort((a, b) => b.effective_severity - a.effective_severity || a.created_at - b.created_at);

  await env.DB.prepare(
    `INSERT INTO access_log (id, at, actor_id, request_id, fields, ip_hash)
     VALUES (?1,?2,?3,NULL,'list:contact_name,contact_phone',?4)`
  ).bind(newId(), now, actor.id,
         await sha256(request.headers.get("CF-Connecting-IP") || "")).run();

  return json({ ok: true, count: rows.length, requests: rows });
}

async function detail(env, actor, id, request) {
  const row = await env.DB.prepare(`SELECT * FROM sos_requests WHERE id = ?1`).bind(id).first();
  if (!row) return fail(404, "not_found");
  const { results } = await env.DB.prepare(
    `SELECT * FROM sos_updates WHERE request_id = ?1 ORDER BY at`
  ).bind(id).all();
  await env.DB.prepare(
    `INSERT INTO access_log (id, at, actor_id, request_id, fields, ip_hash)
     VALUES (?1,?2,?3,?4,'contact_name,contact_phone,note,lat,lon',?5)`
  ).bind(newId(), Date.now(), actor.id, id,
         await sha256(request.headers.get("CF-Connecting-IP") || "")).run();
  return json({ ok: true, request: responderView(row, Date.now()), history: results || [] });
}

const ALLOWED_STATUS = new Set(
  ["new", "triaged", "assigned", "in_progress", "resolved", "cancelled", "duplicate"]);

async function update(request, env, actor, id) {
  let body;
  try { body = await request.json(); } catch { return fail(400, "bad_json"); }

  const row = await env.DB.prepare(
    `SELECT status, severity FROM sos_requests WHERE id = ?1`).bind(id).first();
  if (!row) return fail(404, "not_found");

  const status = typeof body.status === "string" && ALLOWED_STATUS.has(body.status)
    ? body.status : null;
  const severity = intOrNull(body.severity, 1, 5);
  const assigned = clean(body.assigned_to, 120);
  if (!status && severity === null && assigned === null) return fail(422, "nothing_to_update");

  const now = Date.now();
  await env.DB.prepare(
    `UPDATE sos_requests SET
       status = COALESCE(?2, status),
       severity = COALESCE(?3, severity),
       assigned_to = COALESCE(?4, assigned_to),
       resolved_at = CASE WHEN ?2 = 'resolved' THEN ?5 ELSE resolved_at END,
       updated_at = ?5
     WHERE id = ?1`
  ).bind(id, status, severity, assigned, now).run();

  // Audit is append-only and permanent: who changed what, when.
  await env.DB.prepare(
    `INSERT INTO sos_updates (id, request_id, at, actor_id, action, from_status, to_status, note)
     VALUES (?1,?2,?3,?4,'update',?5,?6,?7)`
  ).bind(newId(), id, now, actor.id, row.status, status || row.status,
         clean(body.note, MAX_NOTE)).run();

  return json({ ok: true, id, status: status || row.status });
}

async function clusters(env, actor) {
  const { results } = await env.DB.prepare(
    `SELECT id, lat, lon, geohash, severity, people_count, needs, province, district
     FROM sos_requests WHERE status IN ('new','triaged') ORDER BY severity DESC LIMIT 500`
  ).all();
  const buckets = new Map();
  for (const r of results || []) {
    const key = (r.geohash || "").slice(0, 7);
    if (!key) continue;
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(r);
  }
  const out = [];
  for (const [gh, group] of buckets) {
    if (group.length < 2) continue;   // one request is not a cluster
    out.push({
      geohash: gh,
      count: group.length,
      severity: Math.max(...group.map((g) => g.severity)),
      people: group.reduce((a, g) => a + (g.people_count || 0), 0),
      lat: group.reduce((a, g) => a + g.lat, 0) / group.length,
      lon: group.reduce((a, g) => a + g.lon, 0) / group.length,
      needs: [...new Set(group.flatMap((g) => safeParse(g.needs)))].sort(),
      ids: group.map((g) => g.id),
      province: group[0].province,
      district: group[0].district,
    });
  }
  out.sort((a, b) => b.severity - a.severity || b.count - a.count);
  return json({ ok: true, clusters: out, note:
    "A cluster is a dispatch hint, never a merge: every underlying request stays open." });
}

/** Anonymised. Lets residents see demand in their area without exposing anyone. */
async function publicSummary(env) {
  const { results } = await env.DB.prepare(
    `SELECT province, district, COUNT(*) AS open_requests, MAX(severity) AS max_severity
     FROM sos_requests WHERE status IN ('new','triaged','assigned','in_progress')
     GROUP BY province, district ORDER BY max_severity DESC, open_requests DESC LIMIT 300`
  ).all();
  return json({ ok: true, areas: results || [], note:
    "Aggregated by district. No personal data, no precise locations." });
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "");

    if (path === "/api/health") return json({ ok: true, service: "seraphim-sos" });
    if (path === "/api/public/summary" && request.method === "GET") return publicSummary(env);
    if (path === "/api/needs") return json({ ok: true, needs: Object.keys(NEEDS) });
    if (path === "/api/sos" && request.method === "POST") return submit(request, env);

    // Everything below requires a verified responder.
    const actor = await actorFor(request, env);
    if (!atLeast(actor, "volunteer")) return fail(401, "unauthorised");

    if (path === "/api/sos" && request.method === "GET") return list(request, env, actor);
    if (path === "/api/clusters" && request.method === "GET") return clusters(env, actor);

    const m = path.match(/^\/api\/sos\/([A-Za-z0-9_-]{1,40})$/);
    if (m) {
      if (request.method === "GET") return detail(env, actor, m[1], request);
      if (request.method === "PATCH") return update(request, env, actor, m[1]);
    }
    return fail(404, "not_found");
  },

  /** PDPA storage limitation: rows delete themselves when their time is up. */
  async scheduled(event, env, ctx) {
    const now = Date.now();
    ctx.waitUntil((async () => {
      await env.DB.prepare(`DELETE FROM sos_requests WHERE purge_after < ?1`).bind(now).run();
      await env.DB.prepare(
        `DELETE FROM rate_limits WHERE window_start < ?1`).bind(now - RATE_WINDOW_MS * 6).run();
      // access_log is retained longer than the data it describes: it is the evidence
      // that access was controlled, and must outlive the records themselves.
      await env.DB.prepare(
        `DELETE FROM access_log WHERE at < ?1`).bind(now - 365 * 86400_000).run();
    })());
  },
};
