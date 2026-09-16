# Data Sources, verified status

Legend: ✅ tested working, keyless · 🔑 needs key/registration · ⚠️ needs legal check · ❓ unverified

Last verified: 2026-09-16

## Tier 1, Thailand core (verified live)

### ThaiWater / HII, National Hydroinformatics Data Center ✅⚠️
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
- `situation_level` (1-5) → official severity
- `storage_percent`
- `station.tele_station_lat/long`, `basin`, `geocode` (province/amphoe/tambon codes), `agency`
- Updates ~hourly (sample showed `2026-09-15 14:00`)

> ⚠️ Terms of use NOT yet confirmed. Must contact HII for production use + attribution. Do not ship to real users until cleared.

### ⚠️ `storage_percent` is NOT reservoir storage
Verified 2026-09-15 across **794 stations, 100% agreement**:

    storage_percent = (waterlevel_msl − ground_level) / (min_bank − ground_level) × 100

It is the fraction of **channel depth** filled, bed to bank, so 115% means 15% *above
bank*, i.e. flooding. Reading it as "reservoir 115% full" would be badly wrong in the
most dangerous possible direction. There is no dam or reservoir dataset in this feed:
every one of the 1,121 stations is `station_type: tele_waterlevel`.

**Consequence:** reservoir drawdown is deliberately NOT modelled in the fishing planner.
It needs a real dam dataset from RID or EGAT.

### ⚠️⚠️ `min_bank == 0` means "no bank level", not "the bank is at 0 m MSL"
**Found 2026-09-16, after it had been shipping wrong numbers for days.**

318 of 1,118 stations carry `min_bank: 0`. It is a missing-value sentinel, and the
source does not handle it either:

- for those rows `diff_wl_bank` is simply the water level (a gauge at 164.89 m MSL gets
  `diff_wl_bank: 164.89`)
- `diff_wl_bank_text` therefore says **ล้นตลิ่ง** (overflowing) for **302** of them,
  purely because their level is above zero
- the source refuses to publish a `storage_percent` for any of the 318, which is the
  tell: it knows it has no usable bank there

**Consequence, before the fix:** this project showed **~308 Thai rivers over their banks**
while thaiwater.net showed an ordinary monsoon. Of the 800 stations with a real bank
level, **6** are actually over it (12 once `left_bank`/`right_bank` fallbacks are used).

⚠️ **The 2026-09-15 "1,121/1,121 signs agreed" cross-check did not catch this, because
it was circular.** Our freeboard and the source's `diff_wl_bank_text` are both computed
from the same `min_bank`, so both were wrong in the same direction and agreed perfectly.
A cross-check against a field derived from the field you are validating proves nothing.

**The independent signal is `storage_percent`**, which is now cross-checked separately:
if we call a station over bank, the source's own percentage must agree it is past 100.
Verified after the fix: 0 disagreements.

**`left_bank` / `right_bank` are a valid fallback.** 312 of the 318 zero-`min_bank`
stations publish real left/right bank levels in MSL, consistent with their water levels
(median freeboard 3.3 m). Only 6 stations end up with no threshold at all.

### ⚠️ Impossible water levels are published as facts
Measured 2026-09-16, 5 readings refused:
- `วัดเสมาท่าค้อ` reported **-875.7 m MSL**
- four stations reported exactly **-9.99 m**, one of them (`สถานีคลองหวะ`) sitting
  **23.4 m below its own surveyed bed**

Each produced a large fake freeboard, i.e. "safe". Gate: outside **-20..2600 m MSL**
(Thai terrain, delta to Doi Inthanon), or more than **2 m below the station's own
`ground_level`**. The separation is clean: the sentinels sit 7.7-23.4 m below bed, the
worst genuine reading is 1.84 m below and comes with a negative `storage_percent`, which
the source publishes deliberately for a channel drying below its surveyed bed.

### ThaiWater dams / reservoirs ✅ (verified 2026-09-16)
`https://api-v3.thaiwater.net/api/v1/thaiwater30/analyst/dam`, keyless, ~1 MB.

❌ There is **no dam route in the `public/` namespace** — every `public/dam*` guess 404s.
This path came out of thaiwater.net's own JS bundle, not from guessing.

Four groups arrive together and they are not equivalent:

