"""Street cameras: the one layer here that is a picture rather than a number.

Two operators, one published layer. Every other cross-check in this project is one
model against another; a camera pointed at the bridge a gauge is measuring is the only
thing that lets a reader look at the water and check the number against it. That is the
whole reason this exists, and it is why each camera carries the id of its nearest gauge
and refuses to carry one that is too far away to be about the same water.

**Yala** (`yala-cctv.localth.ai`) — 5 cameras, live HLS video via AWS Kinesis Video
Streams. Two sit 170 m and 210 m from the gauge บ้านท่าสาบ, on สะพานท่าสาป.
**Hat Yai** (`hatyaicityclimate.org`) — 35 entries, JPEG snapshots, of which ~17 are
live street cameras. Eleven are within 1 km of a ThaiWater gauge and several are within
20-30 m, sitting on the floodgates (ปตร.) those gauges measure. Hat Yai floods badly and
often, so this is the more valuable of the two.

**Why this is NOT a SourceAdapter.** A camera yields no water level, no observation and
no datum — it yields a picture. Same reasoning as `googlefloods.py`: forcing it into
`Observation` would mean inventing a level, and an invented level is the worst thing
this codebase can produce. It publishes as its own layer, beside the gauges, never
merged into them.

**Two operators, two shapes, one honest difference.** Yala is `kind: "video"` and Hat Yai
is `kind: "photo"`. They are not flattened into a pretend-common shape, because what a
reader can do with them genuinely differs: one plays, one is a still with a timestamp.

⚠️ **Three sources now, three lying status fields.** ThaiWater's `situation_level` is
null on 302 of 306 overtopped stations. Yala's `signal_status` is 1 on all five cameras
including one with no stream at all. Hat Yai's `enable` is 1 on all 35 entries including
one whose last image is **1,018 days old**. None of the three is read. Liveness is
measured: minted for Yala, read from the image's own timestamp for Hat Yai.

⚠️ **Hat Yai timestamps are Thai local time, and the HTTP headers lie.** `atDate` is
naive wall-clock at UTC+7, so it goes through `parse_local_naive` exactly as ThaiWater
does; reading it as UTC would report every image as 7 hours older than it is. Worse, the
JPEGs are served with `last-modified` set to the moment you asked, so the header says
"now" for a picture that may be six months old. The header is ignored entirely.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from seraphim.adapters.base import (
    USER_AGENT,
    fetch_json,
    ident,
    num,
    parse_local_naive,
    text,
)
from seraphim.models import SourceHealth

# --------------------------------------------------------------------- Yala

#: The vendor's camera list. Static JSON, ~1.5 KB, CORS-open, five records.
YALA_LIST_URL = "https://yala-cctv.localth.ai/cameraData.json"

#: Mints an HLS session URL for one camera. `{customerCode}-{deviceCode}`.
#: CORS-open on GET, which is what lets the browser call it directly.
STREAM_URL_TEMPLATE = (
    "https://gg89f6g289.execute-api.ap-southeast-1.amazonaws.com/stream/hls-url/{key}"
)

#: Yala province, generously bounded. A camera outside this is a data error, not a
#: camera: the whole value of the layer is that it sits on known flood infrastructure.
YALA_BOUNDS = (5.6, 100.6, 7.0, 101.9)  # south, west, north, east

# ------------------------------------------------------------------- Hat Yai

HATYAI_LIST_URL = "https://hatyaicityclimate.org/api/flood/cams"

#: Songkhla province, generously bounded - but NOT as far east as 101.3, which was the
#: first guess and which reaches past Yala at 101.26. The two operators' guards must not
#: overlap, or a coordinate error in one feed would land a camera inside the other's
#: bounds and pass a check that exists precisely to catch it.
HATYAI_BOUNDS = (6.2, 100.0, 7.9, 101.0)

#: `atDate` is naive wall-clock in Thai local time. See the module docstring.
HATYAI_TZ_OFFSET_HOURS = 7.0

#: Entries that are not cameras. The feed mixes in a weather radar, a satellite image
#: and a synoptic chart, which are pictures of the sky rather than of a place. Drawing
#: a radar composite at a point on the map would say "this is what it looks like here",
#: which is false, and this project already has its own radar layer. Matched on the
#: title because that is what actually describes them: `sathingphra` has no code at all.
NON_CAMERA_TITLE_TOKENS = ("เรดาร์", "ดาวเทียม", "แผนที่อากาศ")

#: Past this, a camera is not a live camera and is dropped rather than drawn as stale.
#: The same threshold and the same reasoning as `rws.MAX_READING_AGE_HOURS`: the Dutch
#: feed returns the latest value of every series it ever held, including one from 1740.
#: Hat Yai is the same shape — entries sit in the list for years after the camera died,
#: and one is 1,018 days old. A six-month-old photograph of a canal, drawn on a flood
#: map beside cameras that are live, is read as "this is the canal now".
MAX_PHOTO_AGE_HOURS = 24.0

# -------------------------------------------------------------------- shared

#: Beyond this, a camera is not looking at that gauge's water and must not be offered as
#: a check on it. Refusing to pair is deliberate: a camera offered as evidence about a
#: gauge 5 km upstream is worse than no camera at all.
MAX_GAUGE_PAIR_KM = 1.0

OPERATORS = {
    "yala": {
        "name": "Yala City Municipality (เทศบาลนครยะลา)",
        "attribution": "Yala City Municipality (เทศบาลนครยะลา) via yala-cctv.localth.ai",
        "licence": "unknown",
        "licence_url": None,
        # Stated in words, not left as a bare "unknown", because the absence is the
        # finding: probed 2026-09-18, there is no robots.txt and no terms page (every
        # path returns the same single-page app), the localth.ai apex does not resolve,
        # and the page names no owner. That is weaker footing than ThaiWater, whose
        # terms are also unconfirmed but which at least has HII to ask.
        "licence_note": ("No licence or terms of use could be found for this feed, "
                         "and no owner is named on it."),
        "kind": "video",
        "stream_url_template": STREAM_URL_TEMPLATE,
    },
    "hatyai": {
        "name": "Hatyai City Climate (SCCCRN)",
        "attribution": ("Hatyai City Climate / มูลนิธิเครือข่ายเมืองภาคใต้เพื่อรับมือ"
                        "การเปลี่ยนแปลงสภาพภูมิอากาศ (SCCCRN)"),
        "licence": "CC BY-SA 3.0",
        "licence_url": "http://creativecommons.org/licenses/by-sa/3.0/",
        # A real, named licence, which is why this operator is on much firmer ground
        # than Yala. Attribution is mandatory and the images are supplied to SCCCRN by
        # third parties, so the per-camera sponsor credit travels with each camera
        # rather than collapsing into one site-level line.
        "licence_note": ("Creative Commons Attribution-ShareAlike 3.0. Attribution is "
                         "required; each camera carries its own image credit."),
        "kind": "photo",
    },
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _in_bounds(lat: float, lon: float, bounds: tuple) -> bool:
    s, w, n, e = bounds
    return s <= lat <= n and w <= lon <= e


def _pair_with_gauge(lat: float, lon: float, states) -> dict:
    """Nearest gauge within `MAX_GAUGE_PAIR_KM`, or an explicit absence."""
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


def fetch_yala(states=None, probe: bool = True) -> tuple[list[dict], SourceHealth]:
    """Yala's five HLS cameras, with liveness measured rather than believed."""
    health = SourceHealth(source="yala_cctv", ok=False, optional=True)
    try:
        rows = fetch_json(YALA_LIST_URL, timeout=30)
    except Exception as exc:  # noqa: BLE001 - upstream down must not stop the build
        health.error = f"camera list unavailable: {exc}"
        return [], health
    if not isinstance(rows, list):
        health.error = f"camera list was {type(rows).__name__}, expected a list"
        return [], health

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
        if not _in_bounds(lat, lon, YALA_BOUNDS):
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
        live += ok is True
        dark += ok is False
        unknown += ok is None

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "id": f"yala:{cam_id}",
                "operator": "yala",
                "kind": "video",
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
        return [], health

    claimed_up = sum(1 for r in rows if isinstance(r, dict) and r.get("signal_status") == 1)
    if probe and claimed_up != live:
        health.warnings.append(
            f"signal_status claims {claimed_up} cameras up; minting a session found "
            f"{live} live, {dark} dark, {unknown} unknown. signal_status is not used.")
    health.ok = True
    health.stations = len(features)
    return features, health


