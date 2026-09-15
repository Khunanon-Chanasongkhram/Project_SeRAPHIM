# SeRAPHIM, Plan

**S**ystem for **e**arly **R**eal-time **A**ssessment & **P**redictive **H**azard **I**ncident **M**onitoring

Thai-first flood intelligence & emergency coordination. Free to host, open source, architected
to scale globally and to other hazards.

Reference feel: `gods-eye-view` (live 3D globe, public data, inspectable) + `pizzint.watch`
(one signal, watched obsessively, instantly legible), but for natural disasters.

---

## 1. Decisions taken (2026-09-15)

| Question | Answer | Consequence |
|---|---|---|
| Context | **Real product for real users** | Reliability, PDPA, legal data terms, disclaimers are in scope - not optional |
| Build first | **Live water + flood map** | It's the spine; risk engine, fishing, SOS all read from it |
| SOS | **Full-featured, clearly marked unofficial** | Real intake + triage, but "call 1784/191" everywhere |
| Stack | **My choice** | Static-first: Python workers + MapLibre + Cloudflare, no runtime DB for monitoring |
| Hosting | **Free tier, open source** | GitHub Actions + GitHub Pages, $0/month, no card |
| Name | **SeRAPHIM** | resolved 2026-09-15 |

---

## 2. What this actually is

Three products sharing one data spine:

1. **Watch** (storm mode), live water levels, rainfall, tide, flood extent, terrain, cameras.
   The "god's eye view" of Thai water.
2. **Predict** (the differentiator), not a dashboard. An engine that answers
   *"will my tambon flood, when, and why."*
3. **Respond** (emergency mode), citizens request help with specifics; officials triage.
   Must survive a traffic spike on the worst day of the year.

Plus **Calm mode**, when nothing is flooding, the same tide/weather/reservoir data becomes a
fishing planner. This is strategically important, not a toy: it keeps the app installed and
trusted on the 350 quiet days so people already have it on the 15 bad ones. **Retention is a
safety feature.**

---

## 3. Architecture, static-first, $0/month

Free hosting is a **constraint that improved the design.** With no budget for an always-on
server, the architecture becomes: compute on a schedule, publish static files, serve from a CDN.
That is also the most disaster-resilient shape available, static files on a global CDN are
nearly impossible to knock over, which is precisely what you want on the worst day of the year.

```
seraphim/
├─ workers/          Python, runs on GitHub Actions cron (free, unlimited for public repos)
│  ├─ adapters/      one per source, behind SourceAdapter (thaiwater, openmeteo, gdacs…)
│  ├─ risk/          freeboard, dh/dt, time-to-bank, tide compounding, scoring
│  └─ publish/       emit snapshot JSON + PMTiles → Cloudflare
├─ web/              static site (Astro or Next static export) + MapLibre GL
├─ api/              Cloudflare Worker, SOS intake only (the one thing needing a live server)
├─ data/             committed snapshots + archived Parquet (doubles as an open data record)
└─ docs/
```

### The data path
```
GitHub Actions (every 15 min, free)
  → fetch ThaiWater + Open-Meteo + GDACS
  → normalise to canonical datum (m MSL)
  → compute risk / time-to-bank / tide compounding
  → write stations.json, risk.json, tide.json, alerts.json + PMTiles
  → push to Cloudflare Pages / R2
  → global CDN  →  users
```

**No runtime database for the monitoring and prediction side at all.** Reads are static files.
A million people opening the map during a flood costs $0 and touches no origin server. This is
the single most important structural decision in the project.

### Verified free-tier budget (checked 2026-09-15)

| Need | Service | Free allowance | Our usage |
|---|---|---|---|
| Scheduled compute | **GitHub Actions** | unlimited on public repos | ~96 runs/day, minutes each |
| Static site + snapshots | **Cloudflare Pages** | effectively unlimited bandwidth | the whole read path |
| Blobs / archive | **Cloudflare R2** | 10 GB, **$0 egress**, 10M reads/mo | PMTiles, Parquet history |
| SOS API | **Cloudflare Workers** | 100k req/day, 10 ms CPU | only SOS writes |
| SOS storage | **Cloudflare D1** | **500 MB** | text + coords ≈ hundreds of thousands of records |

Two consequences worth stating plainly:
- **The site must be statically pre-rendered.** If pages were server-rendered, every view would
  burn a Worker request against the 100k/day cap. Static means page views cost nothing and only
  SOS submissions consume quota, where 100k/day is genuinely ample.
- **D1's 500 MB is for SOS only.** Gauge history (~10M rows/year) never goes near it, recent
  data lives in the snapshots, older data is archived as Parquet in R2.

### Honest limits of free
This proves the concept; it is not yet emergency-grade infrastructure.
- **GitHub Actions cron drifts.** Scheduled runs can be delayed 5-20+ min under platform load,
  so freshness is best-effort. Mitigation: **display data age prominently on every reading** -
  never let the UI imply data is live when it may be 25 minutes old.
