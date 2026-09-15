"""Terrain: which ground near a gauge sits low enough to be at risk.

The obvious approach does not work. A gauge reports metres above mean sea level and a
DEM reports ground elevation above mean sea level, so subtracting one from the other
looks like it should give flood depth. Measured against four over-bank Thai stations it
gave +1.7 m, -3.7 m, -2.3 m and -0.7 m: two of them claim the river is flowing several
metres underground.

The reason is resolution, not a broken datum. A ~90 m DEM cell at a canal gate contains
the embankment and the buildings beside it, not the water surface, so the "ground
elevation at the gauge" is often the top of the bank. Absolute comparison is therefore
unusable, and using it anyway would draw confident, wrong flood extents.

So this works **relatively**, in the spirit of Height Above Nearest Drainage: the gauge
is the local drainage reference, and every sampled point is expressed as its height
above that reference. A point is flagged when it sits lower than the amount by which the
river is currently over its bank. Relative heights within a kilometre of each other are
far more trustworthy than agreement between two different absolute datums.

What this is NOT: a hydraulic model. There is no flow routing, so it cannot know whether
water can actually reach a point, and it knows nothing about flood defences, culverts or
pumps. It answers one narrow question, which is worth answering plainly: **which nearby
ground is low enough to be under water if the river is out by this much.**
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from seraphim.adapters.base import USER_AGENT

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
#: The API rejects more than this per request.
BATCH = 100
#: Eight compass bearings at three ranges: enough to say which side of a gauge is low,
#: without pretending to a resolution the underlying DEM does not have.
BEARINGS = (0, 45, 90, 135, 180, 225, 270, 315)
RANGES_M = (250, 500, 1000)
SAMPLES_PER_GAUGE = len(BEARINGS) * len(RANGES_M)

#: Be a considerate client of a free service.
PAUSE_SECONDS = 1.2
#: The elevation service occasionally drops a TLS handshake; one retry clears it.
RETRIES = 2

#: How far below the gauge ground must sit before it is worth pointing at. A ~90 m DEM
#: quantised to whole metres is too coarse to make anything of smaller differences.
LOW_GROUND_DROP_M = 1.0

EARTH_R = 6371000.0

#: Shipped with the code like the province outlines: terrain does not change, so it is
#: sampled once and committed rather than refetched on every build.
PROFILES_FILE = Path(__file__).parent / "assets" / "terrain_th.json"


@dataclass(frozen=True, slots=True)
class Profile:
    """Sampled ground around one gauge, as heights relative to the gauge itself."""

    station_id: str
    base_elevation_m: float
    #: Parallel to BEARINGS x RANGES_M, in metres above `base_elevation_m`.
    relative_m: list[float]

    def low_ground(self, min_drop_m: float = LOW_GROUND_DROP_M) -> list[tuple[int, int, float]]:
        """(bearing, range, metres below the gauge) for ground the channel sits above.

        Deliberately NOT a depth. Sampling four over-bank stations showed the gauge
        itself usually sits on the high ground: at one Samut Prakan canal gate every
        sampled point within a kilometre was 1 to 7 metres below the gauge, because the
        canal is embanked above a delta. Subtracting to get a depth there would have
        turned a 0.4 m overtopping into a claim of 3.4 m of water.

        That is not how overtopping works. Water spilling over a crest fills land at a
        rate set by volume and time, not by geometry, and this has neither. So the only
        honest output is WHERE water would go, never HOW DEEP it would get.
        """
        out = []
        i = 0
        for bearing in BEARINGS:
            for metres in RANGES_M:
                rel = self.relative_m[i]
                i += 1
                if rel is not None and rel <= -min_drop_m:
                    out.append((bearing, metres, round(-rel, 1)))
        return out


def offset(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Move a point along a bearing. Flat-earth is fine over a kilometre."""
    b = math.radians(bearing_deg)
    dlat = (distance_m * math.cos(b)) / EARTH_R
    dlon = (distance_m * math.sin(b)) / (EARTH_R * math.cos(math.radians(lat)))
    return lat + math.degrees(dlat), lon + math.degrees(dlon)


def sample_points(lat: float, lon: float) -> list[tuple[float, float]]:
    """The gauge itself, then its ring of samples."""
    pts = [(lat, lon)]
    for bearing in BEARINGS:
        for metres in RANGES_M:
            pts.append(offset(lat, lon, bearing, metres))
    return pts


