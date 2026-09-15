-- SeRAPHIM SOS — Cloudflare D1 (SQLite) schema.
--
-- This table holds SENSITIVE PERSONAL DATA under Thailand's PDPA: precise location,
-- health needs, vulnerability, and phone numbers of people in danger. Every design
-- choice below follows from that, and none of it can be retrofitted later:
--   * data minimisation — we collect only what a responder needs to act
--   * purpose limitation — recorded consent, with a timestamp, on every record
--   * storage limitation — every row carries its own purge deadline
--   * accountability      — every read of personal data is logged, every change audited
--
-- D1 free tier is 500 MB. Only SOS lives here; gauge history never touches it.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Requests for help.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sos_requests (
  id              TEXT PRIMARY KEY,
  created_at      INTEGER NOT NULL,          -- epoch ms, UTC
  updated_at      INTEGER NOT NULL,

  status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','triaged','assigned','in_progress',
                                      'resolved','cancelled','duplicate')),
  -- 1..5, derived at write time then adjustable by a responder who can see the situation.
  severity        INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
  severity_auto   INTEGER NOT NULL CHECK (severity_auto BETWEEN 1 AND 5),

  lat             REAL NOT NULL CHECK (lat BETWEEN -90 AND 90),
  lon             REAL NOT NULL CHECK (lon BETWEEN -180 AND 180),
  -- Truncated geohash, for "these 14 requests are one street" clustering.
  geohash         TEXT NOT NULL,          -- 7 chars ~150 m, clustered by prefix
  province        TEXT,
  district        TEXT,

  people_count    INTEGER CHECK (people_count IS NULL OR people_count BETWEEN 0 AND 10000),
  needs           TEXT NOT NULL DEFAULT '[]',  -- JSON array of need codes
  water_depth_cm  INTEGER CHECK (water_depth_cm IS NULL OR water_depth_cm BETWEEN 0 AND 2000),
  note            TEXT,

  -- Personal data. Readable only by verified responders, and every read is logged.
  contact_name    TEXT,
  contact_phone   TEXT,
  safe_to_call    INTEGER NOT NULL DEFAULT 1 CHECK (safe_to_call IN (0,1)),

  -- Denormalised flag so the triage queue can prioritise without parsing JSON.
  has_vulnerable  INTEGER NOT NULL DEFAULT 0 CHECK (has_vulnerable IN (0,1)),

  source          TEXT NOT NULL DEFAULT 'web' CHECK (source IN ('web','sms','phone','import')),
  -- Stable per-browser id. The device is the real actor; an IP is a shared resource.
  device_hash     TEXT,
  -- Client-generated id. An offline phone may retry the same request many times as
  -- connectivity flickers; this is what stops one family becoming twelve pins.
  client_id       TEXT,

  -- PDPA: recorded lawful basis and retention deadline, per row.
  consent_at      INTEGER NOT NULL,
  purge_after     INTEGER NOT NULL,

  assigned_to     TEXT,
  resolved_at     INTEGER,
  duplicate_of    TEXT REFERENCES sos_requests(id) ON DELETE SET NULL
);

-- Offline retries must be idempotent, so the same client_id can only land once.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sos_client ON sos_requests(client_id)
  WHERE client_id IS NOT NULL;

-- The triage queue: open requests, worst and oldest first.
CREATE INDEX IF NOT EXISTS idx_sos_queue   ON sos_requests(status, severity DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_sos_geo     ON sos_requests(geohash, created_at);
CREATE INDEX IF NOT EXISTS idx_sos_area    ON sos_requests(province, district, status);
CREATE INDEX IF NOT EXISTS idx_sos_purge   ON sos_requests(purge_after);

-- ---------------------------------------------------------------------------
-- Who may act. Trust tiers, so the system is useful with zero verified officials
-- on day one — which is the realistic starting condition.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS actors (
  id          TEXT PRIMARY KEY,
  token_hash  TEXT NOT NULL UNIQUE,          -- SHA-256; the raw token is never stored
  role        TEXT NOT NULL CHECK (role IN ('volunteer','official','admin')),
  name        TEXT,
  org         TEXT,
  -- Optional geographic scope: a district responder should not read the whole country.
  scope_province TEXT,
  created_at  INTEGER NOT NULL,
  expires_at  INTEGER,
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_actors_token ON actors(token_hash, active);

-- ---------------------------------------------------------------------------
-- Audit trail. Every state change, permanently.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sos_updates (
  id          TEXT PRIMARY KEY,
  request_id  TEXT NOT NULL REFERENCES sos_requests(id) ON DELETE CASCADE,
  at          INTEGER NOT NULL,
  actor_id    TEXT,
  action      TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_updates_req ON sos_updates(request_id, at);

-- ---------------------------------------------------------------------------
-- PDPA accountability: who looked at whose personal data, and when.
-- Kept separate from sos_updates because it must survive the request being purged.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS access_log (
  id          TEXT PRIMARY KEY,
  at          INTEGER NOT NULL,
  actor_id    TEXT,
  request_id  TEXT,
  fields      TEXT,                          -- which personal fields were exposed
  ip_hash     TEXT                           -- hashed, never the raw address
);
CREATE INDEX IF NOT EXISTS idx_access_actor ON access_log(actor_id, at);

-- ---------------------------------------------------------------------------
-- Abuse resistance. A false "30 people trapped" diverts a boat from someone real.
--
-- Keyed by an arbitrary bucket ("dev:<hash>" or "ip:<hash>") rather than by IP alone.
-- Thai mobile networks use carrier-grade NAT heavily, so thousands of subscribers can
-- share one public address: a per-IP limit tight enough to stop an abuser would block
-- an entire neighbourhood, and the people behind a shared mobile NAT are exactly the
-- ones least likely to have a landline to call 1784 from.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rate_limits (
  bucket       TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  count        INTEGER NOT NULL DEFAULT 0
);
