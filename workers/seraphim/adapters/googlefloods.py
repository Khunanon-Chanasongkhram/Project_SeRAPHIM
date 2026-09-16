"""Google Flood Hub, an independent second opinion on flood status.

**What this is.** Google's Flood Forecasting API publishes, for ~5,000 quality-verified
points (plus ~240,000 lower-confidence ones) across 150+ countries, a *flood status*:
a severity class, a trend, a forecast window, and where modelled, inundation map ids.
It is a different kind of thing from every other source here, and the difference decides
the whole design.

**Why this is NOT a SourceAdapter.** Every gauge network in this project produces a
`Station` plus an `Observation` carrying a water level, and the risk engine works from
that level and a bank. Google's flood status carries **no current level at all** — it
carries a severity and a forecast range. Squeezing it into `Observation` would mean
inventing a level, and an invented level is the single most dangerous thing this
codebase can produce (see `models.py` on the datum contract, and the two occasions
terrain was turned into fake depth). So it publishes as its own layer: Google's opinion,
shown beside ours, never blended into it.

**Why no thresholds are published, which is a deliberate omission.** `GaugeModel`
carries `thresholds` (warning / danger / extreme danger) and a `gaugeValueUnit` that is
either `METERS` or `CUBIC_METERS_PER_SECOND`. So a threshold of "4.2" is a water level
for one gauge and a discharge for the next, and where it *is* metres it is in the
gauge's own datum, not metres above MSL — comparable with that gauge's own numbers and
with nothing else on this map. Publishing a bare "4.2" beside Thai levels in m-MSL is
precisely the m vs m-MSL failure this project's rules open with.

Carrying it safely means fetching `gaugeModels/{id}` per gauge, keeping the unit welded
to the number, and rendering `datum: "local"` — all of which is doable, and none of
which can be verified here without a key. So this layer publishes what needs no unit to
be true: Google's severity class, its trend, and its forecast change range labelled as
a change rather than a level. Thresholds are the obvious next step **once somebody has
run `--probe` against a live response.**

**Access.** Needs an API key, and access is gated behind a pilot waitlist
(https://developers.google.com/flood-forecasting). Without `GOOGLE_FLOOD_API_KEY` the
layer is simply absent and the UI says why — the same shape as NASA FIRMS. The key is
used in CI and never reaches a browser.

**Licence.** Data is CC BY 4.0, attribution mandatory, and the free tier is
non-commercial — the same terms as Open-Meteo, which this project already redistributes
as static JSON. Attribution is carried in the published layer and the map credit line.

⚠️ **Verification status.** The method surface below was probed directly on 2026-09-16
and is confirmed: routing happens *before* key validation, so a real method returns 400
INVALID_ARGUMENT ("API key not valid") while a made-up one returns 404. That is how
`gauges:searchGaugesByArea`, `floodStatus:searchLatestFloodStatusByArea`,
`gauges/{id}`, `gaugeModels/{id}`, `gauges:batchGet` and `serializedPolygons/{id}` were
each confirmed to exist without holding a key.

**The response *shapes* below are from Google's published reference, not from a live
response**, because a key could not be obtained here. That is a weaker footing than
everything else in this project, and it is handled by refusing rather than guessing:
every field is read through `_pick`, which accepts both JSON spellings; anything that
cannot be read confidently is dropped and counted in `SourceHealth.warnings`, so the
first real run reports exactly what the payload looked like instead of silently
publishing a third of it. `python -m seraphim.cli googlefloods --probe` checks each
assumption against a live response and prints a pass/fail per field.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from seraphim.adapters.base import num, post_json
from seraphim.models import SourceHealth

BASE = "https://floodforecasting.googleapis.com/v1"

#: Probed 2026-09-16. See the module docstring for how these were confirmed keyless.
#: `gauges:searchGaugesByArea`, `gauges:batchGet` (GET), `gaugeModels/{id}` (GET) and
#: `serializedPolygons/{id}` (GET) were confirmed to exist the same way and are not used
#: yet; the first three are what a thresholds extension needs.
SEARCH_STATUS = "floodStatus:searchLatestFloodStatusByArea"

#: Documented limit is 200 requests/minute. We are nowhere near it: one page per country
#: per refresh, a handful of countries, once every few hours.
PAGE_SIZE = 500

#: Stop after this many pages per region, whatever the token says. A paging bug upstream
#: (or a region far larger than expected) must not turn one build into ten thousand
#: requests against somebody else's quota.
MAX_PAGES = 20

#: Countries to ask about. The API takes a region code and returns that region's points,
#: so this tracks the countries the rest of the map covers rather than fetching a planet
#: of data nobody is looking at.
DEFAULT_REGIONS = ("TH", "GB", "NL", "US")

#: Google's own severity, mapped onto this project's 1-5.
#:
#: UNKNOWN and UNSPECIFIED map to None, deliberately NOT to 1. "We do not know" and
#: "there is no flooding" are different statements, and rendering the first as the
#: second is the direction of error this project refuses to make.
SEVERITY_LEVEL = {
    "EXTREME": 5,
    "SEVERE": 4,
    "ABOVE_NORMAL": 3,
    "NO_FLOODING": 1,
}

SEVERITY_TH = {
    "EXTREME": "รุนแรงมาก",
    "SEVERE": "รุนแรง",
    "ABOVE_NORMAL": "สูงกว่าปกติ",
    "NO_FLOODING": "ไม่มีน้ำท่วม",
}

TREND_TH = {"RISE": "กำลังเพิ่มขึ้น", "FALL": "กำลังลดลง", "NO_CHANGE": "ทรงตัว"}

def _pick(obj: dict, *names, default=None):
    """Read the first present key from several spellings.

    Google's REST transcoding emits lowerCamelCase, but the reference documents the
    proto's snake_case and some Google APIs accept and echo both. Without a live
    response to settle it, reading both is cheap insurance against a parser that
    silently returns nothing — the exact failure that dropped all 1,121 ThaiWater
    stations in Phase 0 because ids were ints and the parser only accepted strings.
    """
    if not isinstance(obj, dict):
        return default
    for n in names:
        if n in obj and obj[n] is not None:
            return obj[n]
    return default


def _latlng(obj: dict) -> tuple[float, float] | None:
    """(lat, lon) from a google.type.LatLng, or None.

    LatLng omits a field when it is zero, so a point on the equator or the prime
    meridian arrives with one key missing. Defaulting the *pair* to None on a missing
    key would silently drop real points; defaulting each component to 0.0 is correct
    here and only here, because that is what the wire format means.
    """
    if not isinstance(obj, dict):
        return None
    lat = num(_pick(obj, "latitude", "lat", default=0.0))
    lon = num(_pick(obj, "longitude", "lng", "lon", default=0.0))
    if lat is None or lon is None:
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    if lat == 0.0 and lon == 0.0:
        # Null Island. Far more likely to be an empty LatLng than a real gauge.
        return None
    return (lat, lon)


def _iso(value) -> str | None:
    """Normalise a timestamp to ISO-8601 UTC, or None. Never invents a time."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def api_key(explicit: str | None = None) -> str:
    return explicit or os.environ.get("GOOGLE_FLOOD_API_KEY") or ""


