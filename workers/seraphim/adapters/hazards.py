"""Global hazard layers: earthquakes and active fires.

Both are fetched during the build and published as static GeoJSON rather than called
from the browser. Two reasons:

  * the NASA FIRMS key never reaches a user's machine, because it never leaves CI
  * the layers are cached, work offline, and cost the visitor two small files

Inspired by the layer set in bilawalsidhu/gods-eye-view (MIT), which uses the same two
upstream sources.
"""

from __future__ import annotations

import csv
import io
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from seraphim.adapters.base import USER_AGENT, fetch_json, num
from seraphim.models import SourceHealth

#: Past 24 hours, all magnitudes. Keyless, CORS-enabled, and tiny.
USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"

#: NASA FIRMS needs a free MAP_KEY. Without one the layer is simply absent, and the
#: UI says why rather than showing an empty toggle.
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}"
FIRMS_SOURCE = "VIIRS_NOAA20_NRT"
#: Thailand and its immediate neighbours: west, south, east, north.
FIRMS_BBOX = "95,4,108,21"
FIRMS_DAYS = 2


def fetch_earthquakes() -> tuple[dict | None, SourceHealth]:
    health = SourceHealth(source="usgs_quakes", ok=False)
    try:
        payload = fetch_json(USGS_URL, timeout=45)
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc)
        return None, health

    features = []
    for f in payload.get("features", []):
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        mag = num(p.get("mag"))
        if len(coords) < 2 or mag is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [coords[0], coords[1]]},
            "properties": {
                "mag": round(mag, 1),
                "place": p.get("place"),
                "time": p.get("time"),
                "depth_km": round(coords[2], 1) if len(coords) > 2 else None,
                "tsunami": int(p.get("tsunami") or 0),
                "url": p.get("url"),
            },
        })
    features.sort(key=lambda f: -f["properties"]["mag"])
    health.ok = True
    health.stations = len(features)
    return ({
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "USGS Earthquake Hazards Program",
        "window": "past 24 hours, all magnitudes",
        "features": features,
    }, health)


#: GDACS: curated global disaster events with an alert level. Unlike the gauge network
#: this needs no sampling and no threshold of our own, because somebody has already
#: decided an event is worth reporting.
GDACS_URL = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
             "?fromDate={since}&alertlevel=Green;Orange;Red")
GDACS_TYPES = {"FL": "flood", "TC": "cyclone", "EQ": "earthquake", "DR": "drought",
               "VO": "volcano", "WF": "wildfire", "TS": "tsunami"}
GDACS_DAYS = 30


def fetch_events() -> tuple[dict | None, SourceHealth]:
    """Current global disaster events, whatever their kind."""
    health = SourceHealth(source="gdacs", ok=False)
    since = (datetime.now(timezone.utc) - timedelta(days=GDACS_DAYS)).date().isoformat()
    try:
        payload = fetch_json(GDACS_URL.format(since=since), timeout=60)
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc)
        return None, health

    features = []
    for f in payload.get("features", []):
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        kind = p.get("eventtype")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [coords[0], coords[1]]},
            "properties": {
                "kind": GDACS_TYPES.get(kind, kind),
                "code": kind,
                "alert": (p.get("alertlevel") or "").lower(),
                "name": p.get("eventname") or p.get("name"),
                "country": p.get("country"),
                "from": p.get("fromdate"),
                "to": p.get("todate"),
                "severity": (p.get("severitydata") or {}).get("severitytext"),
                "url": (p.get("url") or {}).get("report"),
            },
        })
    order = {"red": 0, "orange": 1, "green": 2}
    features.sort(key=lambda f: order.get(f["properties"]["alert"], 3))
    health.ok = True
    health.stations = len(features)
    return ({
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "GDACS (Global Disaster Alert and Coordination System)",
        "window": f"events active in the past {GDACS_DAYS} days",
        "features": features,
    }, health)


def fetch_fires(map_key: str | None = None) -> tuple[dict | None, SourceHealth]:
    """Active fire detections. Returns None when no key is configured."""
    health = SourceHealth(source="nasa_firms", ok=False)
    key = map_key or os.environ.get("FIRMS_MAP_KEY") or ""
    if not key:
        health.error = "no FIRMS_MAP_KEY configured; fire layer omitted"
        # Not a failure: nobody asked for this layer. A key that is present but
        # rejected still fails, because that IS something going wrong.
        health.optional = True
        return None, health

    url = FIRMS_URL.format(key=key, source=FIRMS_SOURCE, bbox=FIRMS_BBOX, days=FIRMS_DAYS)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc)
        return None, health

    if "Invalid MAP_KEY" in text or not text.strip():
        health.error = "FIRMS rejected the key, or returned nothing"
        return None, health

    features = []
    for row in csv.DictReader(io.StringIO(text)):
        lat, lon = num(row.get("latitude")), num(row.get("longitude"))
        if lat is None or lon is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                # Fire radiative power: roughly how much energy the fire is putting out.
                "frp": num(row.get("frp")),
                "brightness": num(row.get("bright_ti4")) or num(row.get("brightness")),
                "confidence": row.get("confidence"),
                "acq_date": row.get("acq_date"),
                "acq_time": row.get("acq_time"),
                "daynight": row.get("daynight"),
            },
        })
    health.ok = True
    health.stations = len(features)
    return ({
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "NASA FIRMS (VIIRS NOAA-20 near real time)",
        "window": f"past {FIRMS_DAYS} days, Thailand region",
        "features": features,
    }, health)


