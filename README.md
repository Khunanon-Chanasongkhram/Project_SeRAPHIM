# SeRAPHIM

**S**ystem for **e**arly **R**eal-time **A**ssessment & **P**redictive **H**azard **I**ncident **M**onitoring

Flood intelligence and emergency coordination for Thailand, built to scale globally and
to other hazards. Live river gauges, rainfall, tide and terrain on one map, with an
early-warning engine that says *when* a bank overtops, not just that a level is high.

---

## VIBE CODE ALERT

> **This is a vibe code mini project. Please read this before you use it for anything.**
>
> This whole thing was built fast, by one person, with heavy AI assistance, over a
> handful of sessions. It is a personal side project and a learning exercise. It is not
> a product, not an official service, and not something anybody has signed off on.
>
> **What that actually means:**
>
> * **It has never run in production.** At the time of writing it has never been
>   deployed, never served a real user, and never been through a real flood.
> * **No hydrologist, engineer or emergency responder has reviewed it.** The risk
>   scoring is a transparent set of rules I wrote and can explain, not a validated
>   hydrological model. The fishing scoring is folklore plus arithmetic.
> * **The data is third party and can be wrong, late or missing.** Gauges fail. Feeds
>   go down. Forecasts are forecasts. The system tries hard to say so when it does not
>   know, but it cannot know what it does not know.
> * **Time-to-bank is linear extrapolation, not a prediction.** It assumes the river
>   keeps rising at exactly the rate it has been. Real rivers do not agree to that.
> * **The terms of use for the Thai water data are not confirmed yet.** See
>   [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).
>
> **Do not make a safety decision based on this.** If water is rising where you are,
> trust your own eyes, then trust the authorities, then maybe glance at this.
>
> **In an emergency in Thailand, call 1784 (DDPM) or 191. Not this.**
>
> It is open source under AGPL-3.0 so you can read exactly what it does and judge it
> yourself. That is the point. If you find something wrong, please open an issue.

---

## Status

Phases 0, 1, 2, 3, 4 and 6 are code complete and locally verified. Nothing is deployed
yet. Phase 5 (terrain) and Phase 7 (global expansion) are not started.
See [`PROGRESS.md`](PROGRESS.md) for the full build log, including the bugs found along
the way and the ones I caught in my own work.

## Why it exists

Thailand has excellent public water telemetry, 1,121 live gauges, and very little
tooling that turns it into a decision. Most dashboards show a level and a colour. Almost
none say:

> *This bank overtops in about 3.3 hours at the current rate, and a spring high tide at
> 18:40 will slow drainage.*

That sentence is the idea. Whether this delivers it well enough to matter is exactly
what has not been tested yet.

## What it does

**Watch.** 1,121 live Thai river gauges, rainfall, GloFAS discharge forecasts, and tide
prediction at 23 verified coastal points, on one map.

**Predict.** An explainable risk score (1 to 5) across 479 districts in all 77
provinces, including **time-to-bank** and tide compounding, which is the mechanism
behind much of Bangkok's flooding: discharge arriving when a high tide has shut the
outflow. Every score carries the reasoning that produced it, in Thai and English.

**Respond.** Citizens request help offline first (the form works with no signal and
syncs later). Verified responders triage on a live map with duplicate-collapsing
clusters, so fourteen requests from one soi dispatch one boat rather than fourteen. Full
PDPA controls: recorded consent, 90 day auto-purge, access logging.

**Calm mode.** On the days nothing is flooding, the same tide, weather and lunar data
becomes a fishing planner for 40 spots. This is not a side feature. An app opened twice
in a lifetime is an app nobody has installed when it matters. Retention is a safety
feature.

## Architecture: static first, $0/month

Free hosting turned out to improve the design. With no always-on server, the shape
becomes: compute on a schedule, publish static files, serve from a CDN. That is also the
most disaster-resilient option available, since static files on a global CDN are very
hard to knock over.

```
GitHub Actions (cron)
  -> fetch ThaiWater + Open-Meteo + GDACS
  -> normalise to one datum (metres above MSL)
  -> compute freeboard / time-to-bank / tide compounding
  -> stations.geojson + meta.json -> Cloudflare R2/Pages -> CDN -> users
```

**There is no runtime database on the monitoring path.** A million people opening the
map during a flood costs nothing and touches no origin server. A live server is used for
exactly one thing: accepting SOS submissions.