def _search(method: str, key: str, body: dict, timeout: int = 60) -> dict:
    url = f"{BASE}/{method}?key={key}"
    return post_json(url, body, timeout=timeout)


def _paged(method: str, key: str, body: dict, items_key: tuple[str, ...],
           health: SourceHealth) -> list[dict]:
    """Collect every page of a search, bounded by MAX_PAGES."""
    out: list[dict] = []
    token = None
    for page in range(MAX_PAGES):
        payload = dict(body)
        if token:
            payload["pageToken"] = token
        result = _search(method, key, payload)
        if not isinstance(result, dict):
            health.warnings.append(f"{method}: page {page} was not an object")
            break
        rows = _pick(result, *items_key, default=[])
        if not isinstance(rows, list):
            health.warnings.append(
                f"{method}: expected a list at {items_key[0]}, got {type(rows).__name__}")
            break
        out.extend(r for r in rows if isinstance(r, dict))
        token = _pick(result, "nextPageToken", "next_page_token")
        if not token:
            break
    else:
        health.warnings.append(
            f"{method}: stopped at the {MAX_PAGES}-page ceiling with a token still "
            "outstanding; the result is incomplete")
    return out


def fetch_google_floods(
    key: str | None = None,
    regions: tuple[str, ...] = DEFAULT_REGIONS,
    include_unverified: bool = False,
) -> tuple[dict | None, SourceHealth]:
    """Flood status points for the regions we cover, as a GeoJSON layer.

    Returns (None, health) when no key is configured. That is not a failure: nobody
    asked for this layer, and the UI says why it is absent.
    """
    health = SourceHealth(source="google_floods", ok=False)
    key = api_key(key)
    if not key:
        health.error = ("no GOOGLE_FLOOD_API_KEY configured; Google Flood Hub layer "
                        "omitted (access is gated behind a pilot waitlist)")
        health.optional = True
        return None, health

    features: list[dict] = []
    seen: set[str] = set()
    dropped = {"no_location": 0, "no_severity": 0, "duplicate": 0}
    by_region: dict[str, int] = {}

    for region in regions:
        body = {
            "regionCode": region,
            "pageSize": PAGE_SIZE,
            "includeNonQualityVerified": bool(include_unverified),
        }
        try:
            rows = _paged(SEARCH_STATUS, key, body,
                          ("floodStatuses", "flood_statuses"), health)
        except Exception as exc:  # noqa: BLE001
            # One region failing must not lose the others: a dead Thai response should
            # not take British points off the map.
            health.warnings.append(f"{region}: {exc}")
            continue

        kept = 0
        for row in rows:
            gid = _pick(row, "gaugeId", "gauge_id")
            if gid is not None:
                gid = str(gid)
            here = _latlng(_pick(row, "gaugeLocation", "gauge_location", default={}))
            if here is None:
                dropped["no_location"] += 1
                continue
            severity = str(_pick(row, "severity", default="") or "").upper()
            level = SEVERITY_LEVEL.get(severity)
            if level is None:
                # UNKNOWN, UNSPECIFIED or something new. Counted and dropped rather
                # than drawn in a colour that implies we know something.
                dropped["no_severity"] += 1
                continue
            if gid and gid in seen:
                dropped["duplicate"] += 1
                continue
            if gid:
                seen.add(gid)

            lat, lon = here
            window = _pick(row, "forecastTimeRange", "forecast_time_range", default={}) or {}
            change = _pick(row, "forecastChange", "forecast_change", default={}) or {}
            delta = _pick(change, "valueChange", "value_change", default={}) or {}
            maps = _pick(row, "inundationMapSet", "inundation_map_set", default={}) or {}
            inundation = _pick(maps, "inundationMaps", "inundation_maps", default=[]) or []
            trend = str(_pick(row, "forecastTrend", "forecast_trend", default="") or "").upper()

            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": {
                    "id": f"gfh:{gid}" if gid else None,
                    "gauge_id": gid,
                    "country": region,
                    "severity": severity,
                    "severity_th": SEVERITY_TH.get(severity),
                    # Google's own severity expressed on this project's 1-5, so the map
                    # can colour it consistently. The original string travels with it,
                    # so nobody has to trust the mapping to read the point.
                    "level": level,
                    "trend": trend or None,
                    "trend_th": TREND_TH.get(trend),
                    "quality_verified": bool(
                        _pick(row, "qualityVerified", "quality_verified", default=False)),
                    "issued_at": _iso(_pick(row, "issuedTime", "issued_time")),
                    "forecast_from": _iso(_pick(window, "start")),
                    "forecast_to": _iso(_pick(window, "end")),
                    # The forecast CHANGE, in the gauge's own unit, which is why the
                    # unit is not attached here: it lives on the gauge model, and
                    # without it this pair of numbers is not a level and not a flow.
                    # Published as a range because Google publishes a range.
                    "change_low": num(_pick(delta, "lowerBound", "lower_bound")),
                    "change_high": num(_pick(delta, "upperBound", "upper_bound")),
                    # Whether Google has modelled an inundation footprint here. The
                    # polygons themselves are deliberately NOT fetched; see the module
                    # docstring and docs/DATA_SOURCES.md.
                    "inundation_maps": len(inundation) if isinstance(inundation, list) else 0,
                    "map_inference": _pick(row, "mapInferenceType", "map_inference_type"),
                    "source": _pick(row, "source"),
                },
            })
            kept += 1
        by_region[region] = kept

    for reason, n in dropped.items():
        if n:
            health.warnings.append(f"{n} flood statuses dropped: {reason}")
    if not features:
        health.error = ("no usable flood statuses returned; "
                        f"regions tried: {', '.join(regions)}")
        return None, health

    # Worst first, so a truncated read still shows the points that matter.
    features.sort(key=lambda f: -(f["properties"]["level"] or 0))
    health.ok = True
    health.stations = len(features)
    health.warnings.append(
        "by region: " + ", ".join(f"{k}={v}" for k, v in sorted(by_region.items())))
    return ({
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "Google Flood Hub (CC BY 4.0)",
        "window": "latest flood status per point",
        "quality": ("quality-verified points only"
                    if not include_unverified else "including lower-confidence points"),
        "note": ("Google's own flood severity forecast, shown alongside this project's "
                 "gauge readings rather than merged with them. It carries no water "
                 "level: severity, trend and a forecast change range only."),
        "features": features,
    }, health)