- **Scheduled workflows auto-disable after 60 days of repo inactivity.** Our own data commits
  should count as activity, but this is a known silent-failure mode, add a heartbeat check.
- **No SLA anywhere.** Acceptable while proving it works.
- **The upgrade path is cheap and known:** a ~$5/mo VPS or Cloudflare paid Workers removes the
  cron drift and request cap. Nothing in this design has to be rewritten to get there, the
  workers just run somewhere else.

### Why these choices
- **MapLibre GL**, free vector maps, no Mapbox token, thousands of live markers, 3D terrain.
- **PMTiles**, a whole tileset as one file on static hosting. No tile server, no tile costs.
- **Python workers**, rasterio/xarray/geopandas/numpy are where the hydrology and geospatial
  libraries live. Spatial joins and terrain run **offline in Actions**, so no PostGIS is needed
  at runtime; results ship as static geometry.
- **Cloudflare over Vercel**, Vercel's free Hobby tier forbids commercial use, which would cap
  this project's future. Cloudflare's free tier has no such restriction and gives unlimited
  bandwidth, which is exactly the resource a map-heavy app spikes on.

---

## 4. The risk engine (§ the actual intellectual content)

Per station, per cycle:

| Signal | From | Meaning |
|---|---|---|
| **Freeboard** | `min_bank − waterlevel_msl` | metres of headroom left |
| **Rate of rise** | `(wl − wl_previous) / Δt` | m/hr, signed |
| **Time-to-bank (TTB)** | `freeboard ÷ rate_of_rise` | **hours until overtopping** ← headline |
| Upstream rain | ThaiWater `rain_24h`, same basin, upstream | inflow already on the ground |
| Forecast rain | Open-Meteo hourly precip | inflow still to come |
| Discharge forecast | Open-Meteo Flood / GloFAS 7-day | medium-range river trend |
| **Tide compounding** | Open-Meteo `sea_level_height_msl` | high tide blocks drainage → backwater |
| **Terrain (HAND)** | DEM-derived (Phase 5) | turns a stage reading into *which streets* |

**Time-to-bank is the headline number.** Most Thai water dashboards show a level and a colour.
Almost none say *"this bank is overtopped in ~3.3 hours at the current rate."* That sentence is
the product.

**Tide compounding matters specifically for Bangkok and the river mouths.** Chao Phraya
flooding is often not "too much rain" alone, it's high discharge arriving at spring high tide
with nowhere to drain. Peak risk = discharge peak ∩ spring high tide. We can compute that
intersection days ahead, because both inputs are forecastable.

**Every score ships with its reasoning.** A risk badge with no explanation gets ignored or
panics people. The UI always shows: *"Level 4, rose 12 cm/hr for 3 h, 0.4 m below bank,
~3.3 h to overtop, spring high tide 18:40 will slow drainage."* Trust requires the "why".

**Honesty rule:** TTB is a linear extrapolation, not a hydrological model. It is labelled as
an estimate, shown with its inputs, and never presented as certainty. Confidence degrades
visibly with stale or sparse data.

---

## 5. Calm mode, tide & fishing

Derived entirely from the verified marine endpoint + astronomy:
- Tide curve, high/low times (local extrema), spring vs neap, tidal range
- Solunar major/minor periods (moon transit + rise/set), moon phase
- **Bite-window score**, fish feed on *moving* water, so weight |dTide/dt|, not tide height;
  combine with solunar, dawn/dusk, barometric trend, wind
- Water-clarity proxy, recent rain + discharge ⇒ turbidity ⇒ affects tactics
- Reservoir mode, dam storage % and release schedule; drawdown changes where fish hold
- Sea / river / reservoir each get their own scoring profile

---

## 6. Emergency response (SOS) - REMOVED

> **This was built, tested and then taken out before deploying.** The reasoning is worth
> keeping: collecting location, phone number, health needs and vulnerability from people
> in danger makes you responsible for that data and, worse, for the expectation that
> somebody is reading it. A side project with nobody on call cannot honour either. A
> request that goes unanswered is worse than a form that was never offered, because the
> person might have called 1784 instead.
>
> The code is on the `sos-component` branch. Everything below is the original design, kept
> so that if it ever moves somewhere with real staffing the thinking does not start over.



**Citizen**, submit in under 30 seconds, one-handed, possibly offline:
location (GPS or map pin), people count, **specific needs** (boat / medical / food / water /
insulin / infant formula / dialysis / elderly / disabled / pets / livestock), water depth,
photo, contact, safe-to-call flag.

**Trust tiers**, the system must be useful with *zero* verified officials on day one, because
that is the realistic starting condition. Thai flood response is heavily volunteer-driven in
practice (community boat crews, temple networks, local groups), so access is tiered rather than
gated: **public** (submit, see aggregate) → **verified volunteer** (see requests in their area)
→ **verified official** (full detail, assignment, export). Value never waits on institutional
adoption we do not yet have.

**Responder**, triage console: live map + queue, severity + age sort, cluster detection
("14 requests in one soi = send one boat, not fourteen"), assignment, status lifecycle, audit
log, CSV & sit-rep export.