#: Past floods, from the same GDACS catalogue as the live event layer.
#:
#: Queried one year at a time on purpose. The SEARCH endpoint caps a response at 100
#: features with no paging parameter, so a single 2005-2026 request silently returns
#: the most recent 100 events and looks like a complete archive. Verified 2026-09-16:
#: the unscoped 21-year query returned exactly 100, all from 2021 onward, while a
#: scoped 2011 query returns the Thailand flood of August 2011 that the wide one
#: dropped. Per-year keeps every response well inside the cap (the busiest year here
#: held 26 orange/red floods worldwide).
GDACS_PAST_URL = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
                  "?eventlist=FL&fromDate={frm}&toDate={to}&alertlevel=Orange;Red")

#: How far back to build the flood archive. 2005 is early enough to include the events
#: people remember by name, and GDACS coverage before it is patchy.
PAST_FLOOD_FROM_YEAR = 2005


def fetch_past_floods(
    from_year: int = PAST_FLOOD_FROM_YEAR,
    to_year: int | None = None,
    years: list[int] | None = None,
) -> tuple[dict | None, SourceHealth]:
    """Historical orange/red floods, one request per year.

    Only orange and red are kept. GDACS raises a green alert for a great many events
    that never produced anything a person would call a flood, and a map covered in
    them teaches the reader to ignore the layer.

    A year that fails is skipped with a warning rather than aborting: a partial archive
    is still a useful archive, and the caller caches and merges across runs.
    """
    health = SourceHealth(source="gdacs_past", ok=False)
    now = datetime.now(timezone.utc)
    to_year = to_year or now.year
    wanted = years if years is not None else list(range(from_year, to_year + 1))

    features = []
    seen: set = set()
    failed = 0
    for year in wanted:
        url = GDACS_PAST_URL.format(frm=f"{year}-01-01", to=f"{year}-12-31")
        try:
            payload = fetch_json(url, timeout=60)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            health.warnings.append(f"{year}: {exc}")
            continue
        rows = (payload or {}).get("features") or []
        if len(rows) >= 100:
            # The cap again. Say so rather than publish a year that is quietly clipped.
            health.warnings.append(
                f"{year}: hit the 100-event response cap, that year is incomplete")
        for f in rows:
            p = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or []
            eid = p.get("eventid")
            if len(coords) < 2 or eid is None or eid in seen:
                continue
            seen.add(eid)
            frm = str(p.get("fromdate") or "")
            to = str(p.get("todate") or "")
            countries = [c.get("countryname") for c in (p.get("affectedcountries") or [])
                         if isinstance(c, dict) and c.get("countryname")]
            days = None
            try:
                days = (datetime.fromisoformat(to) - datetime.fromisoformat(frm)).days + 1
            except ValueError:
                pass
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [coords[0], coords[1]]},
                "properties": {
                    "id": eid,
                    "alert": (p.get("alertlevel") or "").lower(),
                    "name": p.get("eventname") or p.get("name"),
                    "country": p.get("country"),
                    "countries": ", ".join(countries[:6]) or None,
                    "iso3": p.get("iso3"),
                    "from": frm[:10] or None,
                    "to": to[:10] or None,
                    "days": days,
                    "year": year,
                    "url": (p.get("url") or {}).get("report"),
                },
            })

    if not features:
        health.error = "no historical floods returned"
        return None, health
    # Newest first: the recent ones are the ones people are checking against.
    features.sort(key=lambda f: (f["properties"]["from"] or ""), reverse=True)
    health.ok = True
    health.stations = len(features)
    if failed:
        health.warnings.append(f"{failed} of {len(wanted)} years could not be fetched")
    return ({
        "type": "FeatureCollection",
        "generated_at": now.isoformat(),
        "attribution": "GDACS (Global Disaster Alert and Coordination System)",
        "window": f"orange and red floods, {min(wanted)}-{max(wanted)}",
        "note": ("Reported flood events with their GDACS alert level. A point marks "
                 "where the event was located, not the area that flooded."),
        "years": [min(wanted), max(wanted)],
        "features": features,
    }, health)
