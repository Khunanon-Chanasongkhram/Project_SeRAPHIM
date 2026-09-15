"""Open-Meteo Marine — tide prediction at curated coastal points.

Why curated rather than per-station: the marine model only resolves sea cells, so an
inland gauge returns an empty series (verified at Ayutthaya, 2026-09-15). All 23 points
below were individually probed and returned usable curves.

They are chosen for two jobs at once — the river mouths that drive backwater flooding
in the Chao Phraya delta, and the fishing coasts of both seas.

Licence: CC-BY 4.0, non-commercial. Attribution carried into every snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone

from seraphim.adapters.base import fetch_json, num
from seraphim.models import SourceHealth, TideSeries
from seraphim.tide import daily_ranges, find_extremes, range_regime

URL = "https://marine-api.open-meteo.com/v1/marine"

#: (id, Thai name, English name, lat, lon) — every entry verified against the live API.
#: Ranges observed on 2026-09-15 show the real regional physics: the upper Gulf river
#: mouths swing ~2.3 m (which is why Bangkok floods on a high tide), the southern Gulf
#: only ~0.5 m, and the Andaman coast 1.7-2.6 m.
POINTS: list[tuple[str, str, str, float, float]] = [
    ("chaophraya_mouth", "ปากแม่น้ำเจ้าพระยา (สมุทรปราการ)", "Chao Phraya Mouth", 13.48, 100.59),
    ("thachin_mouth", "ปากแม่น้ำท่าจีน (สมุทรสาคร)", "Tha Chin Mouth", 13.45, 100.27),
    ("maeklong_mouth", "ปากแม่น้ำแม่กลอง (สมุทรสงคราม)", "Mae Klong Mouth", 13.35, 100.00),
    ("bangpakong_mouth", "ปากแม่น้ำบางปะกง (ฉะเชิงเทรา)", "Bang Pakong Mouth", 13.50, 100.87),
    ("sichang", "เกาะสีชัง (ชลบุรี)", "Ko Sichang", 13.16, 100.80),
    ("pattaya", "พัทยา (ชลบุรี)", "Pattaya", 12.90, 100.85),
    ("rayong", "ระยอง", "Rayong", 12.62, 101.28),
    ("chanthaburi", "จันทบุรี", "Chanthaburi", 12.45, 102.10),
    ("trat", "ตราด", "Trat", 12.10, 102.60),
    ("huahin", "หัวหิน (ประจวบฯ)", "Hua Hin", 12.53, 99.95),
    ("prachuap", "ประจวบคีรีขันธ์", "Prachuap Khiri Khan", 11.80, 99.83),
    ("chumphon", "ชุมพร", "Chumphon", 10.48, 99.25),
    ("bandon", "อ่าวบ้านดอน (สุราษฎร์ธานี)", "Ban Don Bay", 9.35, 99.35),
    ("nakhonsi", "นครศรีธรรมราช", "Nakhon Si Thammarat", 8.45, 100.15),
    ("songkhla", "สงขลา", "Songkhla", 7.15, 100.65),
    ("pattani", "ปัตตานี", "Pattani", 6.95, 101.30),
    ("narathiwat", "นราธิวาส", "Narathiwat", 6.45, 101.85),
    ("ranong", "ระนอง (อันดามัน)", "Ranong", 9.90, 98.50),
    ("phangnga", "พังงา (อันดามัน)", "Phang Nga", 8.45, 98.25),
    ("phuket", "ภูเก็ต (อันดามัน)", "Phuket", 7.88, 98.32),
    ("krabi", "กระบี่ (อันดามัน)", "Krabi", 8.00, 98.85),
    ("trang", "ตรัง (อันดามัน)", "Trang", 7.35, 99.50),
    ("satun", "สตูล (อันดามัน)", "Satun", 6.70, 99.85),
]

#: Past days are requested as well as future: the range classifier needs a local
#: distribution to rank today against, and 14 days spans a full spring-neap cycle.
PAST_DAYS = 14
FORECAST_DAYS = 7


class TideAdapter:
    id = "openmeteo_marine"
    attribution = "Open-Meteo Marine (CC-BY 4.0)"
    #: Tide is a harmonic prediction — a 7-day curve is as valid tomorrow as today.
    refresh_hours = 24.0

    def fetch(self) -> tuple[list[TideSeries], SourceHealth]:
        health = SourceHealth(source=self.id, ok=False)
        fetched_at = datetime.now(timezone.utc)

        lats = ",".join(str(p[3]) for p in POINTS)
        lons = ",".join(str(p[4]) for p in POINTS)
        url = (
            f"{URL}?latitude={lats}&longitude={lons}&hourly=sea_level_height_msl"
            f"&past_days={PAST_DAYS}&forecast_days={FORECAST_DAYS}&timezone=UTC"
        )
        try:
            payload = fetch_json(url, timeout=90)
        except Exception as exc:  # noqa: BLE001
            health.error = str(exc)
            return [], health

        results = payload if isinstance(payload, list) else [payload]
        if len(results) != len(POINTS):
            health.error = (
                f"expected {len(POINTS)} results, got {len(results)} — refusing to "
                "guess which curve belongs to which coast"
            )
            return [], health

        series: list[TideSeries] = []
        empty = 0
        for (pid, name_th, name_en, lat, lon), result in zip(POINTS, results):
            hourly = result.get("hourly") or {}
            raw_times = hourly.get("time") or []
            raw_heights = [num(v) for v in (hourly.get("sea_level_height_msl") or [])]
            times: list[datetime] = []
            for t in raw_times:
                try:
                    times.append(datetime.fromisoformat(t).replace(tzinfo=timezone.utc))
                except ValueError:
                    times.append(None)  # type: ignore[arg-type]

            pairs = [(t, h) for t, h in zip(times, raw_heights) if t is not None]
            if not pairs or all(h is None for _, h in pairs):
                empty += 1
                continue
            ts = [t for t, _ in pairs]
            hs = [h for _, h in pairs]

            series.append(
                TideSeries(
                    point_id=pid,
                    name=name_en,
                    name_th=name_th,
                    lat=lat,
                    lon=lon,
                    fetched_at=fetched_at,
                    times=ts,
                    heights_m=[h for h in hs if h is not None],
                    extremes=find_extremes(ts, hs),
                )
            )

        if empty:
            health.warnings.append(f"{empty} tide points returned no sea-level data")
        health.ok = bool(series)
        health.stations = len(series)
        health.fetched_at = fetched_at
        if not series:
            health.error = "no tide series returned"
        return series, health


def summarise(s: TideSeries, now: datetime) -> dict:
    """Compact per-point summary for the snapshot.

    The full hourly curve is kept (it is small and the fishing planner needs its shape),
    but timestamps are not repeated per sample — only the start time and a step.
    """
    from seraphim.tide import tide_state

    ranges = daily_ranges(s.times, s.heights_m)
    today = now.date().isoformat()
    today_range = ranges.get(today)
    regime, pct = (
        range_regime(today_range, list(ranges.values()))
        if today_range is not None
        else ("unknown", -1)
    )
    direction, rate = tide_state(s.times, s.heights_m, now)

    return {
        "id": s.point_id,
        "name": s.name,
        "name_th": s.name_th,
        "lat": s.lat,
        "lon": s.lon,
        "state": direction,
        "rate_m_per_hr": rate,
        "range_today_m": today_range,
        "range_regime": regime,
        "range_percentile": pct,
        "next": [
            {"kind": e.kind, "at": e.at.isoformat(), "height_m": e.height_m}
            for e in s.next_extremes(now, limit=6)
        ],
        "curve_start": s.times[0].isoformat() if s.times else None,
        "curve_step_minutes": 60,
        "curve_m": [round(h, 2) for h in s.heights_m],
    }