| Group | Rows | Usable? |
|---|---|---|
| `dam_daily` | 50 large dams | ✅ all with coordinates, all current |
| `dam_medium` | 862 medium dams | ✅ 857 with coordinates, **448 current** |
| `dam_small_tele` | 60 | ❌ **no coordinates at all** |
| `dam_hourly` | 17 | ❌ `dam_storage_percent` is 0 for every row |

⚠️⚠️ **The same 1740 problem as the Dutch feed.** `dam_medium` mixes history in with
today: 448 rows from the last day, then **nothing until a year out**, then 317 rows dated
to the **1970 epoch** and 76 from around 2021. Sorting fullest-first without a recency
gate put a reservoir last read in **2021** at the top of the map labelled as spilling
today. Gate: **7 days**, which sits in the wide empty gap.

⚠️ **Zero is a sentinel here too.** 35 of the 50 large dams report `dam_level: 0`, and
every `dam_hourly` row reports `dam_storage_percent: 0` while carrying a real level.

⚠️ **`dam_storage_percent` is percent of USABLE capacity, so >100 is normal and common.**
59 reservoirs are above 100% (max 136%). It means the reservoir is above its normal full
level and is likely spilling. **It does not mean a dam is failing**, and it is not
comparable to the gauge feed's `storage_percent`, which is a bed-to-bank channel fill.
The UI says so in every dam popup.

Published: **493 reservoirs** (50 large + 443 medium), as `dams.geojson`.

### ThaiWater history, ❌ not available
`public/waterlevel_graph` accepts `station_id` but returns a **Go panic** (`index out of range`)
rather than data; other history paths 404. Probing stopped rather than hammer a government API.
**Consequence:** history cannot be bootstrapped, the archive must accumulate, so time-to-bank
is empty for the first ~1-2 h after deployment.

`waterlevel_msl_previous` is **not** usable as a second data point: measured against our archive
on 2026-09-15, station reporting intervals cluster at **10 / 20 / 30 / 60 min** with no
documented rule, so a rate over an assumed Δt would be wrong by up to 6×.

### GISTDA, flood extent / disaster ❓⚠️
`https://disaster.gistda.or.th/` responds HTTP 200. Sentinel-1 SAR derived flood extent,
1/3/7/30-day windows, JSON + WMS/WMTS/TMS tiles. **Endpoints not yet probed**, Phase 1 task.

### TMD, Thai Meteorological Department 🔑⚠️
`https://data.tmd.go.th/api/index1.php`, timed out from here (HTTP 000).
robots.txt disallows automated collection → **must register for official API access**.
Use Open-Meteo as the interim rainfall source; TMD is the authoritative upgrade.

## Tier 2, Global backbone (verified live, keyless, no registration)

These make global scaling real from day one, same code path works for any country.

| Source | Endpoint | Gives us | Status |
|---|---|---|---|
| **Open-Meteo Flood** | `flood-api.open-meteo.com/v1/flood` | GloFAS river discharge forecast, daily, global | ✅ **in production** |
| **Open-Meteo Forecast** | `api.open-meteo.com/v1/forecast` | precipitation forecast, hourly | ✅ **in production** |
| **Open-Meteo Marine** | `marine-api.open-meteo.com/v1/marine` | `sea_level_height_msl` = **tide curve**, wave height | ✅ **in production** |
| **GDACS** | `gdacs.org/gdacsapi/api/events/geteventlist/SEARCH` | global multi-hazard events with alert level | ✅ **in production**, 100 events |
| **USGS** | `earthquake.usgs.gov/.../summary/*.geojson` | earthquakes | ✅ tested |

### Open-Meteo operational notes (verified 2026-09-15)
- **Multi-location batching works**: comma-separated `latitude`/`longitude`, results returned
  **in request order**. Tested to 150 points in one call. We use 100 for URL headroom.
  Order is the *only* thing tying a result to a station, so a length mismatch drops the
  batch rather than risk pairing the wrong forecast to the wrong river.
- **Quota**: 600/min · 5,000/hr · 10,000/day · 300,000/month. Free tier is
  **non-commercial**, data is **CC-BY 4.0, attribution mandatory**.
