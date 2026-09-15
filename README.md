# SeRAPHIM

**S**ystem for **e**arly **R**eal-time **A**ssessment & **P**redictive **H**azard **I**ncident **M**onitoring

Flood intelligence and emergency coordination — Thailand first, built to scale globally and to
other hazards. Live river gauges, rainfall, tide and terrain on one map, with an early-warning
engine that says *when* a bank overtops, not just that a level is high.

> ⚠️ **SeRAPHIM is not an official emergency channel.**
> In an emergency in Thailand call **1784** (DDPM) or **191**.
> Readings come from third-party telemetry and may be delayed, wrong, or missing.

**Status: Phases 0–4 and 6 complete.** Pre-alpha, not yet deployed.
Security review ([`docs/SECURITY.md`](docs/SECURITY.md)), spike load test and degraded
mode ([`docs/OPERATIONS.md`](docs/OPERATIONS.md)).
1,121 Thai river gauges, rainfall and GloFAS discharge forecasts, tide prediction at 23 coastal
points, and an explainable risk score with **time-to-bank** across 479 districts in all 77
provinces. See [`PROGRESS.md`](PROGRESS.md).

**Calm mode:** the same tide, weather and lunar data becomes a fishing planner for 40
spots — 23 coastal, 17 inland. Not a side feature: an app opened twice in a lifetime is
an app nobody has installed when it matters. Retention is a safety feature.

**SOS:** citizens request help offline-first; verified responders triage on a live map with
duplicate-collapsing clusters. Full PDPA controls — recorded consent, 90-day auto-purge,
access logging. See [`api/README.md`](api/README.md).

> **Time-to-bank needs history.** It is computed from our own archive, so it produces nothing
> until the cron has been running for ~1-2 hours. That is by design: the source's
> `waterlevel_msl_previous` field looked like a shortcut, but its interval varies from 10 to 60
> minutes per station with no documented rule, so a rate derived from it would be wrong by up
> to 6x. Validated end-to-end against replayed archives instead (`tests/test_risk.py`).

---

## Why it exists

Thailand has excellent public water telemetry — 1,121 live gauges — and almost no tooling that
turns it into a decision. Most dashboards show a level and a colour. Almost none say:

> *This bank overtops in ~3.3 hours at the current rate, and a spring high tide at 18:40 will
> slow drainage.*

That sentence is the product.

## Architecture — static-first, $0/month

Free hosting turned out to improve the design. With no always-on server, the shape becomes:
compute on a schedule, publish static files, serve from a CDN. That is also the most
disaster-resilient option available — static files on a global CDN are very hard to knock over,
which is exactly what you want on the worst day of the year.

```
GitHub Actions (cron)
  → fetch ThaiWater + Open-Meteo + GDACS
  → normalise to one datum (metres above MSL)
  → compute freeboard / time-to-bank / tide compounding
  → stations.geojson + meta.json  →  Cloudflare R2/Pages  →  CDN  →  users
```

**There is no runtime database on the monitoring path.** A million people opening the map during
a flood costs nothing and touches no origin server. A live server is used for exactly one thing:
accepting SOS submissions (Phase 4).

Full detail in [`docs/PLAN.md`](docs/PLAN.md).

## Deploying

See **[`docs/DEPLOY.md`](docs/DEPLOY.md)**. Everything runs on free tiers, and no Node,
npm or wrangler is needed locally — the Worker deploys from CI. One file,
`web/config.js`, points the site at your services.

## Quickstart

No dependencies — Python 3.11+ standard library only. No `pip install`, no `npm`.

```bash
# fetch live data and build a snapshot
cd workers
python3 -m seraphim.cli build --out ../data
python3 -m seraphim.cli sources              # list registered adapters
python3 -m seraphim.cli build --no-forecast  # water levels only, no upstream forecast calls
python3 -m seraphim.cli build --refresh-scale 0   # force a forecast refetch

# tests
python3 -m unittest discover -s tests -v

# view the map
cd .. && python3 -m http.server 8000
# → http://localhost:8000/web/

# SOS API locally (no node required)
cd workers && python3 -m seraphim.devserver --port 8788 --seed
# → citizen form: http://localhost:8000/web/sos.html?api=http://127.0.0.1:8788
# → ops console:  http://localhost:8000/web/ops.html  (paste the printed token)
```

## Layout

| Path | What |
|---|---|
| `workers/seraphim/adapters/` | one adapter per source, behind `SourceAdapter` |
| `workers/seraphim/models.py` | canonical types; the datum contract lives here |
| `workers/seraphim/publish.py` | snapshot writer — the output contract *is* the API |
| `workers/seraphim/risk.py` | the risk engine — scoring, time-to-bank, tide compounding, reasons |
| `workers/seraphim/history.py` | archive replay + least-squares rate of rise |
| `workers/seraphim/tide.py` | extremes, lunar phase, empirical tidal-range classification |
| `workers/seraphim/astro.py` | sun/moon position, rise, set, transit — validated |
| `workers/seraphim/fishing.py` | solunar windows and transparent bite scoring |
| `workers/seraphim/cache.py` | per-source forecast cache — keeps us inside the API quota |
| `web/` | map (`index.html`), fishing (`fish.html`), SOS (`sos.html`), ops (`ops.html`) |
| `api/` | Cloudflare Worker + D1 schema for SOS; conformance suite |
| `workers/seraphim/devserver.py` | Python dev server for the SOS API — no node needed |
| `.github/workflows/ingest.yml` | the cron that runs it all |
| **`docs/DEPLOY.md`** | **step-by-step deployment — start here to go live** |
| `docs/PLAN.md` | architecture, risk engine, phases, risks |
| `docs/SECURITY.md` | security review — 6 findings, all fixed |
| `docs/OPERATIONS.md` | capacity, health thresholds, degraded mode |
| `workers/tests/loadtest.py` | spike load test |

## Data sources & attribution

See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for verified endpoints and test dates.

- **ThaiWater / Hydro-Informatics Institute (HII)** — Thai gauge telemetry.
  ⚠️ Terms of use not yet confirmed with HII; required before public launch.
- **Open-Meteo** — GloFAS river discharge, precipitation, tide (`sea_level_height_msl`). CC-BY.
- **GDACS**, **USGS** — global multi-hazard events.
- Basemap © OpenStreetMap contributors, © CARTO.

Every endpoint in that file is marked ✅ tested or ❓ unverified, with the date it was checked.
Nothing enters the code on the strength of documentation alone.

## Design rules

These are enforced in review; the reasoning is in [`CLAUDE.md`](CLAUDE.md).

1. Never invent an endpoint — probe it and record the result.
2. One canonical datum (m MSL). Convert in the adapter, never downstream.
3. A null never becomes `0.0`. "No reading" must not render as "river at zero".
4. Every risk score ships with its reasoning. No bare colour badges.
5. Always display data age. Cron drifts; nothing is labelled "live".
6. Public pages stay statically pre-rendered.
7. A dead source degrades the map, it never takes it down.
8. Time-to-bank is withheld unless the trend earns it — weak fits publish nothing.
9. SOS submissions are never lost to a bad connection; they queue locally and retry.
10. Personal data is never returned unauthenticated, and every authorised read is logged.

## Licence

[GNU AGPL-3.0](LICENSE). Chosen deliberately: if someone runs a modified SeRAPHIM as a public
service, the people relying on it are entitled to see what it actually does. Safety software
should be inspectable by the people whose safety depends on it.
