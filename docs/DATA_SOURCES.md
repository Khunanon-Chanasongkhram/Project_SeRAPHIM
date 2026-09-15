# Data Sources — verified status

Legend: ✅ tested working, keyless · 🔑 needs key/registration · ⚠️ needs legal check · ❓ unverified

Last verified: 2026-09-15

## Tier 1 — Thailand core (verified live)

### ThaiWater / HII — National Hydroinformatics Data Center ✅⚠️
Base: `https://api-v3.thaiwater.net`

| Endpoint | Returns | Size |
|---|---|---|
| `/api/v1/thaiwater30/public/waterlevel` | **1,121 telemetry stations**, live | ~1.8 MB |
| `/api/v1/thaiwater30/public/waterlevel_load` | same + wrapper envelope | ~1.9 MB |
| `/api/v1/thaiwater30/public/rain_24h` | 24h rainfall by station | ~3.0 MB |

Per-station fields that matter (confirmed in live response):
- `waterlevel_msl` + `waterlevel_msl_previous` → **rate of rise (dh/dt)**
- `station.min_bank`, `left_bank`, `right_bank`, `critical_level_msl` → **freeboard**
- `diff_wl_bank` → freeboard already computed by source
- `situation_level` (1–5) → official severity
- `storage_percent`
- `station.tele_station_lat/long`, `basin`, `geocode` (province/amphoe/tambon codes), `agency`
- Updates ~hourly (sample showed `2026-09-15 14:00`)

> ⚠️ Terms of use NOT yet confirmed. Must contact HII for production use + attribution. Do not ship to real users until cleared.

### ⚠️ `storage_percent` is NOT reservoir storage
Verified 2026-09-15 across **794 stations, 100% agreement**:

    storage_percent = (waterlevel_msl − ground_level) / (min_bank − ground_level) × 100

It is the fraction of **channel depth** filled, bed to bank — so 115% means 15% *above
bank*, i.e. flooding. Reading it as "reservoir 115% full" would be badly wrong in the
most dangerous possible direction. There is no dam or reservoir dataset in this feed:
every one of the 1,121 stations is `station_type: tele_waterlevel`.

**Consequence:** reservoir drawdown is deliberately NOT modelled in the fishing planner.
It needs a real dam dataset from RID or EGAT.

### ThaiWater history — ❌ not available
`public/waterlevel_graph` accepts `station_id` but returns a **Go panic** (`index out of range`)
rather than data; other history paths 404. Probing stopped rather than hammer a government API.
**Consequence:** history cannot be bootstrapped — the archive must accumulate, so time-to-bank
is empty for the first ~1-2 h after deployment.

`waterlevel_msl_previous` is **not** usable as a second data point: measured against our archive
on 2026-09-15, station reporting intervals cluster at **10 / 20 / 30 / 60 min** with no
documented rule, so a rate over an assumed Δt would be wrong by up to 6×.

### GISTDA — flood extent / disaster ❓⚠️
`https://disaster.gistda.or.th/` responds HTTP 200. Sentinel-1 SAR derived flood extent,
1/3/7/30-day windows, JSON + WMS/WMTS/TMS tiles. **Endpoints not yet probed** — Phase 1 task.

### TMD — Thai Meteorological Department 🔑⚠️
`https://data.tmd.go.th/api/index1.php` — timed out from here (HTTP 000).
robots.txt disallows automated collection → **must register for official API access**.
Use Open-Meteo as the interim rainfall source; TMD is the authoritative upgrade.

## Tier 2 — Global backbone (verified live, keyless, no registration)

These make global scaling real from day one — same code path works for any country.

| Source | Endpoint | Gives us | Status |
|---|---|---|---|
| **Open-Meteo Flood** | `flood-api.open-meteo.com/v1/flood` | GloFAS river discharge forecast, daily, global | ✅ **in production** |
| **Open-Meteo Forecast** | `api.open-meteo.com/v1/forecast` | precipitation forecast, hourly | ✅ **in production** |
| **Open-Meteo Marine** | `marine-api.open-meteo.com/v1/marine` | `sea_level_height_msl` = **tide curve**, wave height | ✅ **in production** |
| **GDACS** | `gdacs.org/gdacsapi/api/events/geteventlist/SEARCH` | global multi-hazard events w/ alert level | ✅ tested |
| **USGS** | `earthquake.usgs.gov/.../summary/*.geojson` | earthquakes | ✅ tested |

### Open-Meteo operational notes (verified 2026-09-15)
- **Multi-location batching works**: comma-separated `latitude`/`longitude`, results returned
  **in request order**. Tested to 150 points in one call. We use 100 for URL headroom.
  Order is the *only* thing tying a result to a station, so a length mismatch drops the
  batch rather than risk pairing the wrong forecast to the wrong river.
- **Quota**: 600/min · 5,000/hr · 10,000/day · 300,000/month. Free tier is
  **non-commercial**, data is **CC-BY 4.0 — attribution mandatory**.
- **Cadence is per-source**, because they go stale at different rates. Refetching all of
  them on the 30-min level cadence would cost ~100,000 calls/day:

  | Source | Refresh | Why | Calls/day |
  |---|---|---|---|
  | rain | 6 h | weather models publish ~6-hourly; the time-sensitive input | ~4,500 |
  | GloFAS discharge | 24 h | once-daily product; more often returns identical numbers | ~1,100 |
  | marine / tide | 24 h | harmonic prediction, a 7-day curve keeps | ~25 |

  Total ≈ **5,600 location-calls/day** against a 10,000 allowance.
