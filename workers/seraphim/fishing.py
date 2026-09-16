"""Calm mode, solunar windows and bite scoring.

Strategically this is not a side feature. A flood app opened twice in a lifetime is an
app nobody has installed when it matters. The tide and weather data already fetched for
flood work answers a question people have on the other 350 days, so the same install is
already present, already trusted and already granted location permission on the day the
river comes up. **Retention is a safety feature.**

The scoring is deliberately transparent and rule-based. Fishing advice is folklore-rich
and evidence-poor, so rather than implying more certainty than exists, every score shows
the factors that produced it and the user can disagree with any of them.

What the rules encode:
  * fish feed on MOVING water, so the rate of tidal change matters far more than the
    height, peak flow near mid-tide beats slack water at the top
  * low light concentrates feeding: dawn and dusk outrank midday
  * solunar theory (Knight, 1926) puts major periods at lunar transit and antitransit,
    minor periods at moonrise and moonset
  * falling barometric pressure ahead of a front is the one weather signal with broad
    angler consensus
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from seraphim.astro import civil_twilight, moon_events, sun_events
from seraphim.tide import moon_illumination, spring_neap

TH_UTC_OFFSET = 7.0

#: Solunar window half-widths. Majors are the stronger and longer of the two.
MAJOR_HALF_WIDTH_MIN = 60
MINOR_HALF_WIDTH_MIN = 35
#: Low-light window around sunrise/sunset.
LIGHT_HALF_WIDTH_MIN = 55

# Weights, summing to 100 before profile adjustment. Exposed so they can be argued with.
W_TIDE = 30
W_SOLUNAR = 25
W_LIGHT = 25
W_MOON = 10
W_WEATHER = 10

#: Tidal rate (m/hr) treated as "full marks" for water movement. Above this, more
#: movement stops helping, and in strong flow it starts to hinder.
STRONG_TIDE_RATE = 0.35
#: Wind that makes small-boat fishing unpleasant or unsafe.
WIND_UNPLEASANT_KMH = 25.0
WIND_ROUGH_KMH = 40.0
#: A pressure fall of this much over 6 h is the classic pre-frontal feeding trigger.
PRESSURE_FALL_HPA = 2.0


@dataclass(frozen=True, slots=True)
class Factor:
    code: str
    points: int
    th: str
    en: str


@dataclass(frozen=True, slots=True)
class SolunarWindow:
    start: datetime
    end: datetime
    kind: str    # "major" | "minor"
    source: str  # moon_transit | moon_antitransit | moonrise | moonset

    def contains(self, when: datetime) -> bool:
        return self.start <= when <= self.end


@dataclass(frozen=True, slots=True)
class BiteHour:
    at: datetime
    score: int
    factors: list[Factor] = field(default_factory=list)


#: Profiles reweight the same factors for different water.
PROFILES = {
    "sea":       {"tide": 1.0, "solunar": 1.0, "light": 1.0, "moon": 1.0, "weather": 1.0},
    "river":     {"tide": 0.7, "solunar": 1.0, "light": 1.1, "moon": 0.9, "weather": 1.0},
    "reservoir": {"tide": 0.0, "solunar": 1.3, "light": 1.4, "moon": 1.1, "weather": 1.2},
}

#: Applied to ANY water without tide data, whatever its profile says. Without this a
#: non-tidal river silently forfeits the tide factor's 21 points and ranks below every
#: coastal spot for a reason that has nothing to do with fishing, the scale would be
#: measuring "is it near the sea", not "is it worth going".
TIDELESS = {"tide": 0.0, "solunar": 1.3, "light": 1.4, "moon": 1.1, "weather": 1.2}


def effective_weights(profile: str, has_tide: bool) -> dict:
    """Weights actually used, after accounting for whether tide data exists here."""
    base = PROFILES.get(profile, PROFILES["sea"])
    if has_tide and base["tide"] > 0:
        return base
    return TIDELESS


def solunar_windows(lat: float, lon: float, day: date,
                    tz_offset: float = TH_UTC_OFFSET) -> list[SolunarWindow]:
    """Major and minor periods for one local day.

    Majors sit at lunar transit (moon overhead) and antitransit (moon underfoot);
    minors at moonrise and moonset. Antitransit counts even though the moon is below
    the horizon, the claim is about lunar position, not visibility.
    """
    ev = moon_events(lat, lon, day, tz_offset)
    out: list[SolunarWindow] = []
    for when, kind, source, half in (
        (ev.transit, "major", "moon_transit", MAJOR_HALF_WIDTH_MIN),
        (ev.antitransit, "major", "moon_antitransit", MAJOR_HALF_WIDTH_MIN),
        (ev.rise, "minor", "moonrise", MINOR_HALF_WIDTH_MIN),
        (ev.set, "minor", "moonset", MINOR_HALF_WIDTH_MIN),
    ):
        if when is not None:
            out.append(SolunarWindow(when - timedelta(minutes=half),
                                     when + timedelta(minutes=half), kind, source))
    out.sort(key=lambda w: w.start)
    return out


def _taper(when: datetime, centre: datetime, half_width_min: float) -> float:
    """1.0 at the centre of a window, falling linearly to 0 at its edge."""
    off = abs((when - centre).total_seconds()) / 60.0
    return max(0.0, 1.0 - off / half_width_min) if half_width_min else 0.0


def tide_movement_score(rate_m_per_hr: float | None) -> float:
    """0-1 from the RATE of tidal change, not the height.

    Slack water at high tide is the worst moment of the cycle and the one a
    height-based score would rate best; peak flow around mid-tide is the best.
    """
    if rate_m_per_hr is None:
        return 0.0
    return min(1.0, abs(rate_m_per_hr) / STRONG_TIDE_RATE)


def score_hour(
    when: datetime,
    lat: float,
    lon: float,
    profile: str = "sea",
    tide_rate: float | None = None,
    windows: list[SolunarWindow] | None = None,
    sun=None,
    twilight=None,
    wind_kmh: float | None = None,
    pressure_change_hpa: float | None = None,
) -> BiteHour:
    """Score one hour 0-100, with every contributing factor attached."""
    weights = effective_weights(profile, tide_rate is not None)
    factors: list[Factor] = []
    total = 0.0

    # --- moving water -----------------------------------------------------
    if weights["tide"] > 0:
        movement = tide_movement_score(tide_rate)
        pts = round(W_TIDE * weights["tide"] * movement)
        if pts:
            cm = abs(tide_rate or 0) * 100
            factors.append(Factor("tide_movement", pts,
                                  f"น้ำกำลังเดิน {cm:.0f} ซม./ชม.",
                                  f"Water moving {cm:.0f} cm/hr"))
        total += pts

    # --- solunar ----------------------------------------------------------
    best = 0.0
    best_window = None
    for w in windows or []:
        centre = w.start + (w.end - w.start) / 2
        half = (w.end - w.start).total_seconds() / 120.0
        strength = _taper(when, centre, half) * (1.0 if w.kind == "major" else 0.6)
        if strength > best:
            best, best_window = strength, w
    if best_window is not None and best > 0:
        pts = round(W_SOLUNAR * weights["solunar"] * best)
        if pts:
            label = "ช่วงหลัก" if best_window.kind == "major" else "ช่วงรอง"
            factors.append(Factor(f"solunar_{best_window.kind}", pts,
                                  f"{label} (ดวงจันทร์)",
                                  f"Solunar {best_window.kind} period"))
        total += pts

    # --- low light --------------------------------------------------------
    light = 0.0
    which = None
    for when_ev, name_th, name_en in (
        (getattr(sun, "rise", None), "รุ่งเช้า", "dawn"),
        (getattr(sun, "set", None), "พลบค่ำ", "dusk"),
    ):
        if when_ev:
            s = _taper(when, when_ev, LIGHT_HALF_WIDTH_MIN)
            if s > light:
                light, which = s, (name_th, name_en)
    if light > 0 and which:
        pts = round(W_LIGHT * weights["light"] * light)
        if pts:
            factors.append(Factor("low_light", pts, f"แสงน้อยช่วง{which[0]}",
                                  f"Low light around {which[1]}"))
        total += pts

    # --- moon phase -------------------------------------------------------
    illum = moon_illumination(when)
    # Both new and full moons amplify solunar activity; the quarters do not.
    phase_strength = abs(illum - 0.5) * 2.0
    pts = round(W_MOON * weights["moon"] * phase_strength)
    if pts:
        regime = spring_neap(when)
        factors.append(Factor("moon_phase", pts,
                              f"ดวงจันทร์ {illum * 100:.0f}% ({regime})",
                              f"Moon {illum * 100:.0f}% illuminated ({regime})"))
    total += pts

    # --- weather ----------------------------------------------------------
    if pressure_change_hpa is not None and pressure_change_hpa <= -PRESSURE_FALL_HPA:
        pts = round(W_WEATHER * weights["weather"])
        factors.append(Factor("pressure_falling", pts,
                              f"ความกดอากาศลด {abs(pressure_change_hpa):.1f} hPa",
                              f"Pressure falling {abs(pressure_change_hpa):.1f} hPa"))
        total += pts

    if wind_kmh is not None and wind_kmh >= WIND_UNPLEASANT_KMH:
        penalty = -20 if wind_kmh >= WIND_ROUGH_KMH else -8
        factors.append(Factor("wind", penalty,
                              f"ลมแรง {wind_kmh:.0f} กม./ชม."
                              + (", ทะเลมีคลื่น ระวังความปลอดภัย" if wind_kmh >= WIND_ROUGH_KMH else ""),
                              f"Wind {wind_kmh:.0f} km/h"
                              + (", rough water, take care" if wind_kmh >= WIND_ROUGH_KMH else "")))
        total += penalty

    factors.sort(key=lambda f: -f.points)
    return BiteHour(at=when, score=max(0, min(100, round(total))), factors=factors)


def clarity_advice(rain_24h_mm: float | None, discharge_ratio: float | None) -> dict | None:
    """Turbidity is tactics, not score.

    Muddy water does not mean "do not go", it means fish hunt by vibration and scent
    rather than sight. Scoring it down would be wrong; saying so is useful.
    """
    muddy = (rain_24h_mm is not None and rain_24h_mm >= 40) or \
            (discharge_ratio is not None and discharge_ratio >= 2.0)
    if muddy:
        return {
            "state": "turbid",
            "th": "น้ำขุ่นจากฝน/น้ำหลาก, ใช้เหยื่อมีกลิ่นแรงหรือสร้างแรงสั่นสะเทือน หาจุดน้ำนิ่งริมตลิ่ง",
            "en": "Turbid from rain or high flow, use scent or vibration, fish the slower edges",
        }
    if rain_24h_mm is not None and rain_24h_mm < 5:
        return {
            "state": "clear",
            "th": "น้ำใส, ใช้สายเบา เหยื่อธรรมชาติ เข้าใกล้อย่างระมัดระวัง",
            "en": "Clear water, lighter line, natural presentation, approach quietly",
        }
    return None


#: Decimal places per published condition. Integers unless a tenth carries meaning:
#: rain does (0.4 mm is drizzle, 4 mm is a soaking) and the 6-hour pressure change does,
#: because 2 hPa is the threshold the score itself uses.
_CONDITION_ROUNDING = {
    "wind_kmh": 0, "gust_kmh": 0, "wind_dir_deg": 0,
    "cloud_percent": 0, "temp_c": 0, "rain_mm": 1, "pressure_change_hpa": 1,
}


def _round_conditions(hour: dict) -> dict:
    out = {}
    for key, places in _CONDITION_ROUNDING.items():
        value = hour.get(key)
        if value is None:
            continue
        out[key] = int(round(value)) if places == 0 else round(value, places)
    return out


def day_plan(
    lat: float, lon: float, day: date, profile: str = "sea",
    tide_rates: dict[int, float] | None = None,
    weather: dict[int, dict] | None = None,
    tz_offset: float = TH_UTC_OFFSET,
) -> dict:
    """Hour-by-hour plan for one local day, plus the best windows."""
    windows = solunar_windows(lat, lon, day, tz_offset)
    sun = sun_events(lat, lon, day, tz_offset)
    twilight = civil_twilight(lat, lon, day, tz_offset)
    start = datetime(day.year, day.month, day.day) - timedelta(hours=tz_offset)
    start = start.replace(tzinfo=sun.transit.tzinfo if sun.transit else None)

    has_tide = bool(tide_rates)
    hours: list[BiteHour] = []
    conditions: list[dict] = []
    for h in range(24):
        when = start + timedelta(hours=h)
        w = (weather or {}).get(h, {})
        hours.append(score_hour(
            when, lat, lon, profile,
            tide_rate=(tide_rates or {}).get(h),
            windows=windows, sun=sun, twilight=twilight,
            wind_kmh=w.get("wind_kmh"), pressure_change_hpa=w.get("pressure_change_hpa"),
        ))
        # Rounded to what is actually readable. Nobody needs a wind bearing to a
        # tenth of a degree, and the raw floats cost ~100 KB gzipped across 40 spots
        # x 7 days x 24 hours, which is real mobile data for no information.
        conditions.append(_round_conditions(w))

    best = sorted(hours, key=lambda b: -b.score)[:3]
    return {
        "date": day.isoformat(),
        "profile": profile,
        "tide_modelled": has_tide,
        "sunrise": sun.rise.isoformat() if sun.rise else None,
        "sunset": sun.set.isoformat() if sun.set else None,
        "moon_illumination": moon_illumination(start + timedelta(hours=12)),
        "moon_regime": spring_neap(start + timedelta(hours=12)),
        "windows": [
            {"kind": w.kind, "source": w.source,
             "start": w.start.isoformat(), "end": w.end.isoformat()}
            for w in windows
        ],
        "hours": [
            {"at": b.at.isoformat(), "score": b.score,
             "conditions": conditions[i] or None,
             "factors": [{"code": f.code, "points": f.points, "th": f.th, "en": f.en}
                         for f in b.factors]}
            for i, b in enumerate(hours)
        ],
        "best": [{"at": b.at.isoformat(), "score": b.score} for b in best],
        "peak_score": max((b.score for b in hours), default=0),
    }


def tide_rates_for_day(summary: dict, day: date, tz_offset: float = TH_UTC_OFFSET
                       ) -> dict[int, float]:
    """Hourly rate of tidal change (m/hr) for one local day, from a published curve.

    The curve is stored as a start time plus hourly heights rather than repeated
    timestamps, so rates are differences between neighbouring samples.
    """
    curve = summary.get("curve_m") or []
    start_s = summary.get("curve_start")
    if not curve or not start_s:
        return {}
    start = datetime.fromisoformat(start_s)
    day_start = datetime(day.year, day.month, day.day, tzinfo=start.tzinfo) - \
        timedelta(hours=tz_offset)
    out: dict[int, float] = {}
    for h in range(24):
        idx = round((day_start + timedelta(hours=h) - start).total_seconds() / 3600)
        if 0 < idx < len(curve):
            out[h] = curve[idx] - curve[idx - 1]
    return out


def weather_for_day(series: dict, day: date, tz_offset: float = TH_UTC_OFFSET
                    ) -> dict[int, dict]:
    """Conditions per hour of one local day.

    Wind and the 6-hour pressure change feed the bite score. Gusts, wind direction,
    cloud, rain and air temperature ride along unscored, because they are what decides
    whether the trip is a good idea at all.
    """
    times = series.get("time") or []
    pressure = series.get("pressure_msl") or []
    wind = series.get("wind_kmh") or []
    #: Not scored, but shown. These are what a person actually checks before going out,
    #: and a planner that scores an hour 82 without saying it is blowing a gale with
    #: 40 mm of rain is not much of a planner.
    extra = {
        "gust_kmh": series.get("gust_kmh") or [],
        "wind_dir_deg": series.get("wind_dir_deg") or [],
        "cloud_percent": series.get("cloud_percent") or [],
        "rain_mm": series.get("rain_mm") or [],
        "temp_c": series.get("temp_c") or [],
    }
    index = {t: i for i, t in enumerate(times)}
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - \
        timedelta(hours=tz_offset)
    out: dict[int, dict] = {}
    for h in range(24):
        key = (day_start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00")
        i = index.get(key)
        if i is None:
            continue
        entry: dict = {}
        if i < len(wind) and wind[i] is not None:
            entry["wind_kmh"] = wind[i]
        for key, values in extra.items():
            if i < len(values) and values[i] is not None:
                entry[key] = values[i]
        # Falling pressure ahead of a front is the classic pre-frontal feeding trigger.
        if i >= 6 and i < len(pressure) and pressure[i] is not None and pressure[i - 6] is not None:
            entry["pressure_change_hpa"] = round(pressure[i] - pressure[i - 6], 2)
        if entry:
            out[h] = entry
    return out


#: How far the planner looks ahead.
#:
#: Was 3. The limit is the tide curve, and that turned out to be 9 days rather than the
#: 7 we were asking for (Open-Meteo marine fills 216 hours and no more, whatever you
#: request). Spot weather reaches 10 days. Seven is published: it covers next weekend,
#: which is the question people actually have, and stays a day inside both sources so
#: the last day is never half-empty.
DEFAULT_DAYS = 7


def build_fishing(spots, tide_by_id: dict[str, dict], weather_by_id: dict[str, dict],
                  today: date, days: int = DEFAULT_DAYS,
                  tz_offset: float = TH_UTC_OFFSET) -> list[dict]:
    """A multi-day plan for every spot."""
    out = []
    for spot in spots:
        summary = tide_by_id.get(spot.get("tide_point") or "")
        weather = weather_by_id.get(spot["id"], {})
        plans = []
        for k in range(days):
            day = today + timedelta(days=k)
            plans.append(day_plan(
                spot["lat"], spot["lon"], day, spot["profile"],
                tide_rates=tide_rates_for_day(summary, day, tz_offset) if summary else None,
                weather=weather_for_day(weather, day, tz_offset),
                tz_offset=tz_offset,
            ))
        out.append({**{k: v for k, v in spot.items() if k != "tide_point"},
                    "tide_point": spot.get("tide_point"),
                    "days": plans})
    return out