- **Cadence is per-source**, because they go stale at different rates. Refetching all of
  them on the 30-min level cadence would cost ~100,000 calls/day:

  | Source | Refresh | Why | Calls/day |
  |---|---|---|---|
  | rain | 6 h | weather models publish ~6-hourly; the time-sensitive input | ~4,500 |
  | GloFAS discharge | 24 h | once-daily product; more often returns identical numbers | ~1,100 |
  | marine / tide | 24 h | harmonic prediction, a 7-day curve keeps | ~25 |

  Total ≈ **5,600 location-calls/day** against a 10,000 allowance.
- **GloFAS has no river at ~12-34 of our 1,121 gauges**, expected for canals, gates and
  headwaters below the ~5 km model resolution. Left as `null`, never zero.
- **The marine model only resolves sea cells.** Inland coordinates return an empty series
  (verified at Ayutthaya), so tide uses 23 curated coastal points, each individually probed.

Tide verification (Gulf of Thailand, 13.45N 100.6E): returned a clean semi-diurnal curve,
range −0.48 m to +1.81 m, **2.29 m swing**, matches reality. High/low tide times are derived
as local extrema of this series. This single endpoint powers BOTH the fishing planner AND
coastal backwater-flood compounding.

### Verified tide points (23/23 returning data, 2026-09-15)
Upper Gulf river mouths, Chao Phraya, Tha Chin, Mae Klong, Bang Pakong, swing **~2.3 m**,
the largest in the Gulf, which is exactly why the Bangkok delta suffers backwater flooding.
Southern Gulf (Songkhla, Pattani) is only **0.47-0.63 m**; the Andaman coast **1.7-2.6 m**.

⚠️ **Lunar phase is a poor predictor of tidal range here.** Measured over 25 days at Chao
Phraya mouth: "spring" days averaged 2.17 m vs "neap" 1.99 m, a 9% difference, and two days
within 4 days of the same new moon ranged 2.48 m and 1.81 m. The upper Gulf is mixed and
mainly **diurnal**, modulated by lunar *declination* rather than phase. So range is classified
**empirically** against each location's own recent distribution, which is also what makes the
method valid anywhere the project expands to. Moon phase is retained only for solunar fishing
periods (Phase 3), where it genuinely applies.

## Tier 1c, United States, NOAA / National Weather Service ✅ (verified 2026-09-16)

**Three US sources were probed. Only one of them supports a freeboard.**

| Source | Coverage | Thresholds? | Verdict |
|---|---|---|---|
| `mapservices.weather.noaa.gov/.../water/riv_gauges/MapServer/0/query` | 12,842 gauges, 2 paged calls | ✅ action/flood/moderate/major | ✅ **in production** |
| `api.water.noaa.gov/nwps/v1/gauges` | 12,884 gauges, one 13.5 MB call | ❌ category only, numbers need 12,884 per-gauge calls | used only to cross-check timestamps |
| `waterservices.usgs.gov/nwis/iv` | per **state**, so ~50 calls | ❌ none | not used |

Layer 0 is observed stage, layer 1 is the **NWS 24-hour forecast stage**. Both are bulk,
keyless, public domain, and paginate at 10,000 rows with `exceededTransferLimit`.

- **11,467 usable gauges** after refusing anything that is not a height in feet
  (`pedts` must start with `H`; `Q*` is a discharge, and 94 rows are in kcfs).
- **6,848 publish a flood stage.** That is a genuine overtopping threshold, the same
  kind of number as Thailand's `min_bank`, so **US gauges get freeboard, time-to-bank
  and level 5** — unlike the UK, which publishes only a typical range.
- **2,346 publish an official 24-hour forecast stage** (layer 1); `-999` means none.
- **Feet, on the gauge's own datum.** `hdatum` is `none` on all 12,842 rows and the
  values mix river stage (1.9 ft) with pool elevation (3,199.78 ft). Converted to metres
  at the adapter boundary; datum marked `local`, so a level is only ever compared with
  thresholds from the same station.

### ✅ Freeboard cross-checked against NWS's own flood category
**6,797 gauges, 100.00% sign agreement** (2026-09-16), including all 10 then at or over
flood stage. Same discipline as the ThaiWater `diff_wl_bank_text` check: inverting a
freeboard would turn "overtopped" into "safe".

