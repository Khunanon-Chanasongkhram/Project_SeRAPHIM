"""Yala municipal CCTV, a camera pointed at the bridge a gauge is measuring.

**What this is.** Five street cameras published by Yala City Municipality
(เทศบาลนครยะลา) through a vendor at `yala-cctv.localth.ai`. Four of the five sit on
flood infrastructure: two at สะพานท่าสาป (Tha Sap Bridge), one on the river side and
one on the municipality side, one at ตลาดเมืองใหม่ (New City Market) and one at จารู.

**Why a flood project wants them.** The Tha Sap cameras are **170 m and 210 m** from the
ThaiWater gauge บ้านท่าสาบ. They point at the exact bridge that gauge measures. That
makes them the one thing this codebase has never had: a way for a reader to *look at the
river* and check a number against it. Every other cross-check here is one model against
another. This is a picture.

**Why this is NOT a SourceAdapter.** It yields no water level, no observation, no datum
— it yields a video. The same reasoning as `googlefloods.py`: forcing it into
`Observation` would mean inventing a level, and an invented level is the worst thing
this codebase can produce. It publishes as its own layer, beside the gauges, never
merged into them. What it *does* carry is the id of the nearest gauge, so the UI can
put the picture next to that gauge's number without either one claiming to be the other.

**Why the stream URL is not in the snapshot.** The vendor's API mints an AWS Kinesis
Video Streams HLS session URL carrying a `SessionToken`, and those expire. Baking one
into a snapshot that a CDN serves for 15 minutes would publish a URL that is dead before
most people load it. So the snapshot carries the *ingredients* (`customer_code`,
`device_code`) and the browser mints its own session when someone clicks a camera.
Verified: the mint endpoint sends `access-control-allow-origin: *` on GET, so a static
page on our origin can call it. Nothing is fetched from the vendor until a click, which
is also why this does not put a third party in the path of an ordinary map view.

⚠️ **`signal_status` is a lie, and this module does not read it.** All five cameras
report `signal_status: 1`. `BaanRom-04` has no stream at all — the mint endpoint returns
`ResourceNotFoundException: No fragments found in the stream`. This is the same shape as
`situation_level` in ThaiWater, which is null on 302 of 306 overtopped stations: the
source's own status field is unreliable exactly where it matters. So liveness is
**measured**, by minting a session and seeing whether one comes back, and published as
`stream_ok` with the time it was checked. A camera that was dark at build time is still
drawn, because it may have come back; the UI says when it was last known good.

⚠️ **Licence: none found, and that is worse than it sounds.** Probed 2026-09-18: there
is no robots.txt and no terms page (every path returns the same single-page app), the
`localth.ai` apex does not resolve, and the page carries no owner, credit or copyright.
So unlike ThaiWater — whose terms are also unconfirmed, but which at least has HII to
ask — there is no named party to contact here. The layer is published with
`licence: "unknown"` and the UI must not imply permission that nobody granted.

⚠️ **Thai PDPA.** Street CCTV shows identifiable people and vehicles, which is personal
data. The operator publishes these feeds openly and unauthenticated; we do not copy,
record or re-host any frame — the browser fetches from the operator's own origin on an
explicit click. That keeps the operator the data controller. It does not make the
feature free of PDPA consequence, and it is not a substitute for permission.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timezone

from seraphim.adapters.base import USER_AGENT, fetch_json, ident, num, text
from seraphim.models import SourceHealth

#: The vendor's camera list. Static JSON, ~1.5 KB, CORS-open, five records.
CAMERA_LIST_URL = "https://yala-cctv.localth.ai/cameraData.json"

#: Mints an HLS session URL for one camera. `{customerCode}-{deviceCode}`.
#: CORS-open on GET, which is what lets the browser call it directly.
STREAM_URL_TEMPLATE = (
    "https://gg89f6g289.execute-api.ap-southeast-1.amazonaws.com/stream/hls-url/{key}"
)

#: Yala province, generously bounded. A camera outside this is a data error, not a
#: camera: the whole value of the layer is that it sits on known flood infrastructure.
YALA_BOUNDS = (5.6, 100.6, 7.0, 101.9)  # south, west, north, east

#: Beyond this, a camera is not looking at that gauge's water and must not be offered as
#: a check on it. The Tha Sap pair are 0.17 and 0.21 km out; จารู is 4.87 km, which is
#: too far to be evidence about บ้านท่าสาบ and is published unpaired.
MAX_GAUGE_PAIR_KM = 1.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def stream_key(customer_code: str, device_code: str) -> str:
    return f"{customer_code}-{device_code}"


def probe_stream(key: str, timeout: int = 20) -> bool | None:
    """Mint a session and report whether a playable stream came back.

    Three outcomes, and the difference matters. True: a `live_stream_url` exists.
    False: the endpoint answered and said there is no stream (a dark camera). None: we
    could not tell — the network failed, or the response was not the shape we know — and
    None must never be rendered as "dark", because that would report our own outage as
    the municipality's.
    """
    url = STREAM_URL_TEMPLATE.format(key=key)
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None
    # The Lambda reports its own failures inside a 200, so the HTTP status says nothing.
    if payload.get("errorType") or payload.get("errorMessage"):
        return False
    body = payload.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None
    if not isinstance(body, dict):
        return None
    return bool(body.get("live_stream_url"))


def _pair_with_gauge(lat: float, lon: float, states) -> dict:
    """Nearest gauge within `MAX_GAUGE_PAIR_KM`, or an explicit absence.

    Returned unpaired rather than paired-to-something-distant on purpose: a camera
    offered as a check on a gauge 5 km upstream is worse than no camera, because it
    invites a reader to believe a picture that is not of that water.
    """
    best, best_km = None, None
    for s in states or []:
        st = s.station
        if st.lat is None or st.lon is None:
            continue
        km = _haversine_km(lat, lon, st.lat, st.lon)
        if best_km is None or km < best_km:
            best, best_km = s, km
    if best is None or best_km > MAX_GAUGE_PAIR_KM:
        return {"gauge_id": None, "gauge_name": None, "gauge_km": None}
    return {
        "gauge_id": best.station.id,
        "gauge_name": best.station.name,
        "gauge_km": round(best_km, 3),
    }


def fetch_cameras(states=None, probe: bool = True) -> tuple[dict | None, SourceHealth]:
    """The camera layer, with liveness measured rather than believed.

    `states` are this run's gauge readings, used only to name each camera's nearest
    gauge. Passing none is fine: the layer still publishes, just unpaired.
    """
    health = SourceHealth(source="yala_cctv", ok=False, optional=True)
    try:
        rows = fetch_json(CAMERA_LIST_URL, timeout=30)
    except Exception as exc:  # noqa: BLE001 - upstream down must not stop the build
        health.error = f"camera list unavailable: {exc}"
        return None, health

    if not isinstance(rows, list):
        health.error = f"camera list was {type(rows).__name__}, expected a list"
        return None, health

    features: list[dict] = []
    dropped = {"no_id": 0, "no_location": 0, "outside_yala": 0, "no_stream_key": 0}
    live = dark = unknown = 0

    for row in rows:
        if not isinstance(row, dict):
            dropped["no_id"] += 1
            continue
        cam_id = ident(row.get("id"))
        if not cam_id:
            dropped["no_id"] += 1
            continue
        lat, lon = num(row.get("lat")), num(row.get("lng"))
        if lat is None or lon is None:
            dropped["no_location"] += 1
            continue
        s, w, n, e = YALA_BOUNDS
        if not (s <= lat <= n and w <= lon <= e):
            dropped["outside_yala"] += 1
            continue
        customer, device = ident(row.get("customerCode")), ident(row.get("deviceCode"))
        if not customer or not device:
            # Without both codes the browser cannot mint a session, so the camera is
            # undrawable rather than merely dark.
            dropped["no_stream_key"] += 1
            continue

        key = stream_key(customer, device)
        ok = probe_stream(key) if probe else None
        if ok is True:
            live += 1
        elif ok is False:
            dark += 1
        else:
            unknown += 1

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "id": f"yala:{cam_id}",
                "name": text(row.get("name")) or cam_id,
                "zone": text(row.get("zone")),
                "customer_code": customer,
                "device_code": device,
                "stream_key": key,
                # Measured, not reported. See the module docstring on signal_status.
                "stream_ok": ok,
                "stream_checked_at": (datetime.now(timezone.utc).isoformat()
                                      if probe else None),
                **_pair_with_gauge(lat, lon, states),
            },
        })

    for reason, count in dropped.items():
        if count:
            health.warnings.append(f"{count} cameras dropped: {reason}")

    if not features:
        health.error = "no usable cameras in the list"
        return None, health

    # The source's own field, recorded once as a warning rather than published per
    # camera, because publishing it would invite the UI to use it.
    claimed_up = sum(1 for r in rows if isinstance(r, dict) and r.get("signal_status") == 1)
    if probe and claimed_up != live:
        health.warnings.append(
            f"signal_status claims {claimed_up} cameras up; minting a session found "
            f"{live} live, {dark} dark, {unknown} unknown. signal_status is not used.")

    health.ok = True
    health.stations = len(features)
    paired = sum(1 for f in features if f["properties"]["gauge_id"])
    health.warnings.append(
        f"{paired} of {len(features)} cameras are within {MAX_GAUGE_PAIR_KM:g} km of a "
        f"gauge and are offered as a visual check on it")

    return ({
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "Yala City Municipality (เทศบาลนครยะลา) via yala-cctv.localth.ai",
        "licence": "unknown",
        "stream_url_template": STREAM_URL_TEMPLATE,
        "note": (
            "Live municipal street cameras, shown beside this project's gauge readings "
            "rather than merged with them: a camera carries no water level. Video is "
            "fetched by the browser from the operator on an explicit click and is "
            "never copied, recorded or re-hosted here. No licence or terms of use "
            "could be found for this feed, and no owner is named on it."
        ),
        "features": features,
    }, health)