def fetch_hatyai(states=None, now: datetime | None = None) -> tuple[list[dict], SourceHealth]:
    """Hat Yai's flood cameras, aged from the image's own timestamp.

    The feed is a mixture: live street cameras, long-dead ones still listed, and three
    pictures of the sky. Only the first group survives.
    """
    health = SourceHealth(source="hatyai_cctv", ok=False, optional=True)
    now = now or datetime.now(timezone.utc)
    try:
        payload = fetch_json(HATYAI_LIST_URL, timeout=30)
    except Exception as exc:  # noqa: BLE001
        health.error = f"camera list unavailable: {exc}"
        return [], health

    rows = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        health.error = "camera list did not contain an items array"
        return [], health

    features: list[dict] = []
    dropped = {"not_a_camera": 0, "no_location": 0, "outside_songkhla": 0,
               "no_photo": 0, "no_timestamp": 0, "stale": 0}
    oldest_kept = None
    cutoff = now - timedelta(hours=MAX_PHOTO_AGE_HOURS)

    for row in rows:
        if not isinstance(row, dict):
            dropped["not_a_camera"] += 1
            continue
        name = ident(row.get("name"))
        title = text(row.get("title")) or ""
        if not name:
            dropped["not_a_camera"] += 1
            continue
        if any(tok in title for tok in NON_CAMERA_TITLE_TOKENS):
            dropped["not_a_camera"] += 1
            continue

        loc = row.get("location") or {}
        lat = num(loc.get("latitude")) if isinstance(loc, dict) else None
        lon = num(loc.get("longitude")) if isinstance(loc, dict) else None
        if lat is None or lon is None:
            dropped["no_location"] += 1
            continue
        if not _in_bounds(lat, lon, HATYAI_BOUNDS):
            dropped["outside_songkhla"] += 1
            continue

        photo = text(row.get("photo"))
        if not photo or not photo.startswith("https://"):
            dropped["no_photo"] += 1
            continue

        # Thai wall-clock, not UTC. Reading it as UTC would age every image by 7 hours.
        observed = parse_local_naive(row.get("atDate"), HATYAI_TZ_OFFSET_HOURS)
        if observed is None:
            dropped["no_timestamp"] += 1
            continue
        if observed < cutoff:
            dropped["stale"] += 1
            continue
        if oldest_kept is None or observed < oldest_kept:
            oldest_kept = observed

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            "properties": {
                "id": f"hatyai:{name}",
                "operator": "hatyai",
                "kind": "photo",
                "name": title or name,
                "zone": text(row.get("code")),
                "photo_url": photo,
                "observed_at": observed.isoformat(),
                "age_minutes": round((now - observed).total_seconds() / 60, 1),
                # The images are supplied to Hatyai City Climate by a third party, and
                # the credit travels with the picture rather than sitting in a footer.
                "sponsor_name": text(row.get("sponsorName")),
                "sponsor_text": text(row.get("sponsorText")),
                "sponsor_url": text(row.get("sponsorUrl")),
                **_pair_with_gauge(lat, lon, states),
            },
        })

    for reason, count in dropped.items():
        if count:
            health.warnings.append(f"{count} entries dropped: {reason}")
    if not features:
        health.error = "no live cameras in the list"
        return [], health

    enabled = sum(1 for r in rows if isinstance(r, dict) and r.get("enable") == 1)
    if enabled != len(features):
        health.warnings.append(
            f"`enable` is 1 on {enabled} of {len(rows)} entries, including cameras dark "
            f"for months; {len(features)} are actually fresher than "
            f"{MAX_PHOTO_AGE_HOURS:g} h. `enable` is not used.")
    health.ok = True
    health.stations = len(features)
    return features, health