### ✅ `obstime` and `fcsttime` are UTC, verified not assumed
Neither carries an offset. Cross-referenced against the NWPS API, which publishes an
explicit `validTime`: AACS2 reads `2026-09-16 00:15:00` here and
`2026-09-16T00:15:00Z` there; ABBG1's forecast reads `2026-09-16 06:00:00` / 0.3 ft
against `2026-09-16T06:00:00Z` / 0.3 ft. Reading them as local would have shifted every
US reading by up to 10 hours.

### ⚠️ Adding the US would have blown the Open-Meteo allowance threefold
11,467 American gauges took the shared 0.2° forecast grid from 1,029 cells to **7,284**,
about **36,000 location-calls/day against a 10,000 allowance**. Fixed by the fact that
NOAA already publishes a per-gauge hydrological forecast: US gauges leave the shared grid
(`shared_forecast_grid = False`) and use layer 1 instead, which costs **two calls
regardless of gauge count**. The grid is back to ~644 cells, and the US signal is better
than the rainfall proxy it replaced.

**Extreme values were checked, not clipped.** `MCCI2` reads −65.5 m: it is Chicago's
Deep Tunnel, ~300 ft underground. The 2,550–2,654 m readings are Colorado and Wyoming
mountain reservoirs. Both are real.

## Tier 1d, Netherlands, Rijkswaterstaat ✅ (verified 2026-09-16)

⚠️ **The old host is decommissioned.** `waterwebservices.rijkswaterstaat.nl` 301s to a
migration notice that itself 404s. Live service is **DDAPI 2.0**:

| Endpoint | Method | Returns |
|---|---|---|
| `ddapi20-waterwebservices.rijkswaterstaat.nl/METADATASERVICES/OphalenCatalogus` | POST | 2,499 locations, 7.3 MB |
| `.../ONLINEWAARNEMINGENSERVICES/OphalenLaatsteWaarnemingen` | POST | all 706 water-level locations in **one call, ~9 s** |

### ⚠️⚠️ "OphalenLaatsteWaarnemingen" means "latest value of every series", NOT "current"
Of 2,244 series returned, the **oldest "latest" reading is dated 1740-01-01** and the
median is ~27 years old. Rijkswaterstaat keeps historical series in the same endpoint as
live telemetry, distinguished only by timestamp. **Publishing this unfiltered would have
put 286-year-old marks on a live flood map, each looking like an ordinary reading.**
A hard 24 h recency gate drops them; such a series is not a gauge, so it is dropped
rather than shown as stale.

**After the gate: 318 live locations, median age 22 minutes** — fresher than any other
source in this project.

- **One location, up to 18 parallel series** from different methods, including
  *"Visuele aflezing van blad"* (visual reading off a board). Newest per location wins.
- **Centimetres**, converted to metres at the adapter boundary.
- **Four datums**: NAP 690 locations, PLAATSLR 23, MSL 18, TAW 8. NAP and MSL are kept
  as national datums; **TAW (Belgian, ~2.33 m below NAP) and PLAATSLR are marked
  `local`** rather than offset on a constant nobody here has verified.
- ❌ **No flood threshold exists anywhere in the Dutch public API** (re-probed
2026-09-16). No grens/alarm/waak/norm field in any of 2,143 catalogue metadata entries,
and none in the four documented DDAPI endpoints. waterinfo.rws.nl colour-codes its own
map so thresholds exist internally, and its undocumented API hints at them
(`/api/schematicwaterlevel/get` requires a `criticalLocationName`), but it is
undocumented and unversioned and would break silently. Not used.

⚠️ **The 12 MSL-datum locations are North Sea platforms, not river gauges.** They measure
the tide. They are marked `tidal` so no trend forecast is fitted to them; before that
they published "rising 13 cm/h" and a projected +1.22 m in 12 hours, which was a flooding
tide read as a flood.

**No bank level exists in this feed**, so Dutch gauges carry a level and a trend only,
  never a freeboard, time-to-bank or level 5. Same treatment as the UK.
- The API publishes **no administrative geography**; the area rollup groups by the town
  name already embedded in the station code (`dronten.roggebotsluis.vossemeer`), which
  is a label, not an official boundary.

