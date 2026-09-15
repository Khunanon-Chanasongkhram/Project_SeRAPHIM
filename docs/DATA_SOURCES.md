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
| **Open-Meteo Flood** | `flood-api.open-meteo.com/v1/flood` | GloFAS river discharge forecast, daily, global | ✅ tested |
| **Open-Meteo Forecast** | `api.open-meteo.com/v1/forecast` | precipitation forecast, hourly | ✅ tested |
| **Open-Meteo Marine** | `marine-api.open-meteo.com/v1/marine` | `sea_level_height_msl` = **tide curve**, wave height | ✅ tested |
| **GDACS** | `gdacs.org/gdacsapi/api/events/geteventlist/SEARCH` | global multi-hazard events w/ alert level | ✅ tested |
| **USGS** | `earthquake.usgs.gov/.../summary/*.geojson` | earthquakes | ✅ tested |

Tide verification (Gulf of Thailand, 13.45N 100.6E): returned a clean semi-diurnal curve,
range −0.48 m to +1.81 m, **2.29 m swing** — matches reality. High/low tide times are derived
as local extrema of this series. This single endpoint powers BOTH the fishing planner AND
coastal backwater-flood compounding.

## Tier 3 — Terrain (Phase 5)
- **FABDEM** (Copernicus GLO-30 with buildings/trees removed) — best free DEM for flood work; non-commercial licence, check
- **Copernicus DEM GLO-30** — open, 30 m
- Derived: **HAND** (Height Above Nearest Drainage) — converts a gauge reading into *which neighbourhoods* flood
- Admin boundaries: GADM / Thai TIS-1099 province–amphoe–tambon

## Tier 4 — CCTV ❓
Not yet located as a clean public feed. Candidates: RID telemetry portal
(`water.rid.go.th/hydrology/PORTAL/7-TELE.html`), BMA traffic cams, provincial PAO cams.
Expect scraping + legal review. Treat as Phase 1 stretch, not a blocker.