Full detail in [`docs/PLAN.md`](docs/PLAN.md).

## Deploying

See **[`docs/DEPLOY.md`](docs/DEPLOY.md)**. Everything runs on free tiers, and no Node,
npm or wrangler is needed locally, because the Worker deploys from CI. One file,
`web/config.js`, points the site at your services.

## Quickstart

No dependencies. Python 3.11+ standard library only. No `pip install`, no `npm`.

```bash
# fetch live data and build a snapshot
cd workers
python3 -m seraphim.cli build --out ../data
python3 -m seraphim.cli sources              # list registered adapters
python3 -m seraphim.cli build --no-forecast  # water levels only, no upstream calls

# tests
python3 -m unittest discover -s tests -v

# spike load test
python3 -m tests.loadtest

# view it
cd .. && python3 -m http.server 8000
# -> http://localhost:8000/web/

# SOS API locally (no node required)
cd workers && python3 -m seraphim.devserver --port 8788 --seed
# -> citizen form: http://localhost:8000/web/sos.html?api=http://127.0.0.1:8788
# -> ops console:  http://localhost:8000/web/ops.html  (paste the printed token)
```

## Layout

| Path | What |
|---|---|
| **`docs/DEPLOY.md`** | **step by step deployment, start here to go live** |
| `docs/PLAN.md` | architecture, risk engine, phases, risks |
| `docs/SECURITY.md` | security review, 6 findings, all fixed |
| `docs/OPERATIONS.md` | capacity, health thresholds, degraded mode |
| `docs/DATA_SOURCES.md` | every endpoint, marked tested or unverified, with dates |
| `workers/seraphim/adapters/` | one adapter per source, behind `SourceAdapter` |
| `workers/seraphim/models.py` | canonical types; the datum contract lives here |
| `workers/seraphim/risk.py` | risk engine: scoring, time-to-bank, tide compounding |
| `workers/seraphim/history.py` | archive replay and least-squares rate of rise |
| `workers/seraphim/tide.py` | extremes, lunar phase, empirical tidal-range classification |
| `workers/seraphim/astro.py` | sun and moon position, rise, set, transit, validated |
| `workers/seraphim/fishing.py` | solunar windows and transparent bite scoring |
| `workers/seraphim/publish.py` | snapshot writer; the output contract *is* the API |
| `workers/tests/loadtest.py` | spike load test |
| `web/` | map (`index.html`), fishing (`fish.html`), SOS (`sos.html`), ops (`ops.html`) |
| `api/` | Cloudflare Worker and D1 schema for SOS, plus the conformance suite |
| `workers/seraphim/devserver.py` | Python dev server for the SOS API, no node needed |

## Data sources and attribution

See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for verified endpoints and test dates.

* **ThaiWater / Hydro-Informatics Institute (HII)**, Thai gauge telemetry.
  Terms of use **not yet confirmed** with HII. Required before any public launch.
* **Open-Meteo**, GloFAS river discharge, precipitation, tide
  (`sea_level_height_msl`). CC-BY 4.0, non-commercial use.
* **GDACS** and **USGS**, global multi-hazard events.
* Basemap (c) OpenStreetMap contributors, (c) CARTO.

Every endpoint in that file is marked tested or unverified, with the date it was
checked. Nothing entered the code on the strength of documentation alone.

## Design rules

These are enforced in review. The reasoning is in [`CLAUDE.md`](CLAUDE.md).

1. Never invent an endpoint. Probe it and record the result.
2. One canonical datum (metres above MSL). Convert in the adapter, never downstream.
3. A null never becomes `0.0`. "No reading" must not render as "river at zero".
4. Every risk score ships with its reasoning. No bare colour badges.
5. Always display data age. Cron drifts, so nothing is labelled "live".
6. Public pages stay statically pre-rendered.
7. A dead source degrades the map, it never takes it down.
8. Time-to-bank is withheld unless the trend earns it. Weak fits publish nothing.
9. SOS submissions are never lost to a bad connection. They queue locally and retry.
10. Personal data is never returned unauthenticated, and every authorised read is logged.

## Licence

[GNU AGPL-3.0](LICENSE). Chosen deliberately: if someone runs a modified SeRAPHIM as a
public service, the people relying on it are entitled to see what it actually does.
Safety software should be inspectable by the people whose safety depends on it.

That applies to this repository too. Read it before you trust it.