def _fetch(points: list[tuple[float, float]], timeout: int = 30) -> list[float | None]:
    lats = ",".join(f"{a:.5f}" for a, _ in points)
    lons = ",".join(f"{b:.5f}" for _, b in points)
    req = urllib.request.Request(
        f"{ELEVATION_URL}?latitude={lats}&longitude={lons}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    values = data.get("elevation") or []
    if len(values) != len(points):
        raise RuntimeError(f"expected {len(points)} elevations, got {len(values)}")
    return [None if v is None else float(v) for v in values]


def build_profiles(stations, existing: dict, budget: int = 40, log=print) -> dict:
    """Sample terrain for gauges that do not have a profile yet.

    Bounded per run so a free elevation service is never hammered, and resumable so it
    can complete over several runs. Terrain does not change, so this is paid once.
    """
    todo = [s for s in stations
            if s.id not in existing
            # Only where the water level and the bank share a datum. Without a bank
            # level there is nothing to be "over", so the question does not arise,
            # which is why the UK network is skipped entirely.
            and s.bank_msl is not None and s.datum == "MSL"]
    if not todo:
        log(f"[terrain] {len(existing)} profiles, nothing new to sample")
        return existing

    # Each gauge needs 25 points and the API takes 100, so four gauges ride in one
    # request. That is a quarter of the calls, which matters both for the service and
    # for how long a cold start takes.
    per_call = BATCH // (SAMPLES_PER_GAUGE + 1)
    done = 0
    batch = todo[:budget]
    for start in range(0, len(batch), per_call):
        group = batch[start:start + per_call]
        pts: list[tuple[float, float]] = []
        for st in group:
            pts += sample_points(st.lat, st.lon)

        elevations = None
        for attempt in range(RETRIES + 1):
            try:
                elevations = _fetch(pts)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == RETRIES:
                    log(f"[terrain] batch at {group[0].id} failed: {exc}")
                else:
                    time.sleep(PAUSE_SECONDS * 4)
        if elevations is None:
            continue

        stride = SAMPLES_PER_GAUGE + 1
        for i, st in enumerate(group):
            chunk = elevations[i * stride:(i + 1) * stride]
            base = chunk[0] if chunk else None
            if base is None:
                continue
            existing[st.id] = {
                "base": round(base, 1),
                "rel": [None if e is None else round(e - base, 1) for e in chunk[1:]],
            }
            done += 1
        time.sleep(PAUSE_SECONDS)

    log(f"[terrain] sampled {done} new gauges this run; "
        f"{len(existing)} profiles held, {max(0, len(todo) - done)} still to do")
    return existing


def load_profiles(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_profiles(path: Path, profiles: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profiles, separators=(",", ":")), encoding="utf-8")


def build_layer(states, profiles: dict, generated_at) -> dict | None:
    """Points near over-bank gauges whose ground sits below the water surface."""
    features = []
    covered = 0
    for state in states:
        raw = profiles.get(state.station.id)
        if raw is None:
            continue
        covered += 1
        freeboard = state.freeboard_m
        # Only shown where the channel is actually spilling. Low ground beside a river
        # that is well within its banks is just geography.
        if freeboard is None or freeboard > 0:
            continue
        profile = Profile(state.station.id, raw["base"], raw["rel"])
        for bearing, metres, below in profile.low_ground():
            lat, lon = offset(state.station.lat, state.station.lon, bearing, metres)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                "properties": {
                    "station": state.station.id,
                    "station_name": state.station.name,
                    # How far this ground sits below the gauge, NOT how deep the water
                    # would be. The distinction is the whole point of this layer.
                    "below_gauge_m": below,
                    "over_bank_m": round(abs(freeboard), 2),
                    "distance_m": metres,
                    "province": state.station.admin.province if state.station.admin else None,
                },
            })
    if not covered:
        return None
    return {
        "type": "FeatureCollection",
        "generated_at": generated_at.isoformat(),
        "attribution": "Elevation: Open-Meteo (Copernicus DEM). Analysis: SeRAPHIM",
        "gauges_with_terrain": covered,
        "method": (
            "Ground sampled at 8 bearings and 3 ranges around each gauge, expressed as "
            "height relative to the gauge. Points more than 1 m below the gauge are "
            "shown, but only for gauges whose channel is currently over its bank."
        ),
        "caveat": (
            "This shows WHERE water would go, never HOW DEEP it would get. There is no "
            "flow routing, so it cannot know whether water reaches a point; it knows "
            "nothing about flood defences, culverts or pumps; and the DEM is about 90 m "
            "so it misses anything smaller. Gauges usually sit on the high ground, which "
            "is why depth is not derived: subtracting elevations at one embanked canal "
            "would have turned a 0.4 m overtopping into a claim of 3.4 m of water. "
            "Read it as 'this ground is low and the channel beside it is spilling'."
        ),
        "features": features,
    }
