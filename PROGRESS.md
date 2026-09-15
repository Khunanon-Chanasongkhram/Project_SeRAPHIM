# Progress log

Append-only. Newest entry at top. **Read this first when resuming a session.**
Purpose: survive a closed terminal or an expired token with zero context loss.

---

## Current state

**Phase:** 0 (Foundation) — ✅ **code complete, locally verified. Not yet deployed.**
**Next action:** user creates the private GitHub repo + pushes; then wire Cloudflare R2 secrets
so the cron publishes to a live URL. Then Phase 1.
**Blockers:** none for building. For *public launch*: ThaiWater terms unconfirmed, TMD needs API key.
**Needs user:** (1) push to private GitHub repo, (2) Cloudflare account for R2 secrets.

### Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Foundation | ✅ code complete, not deployed |
| 1 | Ingest + live map | ⬜ not started |
| 2 | Risk engine | ⬜ not started |
| 3 | Calm mode (tide/fishing) | ⬜ not started |
| 4 | Respond (SOS) | ⬜ not started |
| 5 | Terrain / HAND | ⬜ not started |
| 6 | Harden / scale | ⬜ not started |
| 7 | Global + multi-hazard | ⬜ not started |

---

## 2026-09-15 — Session 3: Phase 0 built

**Done — Phase 0 is code complete and verified against live data.**
- **AGPL-3.0** chosen and `LICENSE` added. Repo to be **private for now**.
- **Cadence corrected for private repos.** GitHub Free gives private repos 2,000 Actions
  min/month, jobs rounded UP to whole minutes. `*/15` = ~2,880 min/mo -> **over budget**.
  Set cron to **`*/30`** (~1,440 min/mo). One-line change to `*/15` or `*/10` when public
  (public repos = unlimited). Documented at the top of `ingest.yml`.
- **Zero third-party dependencies.** Python stdlib only (no node/npm/pip on this machine
  anyway). No install step, fast CI, no supply chain.
- Built: `models.py` (canonical types + datum contract), `adapters/base.py` (`SourceAdapter`
  + null-safe parsers), `adapters/thaiwater.py`, `publish.py`, `cli.py`, 20 unittest tests,
  `ingest.yml`, `keepalive.yml`, static MapLibre map, README.
- **Live verified:** 1,121/1,121 stations parsed. 680 KB -> 79 KB gzipped (8.6x).
  306 at/over bank, 355 stale, median reading age ~64 min. Map + data paths serve over HTTP 200.

**Bugs found and fixed while building**
1. **All 1,121 stations were silently dropped.** ThaiWater sends station ids as *ints*;
   the `text()` helper only accepted `str`, so every row failed the id gate and the build
   published an empty map. Fixed by adding an explicit `ident()` parser. Lesson: the id
   gate needed its own test, and now has one.
2. **Wrong id field.** `row.id` changes every reading (it identifies the *observation*);
   `station.id` is the stable gauge key. Using `row.id` would have created ~1,121 brand-new
   "stations" every 30 minutes and destroyed any history. Now uses `station.id`.

**Validated, not assumed**
- 306 stations at/over bank looked alarmingly high (27%), so it was checked rather than
  shipped: the source's own `diff_wl_bank_text` flags exactly the same set as `ล้นตลิ่ง`
  (overflowing). **Sign agreement 1,121/1,121.** September is peak monsoon — the number is real.
- The sign cross-check is now permanent in the adapter and warns on any disagreement, because
  inverting freeboard would turn "overtopped" into "safe" — the worst failure this system could have.
- **`situation_level` is null for 302 of the 306 overtopped stations.** The source's own
  severity field is unreliable exactly when it matters most, so our computed freeboard is the
  better signal. Do not depend on `situation_level` in Phase 2.

**Deliberately NOT done (and why)**
- **No rate-of-rise / time-to-bank yet.** `waterlevel_msl_previous` exists, but the interval
  between it and the current reading is undocumented. A rate over an assumed delta-t would be a
  guess presented as a measurement. Phase 2 computes it from *our own* archived history, where
  the interval is known. Recorded in `models.py`.
- **Snapshots are not committed to git** — ~600 KB x 48/day is ~10 GB/year. `data/` is gitignored;
  output goes to R2. `keepalive.yml` exists because a repo that never commits would have its
  scheduled workflows silently disabled after 60 days.