def fetch_cameras(states=None, probe: bool = True,
                  now: datetime | None = None) -> tuple[dict | None, list[SourceHealth]]:
    """Both operators, merged into one layer.

    `states` are this run's gauge readings, used only to name each camera's nearest
    gauge. Passing none is fine: the layer still publishes, just unpaired. One operator
    failing must never take the other off the map.
    """
    now = now or datetime.now(timezone.utc)
    features, health = [], []

    for got, h in (fetch_yala(states, probe=probe), fetch_hatyai(states, now=now)):
        features.extend(got)
        health.append(h)

    if not features:
        return None, health

    paired = sum(1 for f in features if f["properties"]["gauge_id"])
    for h in health:
        if h.ok:
            h.warnings.append(
                f"{MAX_GAUGE_PAIR_KM:g} km pairing: {paired} of {len(features)} cameras "
                f"across all operators are offered as a visual check on a gauge")

    return ({
        "type": "FeatureCollection",
        "generated_at": now.isoformat(),
        "operators": OPERATORS,
        "stream_url_template": STREAM_URL_TEMPLATE,
        "note": (
            "Live street cameras, shown beside this project's gauge readings rather "
            "than merged with them: a camera carries no water level. Images and video "
            "are fetched by the browser from each operator on an explicit click, and "
            "are never copied, recorded or re-hosted here."
        ),
        "features": features,
    }, health)
