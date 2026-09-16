"""United States, NOAA/NWS river gauges: the third country, and the first one since
Thailand that publishes a real overtopping threshold.

Keyless, open, ~12,800 gauges, two paged calls for the lot.

**Why this endpoint and not the other two.** Three US sources were probed. USGS Water
Services (`waterservices.usgs.gov/nwis/iv`) serves gauge height but only per state, so
covering the country costs ~50 calls and still carries no flood threshold. NWPS
(`api.water.noaa.gov/nwps/v1/gauges`) returns all 12,884 gauges in one 13.5 MB call
with a flood *category* but no threshold numbers, and the numbers live behind 12,884
per-gauge calls. This service carries the observation AND the four threshold stages in
one paged bulk query, so it is the only one of the three that supports a freeboard.

**The US publishes a flood stage, so US gauges get the full treatment.** Unlike the UK,
`flood` is the level at which water leaves the channel and begins to inundate, which is
the same kind of number as Thailand's `min_bank`. So it becomes `bank_msl` and these
gauges earn freeboard, time-to-bank and level 5, none of which UK gauges can have. The
risk engine needed no change for this: its caps key on whether a bank level exists, not
on which country a gauge is in.

**6,848 of 11,462 usable gauges have that flood stage.** The rest fall back to the
weaker `action` stage as a typical-range high, or to trend only. A missing threshold is
left missing rather than guessed from a neighbouring gauge.

**Everything is in feet on the gauge's own datum.** `hdatum` reads "none" for all 12,842
rows, and the observed values mix river stage (1.9 ft) with pool elevation (3199.78 ft)
depending on the gauge. Feet convert to metres here at the boundary, and the datum is
marked `local` so a level is only ever compared with thresholds from the same station.

**`obstime` is UTC, verified not assumed.** It carries no offset. Cross-referenced
against the NWPS API, which publishes an explicit `validTime` for the same gauges:
AACS2 read `2026-09-16 00:15:00` here and `2026-09-16T00:15:00Z` there. Reading it as
local time would have shifted every US reading by up to 10 hours and quietly corrupted
every rate of rise.
"""

from __future__ import annotations

from datetime import datetime, timezone

from seraphim.adapters.base import (
    ForecastAdapter,
    SourceAdapter,
    fetch_json,
    ident,
    num,
    parse_local_naive,
    register,
    register_forecast,
    text,
)
from seraphim.models import Admin, Observation, SourceHealth, Station

LAYER = (
    "https://mapservices.weather.noaa.gov/eventdriven/rest/services"
    "/water/riv_gauges/MapServer/0/query"
)
FIELDS = (
    "gaugelid,status,location,waterbody,state,obstime,units,action,flood,moderate,"
    "major,observed,latitude,longitude,hdatum,pedts,secvalue,secunit,lowthresh,wfo"
)
#: The service caps a page at 10,000 rows and reports `exceededTransferLimit` when
#: there are more. Paging is explicit rather than trusting one call to return the lot.
PAGE = 10000
MAX_PAGES = 6

FT_TO_M = 0.3048
KCFS_TO_CMS = 28.316846592

#: SHEF physical-element codes. "H" is a height of water (HG river stage, HP pool,
#: HT tidal, HM/HC/HF/HL variants); "Q" is a discharge. A flow in kcfs parsed as a
#: level would read as a river tens of thousands of feet deep.
HEIGHT_PREFIX = "H"
#: SHEF "HT" is a tidal stage. Those gauges rise and fall twice a day, so a fitted rate
#: of rise describes the last three hours rather than predicting the next twelve.
#: 195 of the 12,842 gauges carry it.
TIDAL_PREFIX = "HT"
#: The only unit this adapter will read a level from. Anything else is refused rather
#: than converted on a guess.
LEVEL_UNIT = "ft"

#: NWS flood categories, worst last. Mapped to our 1-5 as a cross-check on our own
#: scoring, never as a replacement for it.
STATUS_SEVERITY = {
    "no_flooding": 1,
    "low_threshold": 1,
    "action": 2,
    "minor": 3,
    "moderate": 4,
    "major": 5,
}

#: US territory, as boxes. Catches 0,0 and sign-flipped longitudes, which would
#: otherwise drop a gauge into the Atlantic or the wrong hemisphere.
_BOXES = (
    (24.0, 50.0, -125.5, -66.0),    # contiguous 48
    (51.0, 72.0, -180.0, -129.0),   # Alaska
    (18.0, 23.5, -161.5, -154.0),   # Hawaii
    (17.0, 19.0, -68.5, -64.0),     # Puerto Rico, US Virgin Islands
    (13.0, 21.0, 144.0, 146.5),     # Guam, Northern Marianas
    (-15.0, -13.5, -171.5, -168.0),  # American Samoa
)