**Non-negotiables**
- **Offline-first PWA.** Floods kill connectivity. The form must work with no signal and sync
  on reconnect. This is the single most important engineering decision in the SOS feature.
- **SMS/USSD fallback** for people with no data.
- **Disclaimer everywhere**, not an official emergency channel; call **1784** (DDPM) or **191**.
- **PDPA.** SOS records contain location + health + vulnerability data on identifiable people.
  That is sensitive personal data under Thai law. Encryption at rest, strict RBAC, retention
  limits, full audit trail, documented lawful basis. Designed in from the first migration,
  never retrofitted.
- **Abuse resistance**, rate limits, bot protection, duplicate detection, moderation. A false
  "30 people trapped" wastes a boat that someone real needed.

---

## 7. Phases

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0** ✅ Foundation | repo skeleton, `SourceAdapter` interface, Actions cron, Cloudflare Pages deploy, snapshot contract | green cron run publishes a snapshot to a live URL |
| **1** ✅ Ingest + Live map | ThaiWater + Open-Meteo adapters, canonical normalisation, MapLibre map on PMTiles | 1,121 stations live on a public map, refreshing every 15 min |
| **2** ✅ Risk engine | freeboard, dh/dt, TTB, rain, tide compounding, explainable scores, alerts | per-tambon risk with a readable "why" |
| **3** ✅ Calm mode | tide curves, solunar, bite windows, reservoirs | 7-day fishing planner for any Thai coast/river point |
| ~~**4** Respond~~ | **BUILT THEN REMOVED 2026-09-15**, before deploy. Preserved on the `sos-component` branch. | see note below |
| **5** Terrain | DEM ingest, HAND, inundation mapping, 3D | "your street floods at 2.1 m at this gauge" |
| **6** ✅ Harden | load test the spike, degraded mode, SMS fallback, observability, security review | survives a simulated national flood event |
| **7** Global + multi-hazard | country adapter pattern, GDACS/USGS, i18n | second country live with no core rewrite |

**Global scaling is already half-solved**, Open-Meteo Flood/Marine/Forecast and GDACS are
global and keyless. Any country gets baseline coverage immediately; national gauge networks
(like ThaiWater) are *upgrades* plugged in behind a `SourceAdapter` interface. That interface
gets designed in Phase 1, even though it has one implementation, so Phase 7 isn't a rewrite.

---

## 8. Risks I'm flagging now

| Risk | Reality | Mitigation |
|---|---|---|
| **Data licensing** | ThaiWater terms unconfirmed; TMD robots.txt forbids scraping | Contact HII + TMD for written terms before public launch. Attribution everywhere. Blocker for "real users", not for building. |
| **Liability** | An unofficial system people trust in a disaster carries real moral weight | Disclaimers, official-channel prompts, never claim authority, publish our accuracy record |
| **PDPA** | SOS data is sensitive personal data | Designed in from migration #1 |
| **The spike is the point** | Normal traffic ~0; the day it matters, traffic is 1000× | Static-snapshot reads + queue-first writes + degraded mode + load test in Phase 6 |
| **Gauge sparsity** | 1,121 stations ≠ full coverage; flash floods outrun gauges | Be explicit about coverage gaps; fuse rainfall + GloFAS + terrain where gauges are absent |
| **Datum confusion** | m vs m-MSL vs local datum - mixing these produces dangerously wrong numbers | One canonical datum in the DB, conversion at the adapter boundary, unit types in `shared` |
| **Free-tier ceiling** | Cron drift (5-20 min), no SLA, 100k Worker req/day | Show data age everywhere; static reads survive origin failure; known $5/mo upgrade path that requires no rewrite |
| **Scope** | This plan is large | Phases ship independently; each is useful alone |

---

## 9. Open decisions

- ~~**Name**~~ → **SeRAPHIM**, resolved 2026-09-15.
- ~~**Hosting/budget**~~ → **free tier, open source**, resolved 2026-09-15.
- **Verifying real officials (Phase 4).** Proposed ladder, cheapest first:
  1. **`.go.th` magic link**, Thai government email domains are a free, automatic signal.
     Send a sign-in link, only accept `*.go.th`. Strong enough to start, costs nothing.
  2. **Manual allowlist**, for a single pilot district, verify by hand. Doesn't scale, and
     doesn't need to; it's the right tool for the first municipality.
  3. **Invite tree**, a verified official invites colleagues, inviter recorded and accountable.
     Scales organically and produces its own audit trail.
  4. **Agency partnership / SSO**, only realistic once there is traction to point at.
  Start with (1) + (2) together. Design for (3). Treat (4) as a later reward, never a dependency.
- **Open-source licence**, AGPL-3.0 (keeps hosted forks open) vs MIT (maximum adoption).
  Leaning AGPL for a public-good tool, but it can deter agency adoption. Needs a call before launch.
- **CCTV**, no clean public feed found yet; may need scraping + legal review.