⚠️ **A plausibility gate that nearly deleted real data.** Fresh values span −146 cm to
**11,968 cm NAP**. 119.68 m looks impossible in a country famous for being flat, but
`epen.geul.cottessen` gauges the Geul in South Limburg, where the valley floor genuinely
sits above 100 m. The gate is set against Dutch terrain (−7 m Zuidplaspolder to 322 m
Vaalserberg), not against the stereotype.

## Browser-side live refresh, CORS probed 2026-09-16

The map can pull the newest readings straight from the agency, so "live" costs our
origin nothing and keeps the static-CDN architecture intact. **Opt-in, default off**,
because it points a visitor's browser and IP at a foreign government API.

| Source | `Access-Control-Allow-Origin` | Live refresh | Payload (gzip) |
|---|---|---|---|
| ThaiWater | reflects origin | ✅ | ~300 KB |
| NOAA `riv_gauges` | reflects origin | ✅ viewport-scoped, paged | 4.8 KB city / 89 KB continental |
| UK EA | `*` | ✅ | ~357 KB |
| **Rijkswaterstaat** | **absent** | ❌ **blocked in a browser** | — |

The Netherlands therefore refreshes on the build cadence only, and the UI says so rather
than offering a toggle that silently does nothing.

⚠️ NOAA caps a viewport response at 10,000 rows and flags `exceededTransferLimit`; at
continental zoom that truncates, so the client pages rather than updating most gauges in
view and quietly leaving the rest on build-time numbers.

**Live station ids verified against published ids:** TH 1117/1117, US 37/37 in a test
viewport. A mismatch would make the overlay silently do nothing.

### ⚠️ Marine tide silently caps at 9 days
Probed 2026-09-16: `forecast_days=10`, `14` and `16` all return exactly **216 non-null
hours**. The API pads the rest with nulls rather than refusing, so asking for 16 days
publishes a week of holes that look like a data gap instead of a product limit. We ask
for 10 and use the 9 that arrive. The fishing planner publishes **7 days**, staying a day
inside both this and the weather horizon so the last day is never half-empty.

Also fetched here now: `wave_height` and `wave_period` (216 h, same coverage), for sea
state on the fishing page.

### Spot weather, extended 2026-09-16
`pressure_msl, wind_speed_10m, wind_direction_10m, wind_gusts_10m, cloud_cover,
precipitation, temperature_2m, weather_code` over **10 days**, all returning full series.
40 spots in one batched request, so the extra variables and days cost one call, not forty.
Pressure and wind feed the bite score; the rest is shown, not scored, because it is what
decides whether the trip is a good idea at all.

### GloFAS discharge, extended to 30 days
`daily=river_discharge,river_discharge_mean,river_discharge_max,river_discharge_min&forecast_days=30`.
The API serves 60, but the ensemble spread is already wide by day 30 (measured max/min
ratios of **15x** at that range on real Thai points), and a number nobody should act on
is not worth the payload. Same number of locations and fetched once a day, so the extra
days cost no additional calls.

⚠️ **This is published as river FLOW, never converted to a water level.** Turning
discharge into metres needs a rating curve per gauge, which nobody publishes and which
our archive is far too short to fit. A level weeks out would be the most confident wrong
number in the project.

## Tier 2e, Flood history (verified 2026-09-16)

Two independent things, kept apart on purpose: a **modelled flow climatology** per grid
cell, and a list of **reported flood events**. Neither is a flood extent. Nothing here
measures how deep any water got, and the UI says so on every surface that shows it.

### Open-Meteo Flood, historical river discharge ✅
Same endpoint as the forecast: `https://flood-api.open-meteo.com/v1/flood`, with
`start_date` / `end_date` instead of `forecast_days`.

**The property this feature rests on, and it was measured, not assumed:** the archive
and the forecast resolve to the **same grid cell** and agree in magnitude. Probed at six
points 2026-09-16:

| Point | Historical max | Forecast today |
|---|---|---|
| 13.72, 100.50 (Bangkok, on-channel) | 7,429 m³/s | 4,275 m³/s |
| 51.50, -0.10 (Thames, London) | 1,264 m³/s | 17 m³/s |
| 15.67, 100.13 (off-channel) | 5.7 m³/s | 1.1 m³/s |

Off-channel cells read tiny in **both**, which is the point: a percentile computed from
a cell's own record and compared with that same cell's own forecast is self-consistent
even where the cell is not the main channel. Absolute accuracy is not what is claimed.

