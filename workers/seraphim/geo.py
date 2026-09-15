"""Point in polygon, and joining our data to province shapes.

The province outlines carry English names; our gauge data carries Thai ones. Rather
than hand-typing a table of 77 name pairs and getting one of them subtly wrong, the
mapping is derived from the data itself: every gauge knows its own Thai province, and
every gauge has coordinates, so whichever polygon contains the most gauges claiming a
given province IS that province.

That is self-checking in a way a typed table is not. If the shapes and the gauge data
ever disagree, the coverage count drops and the build says so.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"
PROVINCES_FILE = ASSETS / "th_provinces.geojson"


def load_provinces() -> dict:
    return json.loads(PROVINCES_FILE.read_text(encoding="utf-8"))


def _ring_contains(ring: list, lon: float, lat: float) -> bool:
    """Ray casting across one ring."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            denom = yj - yi
            if denom != 0 and lon < (xj - xi) * (lat - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def polygon_contains(polygon: list, lon: float, lat: float) -> bool:
    """A GeoJSON Polygon coordinate array: outer ring first, then holes."""
    if not polygon or not _ring_contains(polygon[0], lon, lat):
        return False
    # A point inside a hole is outside the polygon.
    return not any(_ring_contains(hole, lon, lat) for hole in polygon[1:])


def feature_contains(feature: dict, lon: float, lat: float) -> bool:
    geom = feature.get("geometry") or {}
    kind, coords = geom.get("type"), geom.get("coordinates") or []
    if kind == "Polygon":
        return polygon_contains(coords, lon, lat)
    if kind == "MultiPolygon":
        return any(polygon_contains(p, lon, lat) for p in coords)
    return False


def bbox(feature: dict) -> tuple[float, float, float, float]:
    """Cheap rejection test before the expensive one."""
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    polys = coords if geom.get("type") == "MultiPolygon" else [coords]
    xs: list[float] = []
    ys: list[float] = []
    for poly in polys:
        for ring in poly:
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def map_provinces(states) -> tuple[dict[str, str], dict]:
    """Work out which Thai province name belongs to each polygon.

    Returns {english_name: thai_name} plus a report on how well it worked, so a bad
    join is visible in the build log rather than showing up as a blank map.
    """
    shapes = load_provinces()
    boxes = [(f, *bbox(f)) for f in shapes["features"]]
    votes: dict[str, Counter] = defaultdict(Counter)
    unplaced = 0

    for s in states:
        admin = s.station.admin
        if not admin or not admin.province:
            continue
        lon, lat = s.station.lon, s.station.lat
        for feature, x0, y0, x1, y1 in boxes:
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            if feature_contains(feature, lon, lat):
                votes[feature["properties"]["name"]][admin.province] += 1
                break
        else:
            unplaced += 1

    mapping: dict[str, str] = {}
    weak: list[str] = []
    for english, counter in votes.items():
        thai, count = counter.most_common(1)[0]
        mapping[english] = thai
        total = sum(counter.values())
        # A clean join is nearly unanimous. Anything less means the shapes and the
        # gauge geocoding disagree near a border, which is worth knowing about.
        if count / total < 0.8:
            weak.append(f"{english}: {count}/{total} agree on {thai}")

    report = {
        "polygons": len(shapes["features"]),
        "mapped": len(mapping),
        "gauges_outside_any_province": unplaced,
        "weak_joins": weak,
    }
    return mapping, report
