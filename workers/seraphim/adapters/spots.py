"""Fishing spots — where calm mode is computed.

Precomputed server-side rather than calculated in the browser, so the solunar and
scoring rules live in exactly one implementation. Users pick the nearest spot; the
whole plan is already in the static snapshot.

Coastal spots reuse the 23 verified tide points, so the sea profile gets real tidal
rates. Inland spots are major Thai reservoirs and river reaches.

NOTE on reservoirs: ThaiWater's `storage_percent` is NOT reservoir storage — verified
2026-09-15 across 794 stations, it is the fraction of CHANNEL depth filled, bed to bank
(115% means 15% above bank). So reservoir drawdown, which genuinely changes where fish
hold, is NOT modelled here. It needs a real dam dataset from RID or EGAT.
"""

from __future__ import annotations

from seraphim.adapters.base import fetch_json, num
from seraphim.adapters.marine import POINTS as TIDE_POINTS
from seraphim.models import SourceHealth

#: (id, Thai name, English name, lat, lon, profile)
INLAND: list[tuple[str, str, str, float, float, str]] = [
    ("bhumibol",    "เขื่อนภูมิพล (ตาก)",           "Bhumibol Reservoir",   17.242,  98.973, "reservoir"),
    ("sirikit",     "เขื่อนสิริกิติ์ (อุตรดิตถ์)",   "Sirikit Reservoir",    17.762, 100.559, "reservoir"),
    ("srinagarind", "เขื่อนศรีนครินทร์ (กาญจนบุรี)", "Srinagarind Reservoir",14.400,  99.130, "reservoir"),
    ("vajiralongkorn","เขื่อนวชิราลงกรณ (กาญจนบุรี)","Vajiralongkorn Res.", 14.797,  98.594, "reservoir"),
    ("pasak",       "เขื่อนป่าสักชลสิทธิ์ (ลพบุรี)", "Pasak Jolasid Res.",   15.053, 101.061, "reservoir"),
    ("ubolratana",  "เขื่อนอุบลรัตน์ (ขอนแก่น)",     "Ubolratana Reservoir", 16.773, 102.618, "reservoir"),
    ("lampao",      "เขื่อนลำปาว (กาฬสินธุ์)",       "Lam Pao Reservoir",    16.630, 103.440, "reservoir"),
    ("sirindhorn",  "เขื่อนสิรินธร (อุบลราชธานี)",   "Sirindhorn Reservoir", 15.200, 105.430, "reservoir"),
    ("kaengkrachan","เขื่อนแก่งกระจาน (เพชรบุรี)",   "Kaeng Krachan Res.",   12.903,  99.617, "reservoir"),
    ("ratchaprapha","เขื่อนรัชชประภา (สุราษฎร์ธานี)","Ratchaprapha Res.",     8.970,  98.800, "reservoir"),
    ("lamtakhong",  "เขื่อนลำตะคอง (นครราชสีมา)",    "Lam Takhong Reservoir",14.880, 101.570, "reservoir"),
    ("boraphet",    "บึงบอระเพ็ด (นครสวรรค์)",       "Bueng Boraphet",       15.684, 100.250, "reservoir"),
    ("kwanphayao",  "กว๊านพะเยา (พะเยา)",            "Kwan Phayao",          19.163,  99.880, "reservoir"),
    ("nonghan",     "หนองหาร (สกลนคร)",              "Nong Han",             17.180, 104.120, "reservoir"),
    ("mekong_ck",   "แม่น้ำโขง เชียงคาน (เลย)",      "Mekong at Chiang Khan",17.890, 101.660, "river"),
    ("nan_pl",      "แม่น้ำน่าน พิษณุโลก",           "Nan at Phitsanulok",   16.820, 100.260, "river"),
    ("chaophraya_ay","แม่น้ำเจ้าพระยา อยุธยา",       "Chao Phraya, Ayutthaya",14.350,100.570, "river"),
]

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
#: Pressure and wind are the two weather signals the bite score uses.
WEATHER_PARAMS = "hourly=pressure_msl,wind_speed_10m&forecast_days=4&timezone=UTC"


def all_spots() -> list[dict]:
    """Coastal tide points plus inland waters, in one list."""
    spots = [
        {"id": pid, "name_th": th, "name": en, "lat": lat, "lon": lon,
         "profile": "sea", "tide_point": pid}
        for pid, th, en, lat, lon in TIDE_POINTS
    ]
    spots += [
        {"id": sid, "name_th": th, "name": en, "lat": lat, "lon": lon,
         "profile": profile, "tide_point": None}
        for sid, th, en, lat, lon, profile in INLAND
    ]
    return spots


def fetch_weather(spots: list[dict]) -> tuple[dict[str, dict], SourceHealth]:
    """Hourly pressure and wind for every spot, in one batched request."""
    health = SourceHealth(source="openmeteo_spot_weather", ok=False)
    lats = ",".join(f"{s['lat']:.4f}" for s in spots)
    lons = ",".join(f"{s['lon']:.4f}" for s in spots)
    try:
        payload = fetch_json(f"{WEATHER_URL}?latitude={lats}&longitude={lons}&{WEATHER_PARAMS}",
                             timeout=90)
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc)
        return {}, health

    results = payload if isinstance(payload, list) else [payload]
    if len(results) != len(spots):
        health.error = (f"expected {len(spots)} results, got {len(results)} — refusing to "
                        "pair weather with the wrong water")
        return {}, health

    out: dict[str, dict] = {}
    for spot, result in zip(spots, results):
        hourly = result.get("hourly") or {}
        times = hourly.get("time") or []
        pressure = [num(v) for v in (hourly.get("pressure_msl") or [])]
        wind = [num(v) for v in (hourly.get("wind_speed_10m") or [])]
        if not times:
            continue
        out[spot["id"]] = {"time": times, "pressure_msl": pressure, "wind_kmh": wind}

    health.ok = bool(out)
    health.stations = len(out)
    if not out:
        health.error = "no spot weather returned"
    return out, health