⚠️ **The rate limit is on data volume, not request count**, and this is the constraint
that shaped the whole design. Measured:

| Request | Result |
|---|---|
| 100 cells x 20 years | ❌ `"Your API call requests too much data"` (HTTP 400) |
| 50 cells x 12 years | ✅ 4.24 MB in 2.6 s |
| 25 cells x 20 years | ✅ 3.46 MB in 3.4 s |
| two 50-cell requests inside one minute | ❌ HTTP 429, minutely limit |

So: **50 cells per request, one request per ~65 s**. That is ~600 weighted calls against
a 600/minute allowance, which is why a single 50-cell request very nearly exhausts the
minute on its own. A full build already spends ~5,600 of the 10,000 daily calls, so this
runs as a **separate, budgeted, daily command** (`seraphim.cli floodhist --budget 300`)
and never inside the 15-minute build. Cached for 180 days: a twelve-year climatology
does not move.

Real run, 2026-09-16: 246 of 300 requested Thai cells fetched, one batch of 50 lost to a
429 that survived its retry. The remaining cells are simply picked up by the next run.

**Window:** 2014-01-01 to yesterday (today's value in this endpoint is a forecast, not a
reanalysis). ~4,300 days/cell, ~85 KB/cell.

**Derived per cell, and stored instead of the series:** median / p90 / p95 / p99 / max
with its date, a monthly mean profile, Gumbel-fitted 2-, 5- and 10-year return levels
from annual maxima, and independent high-flow episodes above the 2-year level.

⚠️ **Annual maxima use a water year whose boundary is the cell's own driest month,** not
January. A calendar year splits a monsoon running October to January, which is exactly
how Thailand's worst floods behave, and splitting one flood in two halves its apparent
size. Picking the boundary from the data works in either hemisphere.

⚠️ **A record that will not fit produces no episode count at all.** A river with an
identical peak every year defeats the Gumbel fit (zero variance), and an earlier version
fell through to "0 episodes", which the map reads as "not flood-prone". A failed
measurement was rendering as a reassuring answer. Caught by a test, fixed, and the test
kept.

⚠️⚠️ **Episode FREQUENCY is a tautology and must not be used to rank anything.** A
2-year return level is *defined* as the flow exceeded about once every two years, so
episodes-per-decade is pinned near 5 for every river there is. Measured over the first
246 real Thai cells: 2.4 to 11.0, **median 5.5**, i.e. scatter around the value the
arithmetic forces. A flood-prone classification built on it was ranking rivers by
fitting error. Flood exposure is derived from **duration** instead: days a year above
the 2-year level, which over 31 cells with full daily series runs **0.7 to 11.3 (median
2.6)**, a 16x spread, plus the flood growth ratio (10-year over 2-year level, 1.22-3.96
over the same cells). Class boundaries 3/6/10 days sit near the 60th/85th/97th
percentiles of that sample. **31 mostly-Thai cells is a thin calibration**, and it wants
redoing once the climatology covers more of the world.

### GDACS historical flood events ✅
`https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=FL&fromDate=...&toDate=...&alertlevel=Orange;Red`

⚠️ **The response is capped at 100 features and there is no paging parameter.** A single
2005-2026 query returns exactly 100 events, all from 2021 onward, and looks for all the
world like a complete archive. The 2011 Thailand flood is missing from it. Scoped to
2011 alone, that event is returned normally. **So the archive is fetched one year at a
time**, which keeps every response well inside the cap (the busiest year here held 26
orange/red floods worldwide).

Green is excluded: GDACS raises green for a great many events nobody would call a flood,
and a map covered in them teaches the reader to ignore the layer.

Verified 2026-09-16, querying 2011/2012/2025: 66 events, including the **2011 Thailand
flood (05 Aug 2011 - 09 Jan 2012, 158 days, orange)** and the November-December 2025
southern Thailand floods. Cached 7 days; published as `floods-past.geojson`.

Each event carries a point, not a polygon. A footprint endpoint exists
(`/api/polygons/getgeometry`) and is deliberately not used: it is one request per event
per episode, and the popup says the point marks where the event was reported rather than
the area that flooded.