class NWSRiverGaugesAdapter(SourceAdapter):
    id = "nwsriv"
    attribution = "NOAA / US National Weather Service river gauges (public domain)"
    country = "US"
    #: NWS publishes a per-gauge river forecast (below), which beats a rainfall
    #: proxy over a 22 km cell. Joining the shared grid would also have taken it
    #: from 1,029 cells to 7,284 and blown the Open-Meteo allowance threefold.
    shared_forecast_grid = False

    def fetch(self) -> tuple[list[Station], list[Observation], SourceHealth]:
        health = SourceHealth(source=self.id, ok=False)
        try:
            rows = _fetch_all_pages(LAYER, FIELDS)
        except Exception as exc:  # noqa: BLE001
            health.error = str(exc)
            return [], [], health

        stations: list[Station] = []
        observations: list[Observation] = []
        seen: set[str] = set()
        not_a_level = no_geo = no_reading = no_time = dupes = 0
        with_bank = 0

        for r in rows:
            lid = ident(r.get("gaugelid"))
            if not lid:
                continue
            if lid in seen:
                dupes += 1
                continue

            # --- refuse anything that is not a height in feet -----------------
            pedts = text(r.get("pedts")) or ""
            unit = text(r.get("units"))
            if not pedts.startswith(HEIGHT_PREFIX) or unit != LEVEL_UNIT:
                not_a_level += 1
                continue

            lat, lon = num(r.get("latitude")), num(r.get("longitude"))
            if lat is None or lon is None or not _plausible_us(lat, lon):
                no_geo += 1
                continue

            level_ft = num(r.get("observed"))
            if level_ft is None:
                no_reading += 1
                continue

            observed_at = parse_local_naive(r.get("obstime"), 0.0)
            if observed_at is None:
                no_time += 1
                continue

            seen.add(lid)
            flood_ft = num(r.get("flood"))
            if flood_ft is not None:
                with_bank += 1

            name = text(r.get("waterbody")) or f"gauge {lid}"
            where = text(r.get("location"))
            state = text(r.get("state"))
            wfo = text(r.get("wfo"))

            stations.append(Station(
                source=self.id,
                external_id=lid,
                name=f"{name}{f' at {where}' if where else ''}",
                name_en=f"{name}{f' at {where}' if where else ''}",
                lat=lat, lon=lon,
                # NWS flood stage: the level at which water leaves the channel. The same
                # kind of number as Thailand's bank level, so it earns a freeboard.
                bank_msl=_ft(flood_ft),
                # Marked local, not MSL: hdatum is "none" on every row and the values mix
                # river stage with pool elevation. Comparable only with this station's
                # own thresholds, which is exactly how freeboard uses it.
                datum="local",
                critical_msl=_ft(num(r.get("major"))),
                # Action stage is "high enough to act on", below flood stage. Only ever
                # consulted for gauges with no flood stage, where it is the honest
                # weaker signal rather than a stand-in for overtopping.
                typical_high=_ft(num(r.get("action"))),
                typical_low=_ft(num(r.get("lowthresh"))),
                basin=text(r.get("waterbody")),
                agency="NOAA / National Weather Service",
                tidal=pedts.startswith(TIDAL_PREFIX),
                admin=Admin(country="US", province=state, province_code=state,
                            district=wfo.upper() if wfo else None),
            ))
            observations.append(Observation(
                station_id=stations[-1].id,
                observed_at=observed_at,
                level_msl=round(level_ft * FT_TO_M, 3),
                discharge_cms=_kcfs(r),
                source_severity=STATUS_SEVERITY.get(text(r.get("status")) or ""),
            ))

        for label, count in (("not a water level in feet (discharge, or another unit)", not_a_level),
                             ("missing/implausible coordinates", no_geo),
                             ("no observed value", no_reading),
                             ("unparseable timestamp", no_time),
                             ("duplicate gauge id", dupes)):
            if count:
                health.warnings.append(f"{count} rows skipped: {label}")
        health.warnings.append(
            f"{with_bank} of {len(stations)} have a published flood stage, so the rest "
            "carry a level and a trend but no freeboard or time-to-bank")

        health.ok = bool(stations)
        health.stations = len(stations)
        health.observations = len(observations)
        health.fetched_at = datetime.now(timezone.utc)
        if not stations:
            health.error = "no usable stations"
        return stations, observations, health


def _fetch_all_pages(layer: str = LAYER, fields: str = FIELDS) -> list[dict]:
    """Page through an ArcGIS layer until it stops saying there is more.

    Bounded by MAX_PAGES so a service that always sets `exceededTransferLimit` cannot
    spin the build forever.
    """
    rows: list[dict] = []
    for page in range(MAX_PAGES):
        url = (f"{layer}?where=1%3D1&outFields={fields}&returnGeometry=false"
               f"&resultOffset={page * PAGE}&resultRecordCount={PAGE}&f=json")
        doc = fetch_json(url, timeout=120)
        if not isinstance(doc, dict):
            raise RuntimeError(f"unexpected response shape from {layer}")
        if "error" in doc:
            raise RuntimeError(f"service error from {layer}: {doc['error']}")
        features = doc.get("features") or []
        rows.extend(f.get("attributes", {}) for f in features
                    if isinstance(f, dict))
        if not doc.get("exceededTransferLimit") or not features:
            break
    return rows