def probe(key: str | None = None, region: str = "TH") -> int:
    """Check the documented schema against a live response, and say what differs.

    Exists because this adapter was written against Google's published reference rather
    than a live payload: a key could not be obtained on the machine that built it. Run
    this the first time a key is available. It prints the raw shape of one row and a
    pass/fail for every field the adapter depends on, so the gap between "documented"
    and "verified" closes in one command instead of being discovered by a blank layer.
    """
    key = api_key(key)
    if not key:
        print("no GOOGLE_FLOOD_API_KEY set; nothing to probe")
        return 2

    print(f"POST {BASE}/{SEARCH_STATUS}  regionCode={region}")
    try:
        raw = _search(SEARCH_STATUS, key, {"regionCode": region, "pageSize": 5})
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        return 1

    top = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
    print(f"top-level keys: {top}")
    rows = _pick(raw, "floodStatuses", "flood_statuses", default=[])
    print(f"rows returned: {len(rows) if isinstance(rows, list) else '(not a list)'}")
    if not rows:
        print("no rows; try another region")
        return 1

    row = rows[0]
    print("\n--- first row, verbatim ---")
    print(json.dumps(row, indent=1, ensure_ascii=False)[:2000])

    print("\n--- assumptions ---")
    checks = [
        ("gauge id", _pick(row, "gaugeId", "gauge_id")),
        ("location", _latlng(_pick(row, "gaugeLocation", "gauge_location", default={}))),
        ("severity", _pick(row, "severity")),
        ("trend", _pick(row, "forecastTrend", "forecast_trend")),
        ("issued time", _iso(_pick(row, "issuedTime", "issued_time"))),
        ("forecast window", _pick(row, "forecastTimeRange", "forecast_time_range")),
        ("forecast change", _pick(row, "forecastChange", "forecast_change")),
        ("inundation set", _pick(row, "inundationMapSet", "inundation_map_set")),
        ("quality flag", _pick(row, "qualityVerified", "quality_verified")),
    ]
    bad = 0
    for name, value in checks:
        ok = value is not None
        bad += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'MISS'} {name:<18} {str(value)[:70]}")

    sev = str(_pick(row, "severity", default="") or "").upper()
    if sev and sev not in SEVERITY_LEVEL and sev not in ("UNKNOWN", "SEVERITY_UNSPECIFIED"):
        print(f"  MISS severity value    {sev!r} is not in SEVERITY_LEVEL — add it")
        bad += 1

    _doc, health = fetch_google_floods(key, regions=(region,))
    print(f"\nadapter on {region}: ok={health.ok} points={health.stations}")
    for w in health.warnings:
        print(f"  warning: {w}")
    if health.error:
        print(f"  error: {health.error}")
    if bad:
        print(f"\n{bad} assumption(s) failed. Fix googlefloods.py before trusting the layer.")
    else:
        print("\nAll documented fields present. Update docs/DATA_SOURCES.md to verified.")
    return 1 if bad else 0