## Tier 2f, Google Flood Hub 🔑❓ (surface probed 2026-09-16, payload NOT seen)

`https://floodforecasting.googleapis.com/v1`. **Read the two headings below carefully:
half of this is verified and half of it is not, and the split is not the usual one.**

### ✅ What WAS verified here, without a key
Routing happens **before** key validation on this API, which makes the method surface
probeable by anyone. A real method returns `400 INVALID_ARGUMENT` ("API key not valid");
a made-up one returns `404`. Confirmed 2026-09-16:

| Method | Verb | Result | Exists |
|---|---|---|---|
| `floodStatus:searchLatestFloodStatusByArea` | POST | 400 INVALID_ARGUMENT | ✅ |
| `gauges:searchGaugesByArea` | POST | 400 INVALID_ARGUMENT | ✅ |
| `gauges:batchGet` | **GET** | 400 INVALID_ARGUMENT | ✅ |
| `gauges/{gaugeId}` | GET | 400 INVALID_ARGUMENT | ✅ |
| `gaugeModels/{gaugeId}` | GET | 400 INVALID_ARGUMENT | ✅ |
| `serializedPolygons/{id}` | GET | 400 INVALID_ARGUMENT | ✅ |
| `gauges:batchGet` | POST | 404 | ❌ GET only |
| `floodStatus:queryLatestFloodStatusByGaugeIds` | POST | 404 | ❌ does not exist |
| `gaugeModels:batchGet`, `gauges:queryGaugeModels` | POST | 404 | ❌ do not exist |
| `$discovery/rest?version=v1` | GET | 403 | needs a key too |

### ❓ What was NOT verified: the response payload
**No key could be obtained on the machine that built this.** Access is gated behind a
pilot waitlist. So every *field name* in `adapters/googlefloods.py` comes from Google's
published reference, **not from a live response**, which is a weaker footing than
anything else in this file and is treated as such:

- every field is read through `_pick()`, which accepts both `lowerCamelCase` (what REST
  transcoding emits) and `snake_case` (what the reference documents)
- anything unreadable is **dropped and counted** in `SourceHealth.warnings`, so the
  first real run reports what the payload actually looked like instead of silently
  publishing a third of it
- **`python -m seraphim.cli googlefloods --probe` closes the gap in one command**: it
  dumps a live row verbatim and prints pass/fail for every field the adapter reads.
  Run it the first time a key exists, then change this heading to ✅.

### Access, quota, licence
- **API key required**, and access is **waitlisted** (pilot). Form and docs at
  https://developers.google.com/flood-forecasting. Set `GOOGLE_FLOOD_API_KEY`; without
  it the layer is absent and the UI says why, exactly like NASA FIRMS.
- **200 requests/minute.** We use one request per region per 3 h, so this is not close.
- **CC BY 4.0, attribution mandatory**, free tier **non-commercial** — the same terms as
  Open-Meteo, which this project already redistributes as static JSON. Attribution is in
  the layer file and the map credit line.
- Coverage: **150+ countries**, ~5,000 quality-verified points and ~240,000
  lower-confidence ones. Status refreshes several times a day; forecasts run 7 days.

### ⚠️ It publishes NO water level, and that decided the architecture
`FloodStatus` carries `severity`, `forecastTrend`, a `forecastTimeRange` and a
`forecastChange.valueChange` **range** — and no current level. Every other source here
produces a `Station` + `Observation` with a level that the risk engine works from.
Forcing Google into that shape would mean **inventing a level**, so it is deliberately
not a `SourceAdapter`: it publishes as its own layer, `floods-google.geojson`, shown
beside our gauges and never merged into them.

### ⚠️ Thresholds are deliberately not published (yet)
`GaugeModel.thresholds` (warning / danger / extreme danger) come with a
`gaugeValueUnit` of **`METERS` or `CUBIC_METERS_PER_SECOND`**. So "4.2" is a water level
for one gauge and a discharge for the next, and where it is metres it is in the gauge's
**own datum**, not m-MSL. Publishing a bare number beside Thai levels in m-MSL is
exactly the failure this document opens with. Carrying it safely needs
`gaugeModels/{id}` per gauge with the unit welded to the number and `datum: "local"` —
all doable, none of it verifiable without a key. It is the obvious next step **after**
`--probe` passes.