def _ft(value: float | None) -> float | None:
    """Feet to metres, preserving None. Converting at the boundary is the datum rule."""
    return None if value is None else round(value * FT_TO_M, 3)


def _kcfs(row: dict) -> float | None:
    """Secondary value, but only when it is actually a discharge in kcfs."""
    if (text(row.get("secunit")) or "").lower() != "kcfs":
        return None
    v = num(row.get("secvalue"))
    return None if v is None else round(v * KCFS_TO_CMS, 3)


def _plausible_us(lat: float, lon: float) -> bool:
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in _BOXES)


register(NWSRiverGaugesAdapter())


# ---------------------------------------------------------------------------
# The forecast side of the same service.
# ---------------------------------------------------------------------------

FORECAST_LAYER = (
    "https://mapservices.weather.noaa.gov/eventdriven/rest/services"
    "/water/riv_gauges/MapServer/1/query"
)
FORECAST_FIELDS = "gaugelid,status,fcsttime,forecast,units,pedts"
#: NWS uses -999 for "no forecast at this gauge", and most gauges have none.
NO_FORECAST = -999.0


class NWSForecastAdapter(ForecastAdapter):
    """The NWS 24-hour river stage forecast: the US's own, better forward signal.

    The shared Open-Meteo grid answers "how much rain is coming near this gauge". This
    answers "what will THIS gauge read tomorrow morning", from NOAA's own hydrological
    models. Where both exist the second is strictly better, which is why US gauges leave
    the shared grid (`shared_forecast_grid = False` above) rather than being fetched
    twice.

    It also costs two calls no matter how many gauges we ask about, so 11,467 American
    gauges add nothing to the Open-Meteo allowance instead of tripling it.

    **2,346 of 13,037 gauges carry a real forecast** (probed 2026-09-16); the rest read
    -999 and get nothing rather than a zero.

    **`fcsttime` is UTC, verified not assumed**, the same way `obstime` was: checked
    against the NWPS API's explicit `validTime` on three gauges, e.g. ABBG1 reads
    `2026-09-16 06:00:00` / 0.3 ft here and `2026-09-16T06:00:00Z` / 0.3 ft there.
    """

    id = "nwsfcst"
    attribution = "NOAA / US National Weather Service river forecast (public domain)"
    country = "US"
    #: Fed real station ids, not grid centroids: this forecasts each gauge individually.
    grid = False
    #: NWS reissues through the day. Three hours keeps it current at two calls a refresh.
    refresh_hours = 3.0

    def fetch_for(
        self, points: list[tuple[str, float, float]]
    ) -> tuple[dict[str, dict], SourceHealth]:
        health = SourceHealth(source=self.id, ok=False)
        known = {sid for sid, _, _ in points}
        try:
            rows = _fetch_all_pages(FORECAST_LAYER, FORECAST_FIELDS)
        except Exception as exc:  # noqa: BLE001
            health.error = str(exc)
            return {}, health

        out: dict[str, dict] = {}
        none_published = wrong_unit = unmatched = 0
        for r in rows:
            lid = ident(r.get("gaugelid"))
            if not lid:
                continue
            level_ft = num(r.get("forecast"))
            if level_ft is None or level_ft <= NO_FORECAST:
                none_published += 1
                continue
            # Same refusal as the observed side: a height in feet, or nothing.
            if (text(r.get("pedts")) or "").startswith(HEIGHT_PREFIX) is False \
                    or text(r.get("units")) != LEVEL_UNIT:
                wrong_unit += 1
                continue
            at = parse_local_naive(r.get("fcsttime"), 0.0)
            if at is None:
                continue
            station_id = f"{NWSRiverGaugesAdapter.id}:{lid}"
            # A forecast for a gauge we did not publish an observation for has nothing
            # to attach to, and must not invent a station.
            if station_id not in known:
                unmatched += 1
                continue
            out[station_id] = {
                "forecast_level": round(level_ft * FT_TO_M, 3),
                "forecast_level_at": at.isoformat(),
                "forecast_level_source": "NWS",
            }

        if none_published:
            health.warnings.append(
                f"{none_published} gauges publish no forecast (-999), left empty")
        if wrong_unit:
            health.warnings.append(f"{wrong_unit} forecasts skipped: not a height in feet")
        if unmatched:
            health.warnings.append(
                f"{unmatched} forecasts skipped: no matching observed gauge")

        health.ok = True
        health.stations = len(out)
        health.observations = len(out)
        health.fetched_at = datetime.now(timezone.utc)
        return out, health


register_forecast(NWSForecastAdapter())
