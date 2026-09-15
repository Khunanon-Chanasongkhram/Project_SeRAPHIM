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
from datetime import datetime, timezone

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


def fetch_fires(map_key: str | None = None) -> tuple[dict | None, SourceHealth]:
    """Active fire detections. Returns None when no key is configured."""
    health = SourceHealth(source="nasa_firms", ok=False)
    key = map_key or os.environ.get("FIRMS_MAP_KEY") or ""
    if not key:
        health.error = "no FIRMS_MAP_KEY configured; fire layer omitted"
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