### ⚠️ `UNKNOWN` severity is dropped, not drawn
`Severity` is EXTREME / SEVERE / ABOVE_NORMAL / NO_FLOODING / UNKNOWN / UNSPECIFIED.
Only the first four map to a level (5/4/3/1). UNKNOWN maps to **nothing** and the point
is dropped with a count: "we do not know" and "there is no flooding" are different
statements, and rendering the first as the second is the wrong direction to be wrong in.
A severity value Google adds later is likewise dropped rather than coloured by accident.

### Inundation polygons: available, not fetched
`FloodStatus.inundationMapSet` lists `serializedPolygonId`s, and
`serializedPolygons/{id}` exists. This is a real modelled flood **extent** — the thing
this project has twice refused to fake from elevation. It is still not fetched: it is
one request per polygon per point per refresh, and the payload and terms of caching a
footprint are not something to guess at. The layer publishes **how many** inundation
maps exist at a point, so the capability is visible without the map implying we have
drawn one.

## Tier 2b, Astronomy (computed, not fetched)
Sun and moon positions are computed locally (`workers/seraphim/astro.py`), so calm mode
costs no API quota and works offline.
- **Solar validated against Open-Meteo's own sunrise/sunset** across 5 Thai locations ×
  7 days: agreement within **37 s worst case, 17 s mean** (their values are floored to
  the minute). Fixtures captured in `workers/tests/fixtures_sun.json`.
- **Lunar validated against physical invariants**, no ephemeris available, so the sky is
  the oracle: transit drifts **47.7 min/day** (expect ~50), transit↔antitransit sits
  **12.4 h** apart (half a lunar day), and at full moon **moonrise lands within 0.4 h of
  sunset**.

## Tier 2c, Basemaps and boundaries (verified 2026-09-15)

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

## Tier 2d, Global layers (verified 2026-09-15)

| Source | What | Key? | Status |
|---|---|---|---|
| **USGS** `all_day.geojson` | earthquakes, past 24 h, all magnitudes | no | ✅ 199 on test |
| **RainViewer** `api.rainviewer.com/public/weather-maps.json` | global rain radar, 13 past frames | no | ✅ animated |
| **Radio Browser** `de1.api.radio-browser.info` | geolocated stations | no | ✅ 693 usable of 800 |
| **OpenStreetMap** `tile.openstreetmap.org` | street basemap | no | ✅ (respect the tile usage policy) |
| **NASA FIRMS** `firms.modaps.eosdis.nasa.gov/api/area/csv` | active fires | **yes, free** | ⚠️ optional |

Earthquakes and fires are fetched **during the build** and published as static GeoJSON,
which keeps the FIRMS key in CI and off every visitor's machine. Radar and radio have to
be client side because one is tiles and the other is audio the user chooses.

**Radio streams are filtered to HTTPS only.** An http stream on an https page is blocked
as mixed content, so offering one would be a play button that silently does nothing.
That filter is what takes 800 stations down to about 690.

### NASA GIBS fire tiles: tried and abandoned
GIBS publishes VIIRS thermal anomalies as **vector tiles**, not images, which is why the
`.png` guesses 404'd. Reading the real `ResourceURL` template out of its WMTS capabilities
gave the exact URL shape, and that still 404s at every zoom, date and tile tried,
including over Central Africa and the Amazon in September where fires certainly exist. So
FIRMS with a key is the path, and the layer is optional.

## Tier 4, CCTV / live cameras ❌ NOT AVAILABLE

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

## Tier 3, Terrain (Phase 5)
- **FABDEM** (Copernicus GLO-30 with buildings/trees removed), best free DEM for flood work; non-commercial licence, check
- **Copernicus DEM GLO-30**, open, 30 m
- Derived: **HAND** (Height Above Nearest Drainage), converts a gauge reading into *which neighbourhoods* flood
- Admin boundaries: GADM / Thai TIS-1099 province-amphoe-tambon

## Tier 4, CCTV ❓
Not yet located as a clean public feed. Candidates: RID telemetry portal
(`water.rid.go.th/hydrology/PORTAL/7-TELE.html`), BMA traffic cams, provincial PAO cams.
Expect scraping + legal review. Treat as Phase 1 stretch, not a blocker.