- **GloFAS has no river at ~12-34 of our 1,121 gauges** — expected for canals, gates and
  headwaters below the ~5 km model resolution. Left as `null`, never zero.
- **The marine model only resolves sea cells.** Inland coordinates return an empty series
  (verified at Ayutthaya), so tide uses 23 curated coastal points, each individually probed.

Tide verification (Gulf of Thailand, 13.45N 100.6E): returned a clean semi-diurnal curve,
range −0.48 m to +1.81 m, **2.29 m swing** — matches reality. High/low tide times are derived
as local extrema of this series. This single endpoint powers BOTH the fishing planner AND
coastal backwater-flood compounding.

### Verified tide points (23/23 returning data, 2026-09-15)
Upper Gulf river mouths — Chao Phraya, Tha Chin, Mae Klong, Bang Pakong — swing **~2.3 m**,
the largest in the Gulf, which is exactly why the Bangkok delta suffers backwater flooding.
Southern Gulf (Songkhla, Pattani) is only **0.47–0.63 m**; the Andaman coast **1.7–2.6 m**.

⚠️ **Lunar phase is a poor predictor of tidal range here.** Measured over 25 days at Chao
Phraya mouth: "spring" days averaged 2.17 m vs "neap" 1.99 m — a 9% difference, and two days
within 4 days of the same new moon ranged 2.48 m and 1.81 m. The upper Gulf is mixed and
mainly **diurnal**, modulated by lunar *declination* rather than phase. So range is classified
**empirically** against each location's own recent distribution, which is also what makes the
method valid anywhere the project expands to. Moon phase is retained only for solunar fishing
periods (Phase 3), where it genuinely applies.

## Tier 2b — Astronomy (computed, not fetched)
Sun and moon positions are computed locally (`workers/seraphim/astro.py`), so calm mode
costs no API quota and works offline.
- **Solar validated against Open-Meteo's own sunrise/sunset** across 5 Thai locations ×
  7 days: agreement within **37 s worst case, 17 s mean** (their values are floored to
  the minute). Fixtures captured in `workers/tests/fixtures_sun.json`.
- **Lunar validated against physical invariants** — no ephemeris available, so the sky is
  the oracle: transit drifts **47.7 min/day** (expect ~50), transit↔antitransit sits
  **12.4 h** apart (half a lunar day), and at full moon **moonrise lands within 0.4 h of
  sunset**.

## Tier 2c — Basemaps and boundaries (verified 2026-09-15)

| Source | URL | Licence / terms | Status |
|---|---|---|---|
| **Esri World Imagery** | `server.arcgisonline.com/.../World_Imagery/MapServer/tile/{z}/{y}/{x}` | attribution required: "Imagery © Esri, Maxar, Earthstar Geographics" | ✅ tested, 256px JPEG, no key |
| **Esri Boundaries and Places** | `.../Reference/World_Boundaries_and_Places/...` | same | ✅ tested, PNG label overlay |
| **CARTO dark-matter / positron** | `basemaps.cartocdn.com` | © OpenStreetMap contributors, © CARTO | ✅ in use |
| **Thai province outlines** | `apisit/thailand.json` (vendored) | MIT on the repo | ✅ 77 polygons, 167 KB |

⚠️ The province outline repo is MIT, but **it does not document where its original
shapefile came from**. For drawing approximate province shading that is acceptable; it
would not be if the boundaries were ever used for anything official. Worth replacing with
a sourced dataset if this becomes more than a hobby project.

The outlines carry English province names while the gauge data carries Thai ones. Rather
than hand-typing 77 name pairs, the mapping is **derived from the data**: each gauge knows
its own Thai province and its coordinates, so whichever polygon contains the most gauges
claiming a province is that province. 77/77 resolve, 5 by majority vote across a border,
12 gauges fall outside every polygon (coastal, or simplification artifacts). The build
prints those counts, so a bad join is visible rather than silent.

## Tier 4 — CCTV / live cameras ❌ NOT AVAILABLE

Asked for, and genuinely looked for. Result: **no public camera feed has been found.**

- ThaiWater: `public/cctv`, `public/camera`, `public/cctv_load`, `public/waterlevel_cctv`,
  `public/station_cctv` all return 404. `shared/cctv` returns **403**, so something exists
  behind authentication but is not public.
- GISTDA's disaster portal is a JavaScript app whose API calls are not visible to a plain
  fetch, and the endpoint paths guessed so far all 404.

Provincial and municipal cameras do exist in Thailand, but scattered across sites with no
common API, mostly without any stated terms of use. Scraping them for a public map is both
fragile and legally unclear, so there is no camera layer. The layers panel says so plainly
rather than leaving a gap the user has to interpret.

## Tier 3 — Terrain (Phase 5)
- **FABDEM** (Copernicus GLO-30 with buildings/trees removed) — best free DEM for flood work; non-commercial licence, check
- **Copernicus DEM GLO-30** — open, 30 m
- Derived: **HAND** (Height Above Nearest Drainage) — converts a gauge reading into *which neighbourhoods* flood
- Admin boundaries: GADM / Thai TIS-1099 province–amphoe–tambon

## Tier 4 — CCTV ❓
Not yet located as a clean public feed. Candidates: RID telemetry portal
(`water.rid.go.th/hydrology/PORTAL/7-TELE.html`), BMA traffic cams, provincial PAO cams.
Expect scraping + legal review. Treat as Phase 1 stretch, not a blocker.
