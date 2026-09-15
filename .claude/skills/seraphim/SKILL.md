---
name: seraphim
description: Conventions and guardrails for the SeRAPHIM flood-intelligence platform. Load when working on water data ingestion, risk scoring, tide/fishing features, the SOS emergency system, or the map UI in this repo.
---

# SeRAPHIM working conventions

Safety-critical water data. Read `docs/PLAN.md` and `PROGRESS.md` before acting.

## Session protocol
1. **Start:** read `PROGRESS.md` → know the phase, next action, blockers.
2. **End (or when context runs low):** append a dated entry to `PROGRESS.md` with
   Done / Decided / Next / Blockers, and update the phase table. Never let a session die
   without writing this — it is the recovery point.

## Data integrity (non-negotiable)
- **Probe before you trust.** No endpoint enters the code until curl-verified and recorded in
  `docs/DATA_SOURCES.md` with a date. Never write a plausible-looking URL from memory.
- **Canonical datum: metres above MSL.** Convert in the source adapter, never downstream.
  `waterlevel_m` and `waterlevel_msl` are different things; ThaiWater often nulls the former.
- **Nulls are everywhere.** Upstream fields are frequently null or empty-string. Every parse
  must handle both. A null that becomes `0` becomes a river at bank level. Never coerce.
- **Timestamps:** store UTC, display Asia/Bangkok. Source strings like `2026-09-15 14:00` are
  local and naive — localise explicitly at the boundary.
- **Staleness is a first-class state.** A gauge that stopped reporting during a flood is a
  signal, not a gap. Track `last_seen` and surface it.

## Risk engine
- Time-to-bank `= freeboard / rate_of_rise`, only when `rate_of_rise > 0`. Guard divide-by-zero,
  clamp absurd values, and label it an **estimate** — it is linear extrapolation, not hydrology.
- Never emit a score without its contributing inputs attached, and never a bare colour badge.
- Prefer the source's own `situation_level` and `diff_wl_bank` as cross-checks; if our number
  disagrees loudly with the official one, show both rather than silently picking.
- Degrade confidence visibly with data age and station sparsity.

## Scale & resilience (free tier — the limits are load-bearing)
- Read paths are **static files on the CDN**. There is no runtime DB on the monitoring path.
  Never introduce one.
- **Keep public pages statically pre-rendered.** SSR would spend the 100k/day Cloudflare Worker
  cap on page views. Workers are for SOS writes only.
- Budget: Workers 100k req/day · D1 **500 MB** (SOS only) · R2 10 GB / $0 egress · Actions free
  on public repos. Gauge history is archived to Parquet in R2, never accumulated in D1.
- **Always show data age.** Actions cron drifts 5–20 min; never render a reading as "live".
- SOS writes: validate → persist → return fast. Enrichment happens out of band.
- Every user-facing surface must have a degraded-mode render using last-known data.
- Assume connectivity fails during floods: offline-first PWA, sync on reconnect.

## SOS & privacy
- Sensitive personal data (location, health, vulnerability). Encrypt at rest, RBAC, retention
  limits, audit log on every read by an official. Designed in, never retrofitted.
- Disclaimer + **1784 (DDPM) / 191** on every emergency surface. Never imply official status.
- Assume abuse: rate limit, dedupe, moderate. A fake request diverts a real boat.

## UI
- Thai-first, bilingual TH/EN. Thai users are the primary audience; don't treat Thai as a translation.
- Legible at a glance on a phone, in daylight, by a stressed person. Emergency UI is not the
  place for cleverness.
- Colour is never the only signal (accessibility + printing + panic).

## Global-readiness
Everything country-specific goes behind `SourceAdapter`. Open-Meteo/GDACS paths are global and
keyless — they are the fallback for any country with no national gauge network.