---

## 2026-09-15 — Session 2: naming, move, free-tier re-architecture

**Done**
- Renamed project **NAMFAO → SeRAPHIM**
  (*System for early Real-time Assessment & Predictive Hazard Incident Monitoring*).
  All files moved to `/home/khunanon/Mini_Project/Project_SeRAPHIM`, git re-init'd at that root
  (it is the future GitHub repo root). Skill dir renamed to `.claude/skills/seraphim/`.
  Verified zero stale `namfao` references.
- Re-architected for **free hosting + open source**. Verified free-tier limits live:
  - GitHub Actions: **unlimited minutes on public repos**; scheduled workflows auto-disable
    after 60 days of repo inactivity (known silent-failure mode → needs heartbeat)
  - Cloudflare: Workers 100k req/day + 10 ms CPU · **D1 500 MB** (not 5 GB — corrected) ·
    R2 10 GB with **$0 egress** · Pages bandwidth effectively unlimited
- **Key structural decision: no runtime database on the monitoring/prediction path.**
  Actions cron → compute → static snapshot JSON + PMTiles → Cloudflare CDN. A million map views
  cost $0 and touch no origin. Also the most disaster-resilient shape available.
  Corollary: the site MUST stay statically pre-rendered, or page views burn the Worker cap.
- Chose **Cloudflare over Vercel** — Vercel Hobby forbids commercial use, which would cap the
  project's future; Cloudflare free has no such restriction and unlimited bandwidth.
- Added SOS **trust tiers** (public → verified volunteer → verified official) so the system is
  useful with zero verified officials on day one.
- Proposed official-verification ladder: `.go.th` magic link + manual allowlist → invite tree →
  agency SSO. Recorded in PLAN §9.

**Decided**
- Name: SeRAPHIM · Hosting: free tier, open source · no PostGIS/Timescale at runtime
  (spatial work runs offline in Actions, ships as static geometry)

**Open questions for user**
- Open-source licence: AGPL-3.0 (keeps hosted forks open) vs MIT (max adoption)
- Cloudflare + GitHub accounts needed at Phase 0 start

---

## 2026-09-15 — Session 1: research & planning

**Done**
- Confirmed empty project dir; `git init`. Python 3.14.7 available; no node/docker detected yet.
- Researched and **live-tested** data sources (results in `docs/DATA_SOURCES.md`):
  - ✅ ThaiWater `api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel` — **1,121 live
    stations**, keyless. Has `waterlevel_msl`, `waterlevel_msl_previous`, `min_bank`,
    `critical_level_msl`, `diff_wl_bank`, `situation_level`, lat/lon, basin, tambon geocode.
    This is enough for a real early-warning engine.
  - ✅ ThaiWater `/public/rain_24h` — 24h rainfall by station
  - ✅ Open-Meteo Flood (GloFAS river discharge), Forecast (precip), Marine
    (`sea_level_height_msl` = tide; verified 2.29 m swing in Gulf of Thailand)
  - ✅ GDACS global events, USGS earthquakes
  - ❌ TMD `data.tmd.go.th` — HTTP 000 + robots.txt disallows → needs official API key
  - ❓ GISTDA portal up (200) but endpoints unprobed
  - ❓ No clean public CCTV feed found yet
- Key insight: global backbone (Open-Meteo + GDACS) is keyless and worldwide → **global scaling
  is half-solved from day one**; national networks are upgrades behind a `SourceAdapter`.
- Asked user 4 scoping questions. Answers: real product · map first · SOS full-but-unofficial ·
  stack my choice.
- Wrote `docs/PLAN.md`, `docs/DATA_SOURCES.md`, `CLAUDE.md`, `.claude/skills/seraphim/SKILL.md`.

**Decided**
- Stack: Next.js 15 + TS + MapLibre GL + Postgres/PostGIS/TimescaleDB + Python workers, pnpm monorepo
- Headline feature = **time-to-bank** (hours until overtopping), with explainable reasoning
- Tide compounding (spring high tide ∩ discharge peak) is the Bangkok-specific insight
- Calm-mode fishing planner is a retention strategy, not a toy — installed on quiet days = trusted on bad days

**Open questions raised (all since resolved — see Session 2)**
- Final product name · hosting + budget · verifying real officials
