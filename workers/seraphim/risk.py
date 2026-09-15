"""The risk engine, turning readings into a decision, with its reasoning attached.

Most water dashboards show a level and a colour. This module exists to answer the
question people actually have: *will my area flood, when, and why.*

Two commitments shape everything here:

1. **Every score carries its reasons.** A bare colour badge is either ignored or
   panics people. The output always includes the specific numbers that produced it,
   in Thai and English, so a reader can judge it for themselves.
2. **Uncertainty is published, not hidden.** Time-to-bank is linear extrapolation, not
   hydrology. It is withheld entirely when the trend is weak, capped when absurd, and
   always labelled with its confidence.

Rules are explicit and inspectable rather than a fitted model: when this tells someone
to move their family, they are entitled to see the arithmetic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from seraphim.history import Trend
from seraphim.models import StationState

# --- thresholds, all named so they can be argued with -----------------------

#: Below this rate, a "rise" is sensor jitter; dividing by it yields fantasy forecasts.
MIN_RATE_M_PER_HR = 0.01
#: Beyond this horizon a linear extrapolation of river level means nothing.
MAX_TTB_HOURS = 72.0

TTB_SEVERE_HOURS = 6.0
TTB_WARNING_HOURS = 24.0

FREEBOARD_SEVERE_M = 0.30
FREEBOARD_WARNING_M = 0.50
FREEBOARD_WATCH_M = 1.50

RAIN_HEAVY_MM = 50.0
RAIN_EXTREME_MM = 90.0
DISCHARGE_RISE_STRONG = 3.0

#: Tidal influence reaches far up the Chao Phraya, so distance alone is too crude;
#: pairing it with a low bank elevation approximates the tidal reach. A terrain-based
#: test replaces this in Phase 5.
TIDAL_REACH_KM = 100.0
TIDAL_BANK_ELEV_M = 5.0
#: How close to high water counts as "drainage is blocked".
TIDE_COINCIDENCE_HOURS = 3.0

#: Ceiling for any level reached by forecast rather than observation.
MAX_FORECAST_LEVEL = 4
#: Ceiling for a station whose network publishes no overtopping threshold. Levels 4 and
#: 5 claim the river is at or over its bank, and that claim cannot be made from a
#: typical operating range.
NO_BANK_MAX_LEVEL = 3

LEVEL_NAMES = {
    1: ("ปกติ", "Normal"),
    2: ("เฝ้าระวัง", "Watch"),
    3: ("เตือนภัย", "Warning"),
    4: ("อันตราย", "Severe"),
    5: ("วิกฤต", "Critical"),
}


@dataclass(frozen=True, slots=True)
class Reason:
    """One contributing factor, stated with its number so it can be checked."""

    code: str
    th: str
    en: str


@dataclass(frozen=True, slots=True)
class Risk:
    station_id: str
    level: int
    time_to_bank_hr: float | None
    rate_m_per_hr: float | None
    trend_confidence: str
    confidence: str
    reasons: list[Reason] = field(default_factory=list)

    @property
    def level_th(self) -> str:
        return LEVEL_NAMES[self.level][0]

    @property
    def level_en(self) -> str:
        return LEVEL_NAMES[self.level][1]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_tide_point(lat: float, lon: float, tide_points: list[dict]) -> tuple[dict | None, float]:
    best, best_d = None, float("inf")
    for p in tide_points:
        d = haversine_km(lat, lon, p["lat"], p["lon"])
        if d < best_d:
            best, best_d = p, d
    return best, best_d


def time_to_bank(freeboard_m: float | None, trend: Trend | None) -> float | None:
    """Hours until the water reaches bank level at the current rate.

    The headline number, and the one most able to mislead, so it is withheld unless
    the trend is actually trustworthy.
    """
    if freeboard_m is None or freeboard_m <= 0 or trend is None:
        return None
    if trend.confidence not in ("good", "fair"):
        return None
    rate = trend.rate_m_per_hr
    if rate is None or rate < MIN_RATE_M_PER_HR:
        return None
    hours = freeboard_m / rate
    return round(hours, 1) if hours <= MAX_TTB_HOURS else None


def _tide_reason(
    state: StationState, tide_points: list[dict], now: datetime
) -> Reason | None:
    """High water blocking drainage.

    This is the Bangkok mechanism: the delta does not flood only because of rainfall,
    but because discharge arrives when the tide has shut the door on the outflow.
    """
    st = state.station
    if st.bank_msl is None or st.bank_msl > TIDAL_BANK_ELEV_M or not tide_points:
        return None
    point, distance = nearest_tide_point(st.lat, st.lon, tide_points)
    if point is None or distance > TIDAL_REACH_KM:
        return None

    for extreme in point.get("next", []):
        if extreme.get("kind") != "high":
            continue
        try:
            at = datetime.fromisoformat(extreme["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if abs((at - now).total_seconds()) <= TIDE_COINCIDENCE_HOURS * 3600:
            local = at + timedelta(hours=7)
            return Reason(
                code="tide_block",
                th=f"น้ำทะเลหนุนสูง {local:%H:%M} น. ({point['name_th']}) ระบายน้ำได้ช้าลง",
                en=(
                    f"High tide {local:%H:%M} ICT at {point['name']} "
                    f"({distance:.0f} km away) will slow drainage"
                ),
            )
    return None


def assess(
    state: StationState,
    trend: Trend | None,
    tide_points: list[dict],
    now: datetime,
) -> Risk:
    """Score one station and explain the score."""
    reasons: list[Reason] = []
    fb = state.freeboard_m
    fc = state.forecast
    rate = trend.rate_m_per_hr if trend else None
    rising = bool(trend and trend.rising and rate and rate >= MIN_RATE_M_PER_HR)
    ttb = time_to_bank(fb, trend)

    level = 1

    # --- the water itself ---------------------------------------------------
    if fb is not None and fb <= 0:
        level = 5
        reasons.append(
            Reason(
                "over_bank",
                f"น้ำล้นตลิ่งแล้ว {abs(fb):.2f} ม.",
                f"Water is {abs(fb):.2f} m above bank level",
            )
        )
    else:
        if ttb is not None and ttb <= TTB_SEVERE_HOURS:
            level = max(level, 4)
        elif ttb is not None and ttb <= TTB_WARNING_HOURS:
            level = max(level, 3)
        if fb is not None:
            if fb <= FREEBOARD_SEVERE_M and rising:
                level = max(level, 4)
            elif fb <= FREEBOARD_WARNING_M:
                level = max(level, 3)
            elif fb <= FREEBOARD_WATCH_M:
                level = max(level, 2)

    # --- networks that publish a typical range and no bank level ---
    # The UK is the first of these. Exceeding a normal operating range is real
    # information, but it is not overtopping, so it is capped well below the levels
    # that mean "the river is out".
    if fb is None:
        above = state.above_typical_m
        if above is not None:
            if above > 0:
                level = max(level, 3 if rising else 2)
                reasons.append(Reason(
                    "above_typical",
                    f"สูงกว่าช่วงปกติของสถานีนี้ {above:.2f} ม. (ไม่ใช่ล้นตลิ่ง)",
                    f"{above:.2f} m above this station's typical range "
                    f"(not a bank level, which this network does not publish)"))
            else:
                reasons.append(Reason(
                    "within_typical",
                    f"อยู่ในช่วงปกติ (ต่ำกว่าระดับสูงสุดปกติ {abs(above):.2f} ม.)",
                    f"within its typical range, {abs(above):.2f} m below the usual high"))
        elif state.station.typical_high is None and state.station.bank_msl is None:
            reasons.append(Reason(
                "no_threshold",
                "ไม่มีค่าระดับอ้างอิงสำหรับสถานีนี้",
                "no threshold published for this station, so only the trend is shown"))

    if fb is not None and fb > 0:
        reasons.append(
            Reason(
                "freeboard",
                f"ต่ำกว่าตลิ่ง {fb:.2f} ม.",
                f"{fb:.2f} m below bank level",
            )
        )

    if rising and rate is not None:
        reasons.append(
            Reason(
                "rising",
                f"น้ำขึ้น {rate * 100:.0f} ซม./ชม. (ช่วง {trend.span_hours:.1f} ชม.)",
                f"Rising {rate * 100:.0f} cm/hr over the last {trend.span_hours:.1f} h",
            )
        )
    if ttb is not None:
        reasons.append(
            Reason(
                "time_to_bank",
                f"คาดถึงระดับตลิ่งใน ~{ttb:.1f} ชม. (ประมาณการ)",
                f"~{ttb:.1f} h to bank level at this rate (estimate)",
            )
        )

    # --- what is coming -----------------------------------------------------
    if fc and fc.rain_next_24h_mm is not None:
        rain = fc.rain_next_24h_mm
        if rain >= RAIN_EXTREME_MM:
            level = max(level, 3 if (fb or 99) <= FREEBOARD_WATCH_M else 2)
            reasons.append(
                Reason("rain_extreme", f"ฝนคาดการณ์ {rain:.0f} มม. ใน 24 ชม.",
                       f"{rain:.0f} mm rain forecast in next 24 h")
            )
        elif rain >= RAIN_HEAVY_MM:
            if rising or (fb is not None and fb <= FREEBOARD_WATCH_M):
                level = max(level, 2)
            reasons.append(
                Reason("rain_heavy", f"ฝนคาดการณ์ {rain:.0f} มม. ใน 24 ชม.",
                       f"{rain:.0f} mm rain forecast in next 24 h")
            )

    if fc and fc.discharge_rise_ratio is not None:
        ratio = fc.discharge_rise_ratio
        if ratio >= DISCHARGE_RISE_STRONG:
            level = max(level, 2)
            reasons.append(
                Reason("discharge_rise",
                       f"แบบจำลองคาดน้ำท่าเพิ่มเป็น {ratio:.1f} เท่าใน 7 วัน",
                       f"Model expects discharge to rise {ratio:.1f}x within 7 days")
            )

    # --- tide compounding ---------------------------------------------------
    tide_reason = _tide_reason(state, tide_points, now)
    if tide_reason is not None:
        reasons.append(tide_reason)
        # Only compounds an already-elevated situation; a high tide alone floods nothing.
        if level >= 3 or (fb is not None and fb <= FREEBOARD_WATCH_M):
            # Capped below 5 deliberately. Level 5 means "water is over the bank now" -
            # an observed fact. A forecast, however well founded, must not wear the same
            # badge as a river that is already out, or a responder scanning the map
            # cannot tell what is happening from what might. Only `over_bank` reaches 5.
            #
            # Written as a guarded increment, not min(): a naive min() would DEMOTE an
            # already-overtopped station from 5 to 4, which is how a cap intended to
            # prevent overstatement ends up understating a river that is already out.
            if level < MAX_FORECAST_LEVEL:
                level += 1

    # --- honesty about the data ---------------------------------------------
    trend_conf = trend.confidence if trend else "none"
    confidence = trend_conf
    if state.is_stale:
        # A gauge that fell silent during a flood is a signal, not a gap: the score
        # stands on the last known reading, but nobody should read it as current.
        confidence = "poor"
        reasons.append(
            Reason("stale",
                   f"ไม่ได้รับข้อมูลมา {state.data_age_minutes / 60:.1f} ชม.",
                   f"No reading for {state.data_age_minutes / 60:.1f} h, score uses last known value")
        )
    elif trend_conf == "none":
        reasons.append(
            Reason("no_trend", "ยังไม่มีข้อมูลย้อนหลังพอจะคำนวณแนวโน้ม",
                   "Not enough history yet to compute a trend")
        )

    # A station with no overtopping threshold cannot be said to have overtopped.
    if fb is None:
        level = min(level, NO_BANK_MAX_LEVEL)

    return Risk(
        station_id=state.station.id,
        level=level,
        time_to_bank_hr=ttb,
        rate_m_per_hr=rate,
        trend_confidence=trend_conf,
        confidence=confidence,
        reasons=reasons,
    )


def rollup(states: list[StationState], risks: dict[str, Risk]) -> list[dict]:
    """Aggregate to district level, the unit people actually live in.

    Takes the worst station in each district rather than an average: one overtopping
    river is not cancelled out by three calm ones nearby.
    """
    buckets: dict[tuple, dict] = {}
    for s in states:
        admin = s.station.admin
        if not admin or not admin.province:
            continue
        risk = risks.get(s.station.id)
        if risk is None:
            continue
        # Country is part of the key: a Thai amphoe and a British catchment are not the
        # same kind of place and must never collapse into one row.
        key = (admin.country,
               admin.province_code or admin.province,
               admin.district_code or admin.district)
        b = buckets.setdefault(
            key,
            {
                "country": admin.country,
                "province": admin.province,
                "province_code": admin.province_code,
                "district": admin.district,
                "district_code": admin.district_code,
                "level": 1,
                "stations": 0,
                "over_bank": 0,
                "worst_station": None,
                "worst_ttb_hr": None,
                "reasons": [],
            },
        )
        b["stations"] += 1
        if (s.freeboard_m or 1) <= 0:
            b["over_bank"] += 1
        if risk.level > b["level"]:
            b["level"] = risk.level
            b["worst_station"] = s.station.name
            b["worst_ttb_hr"] = risk.time_to_bank_hr
            b["reasons"] = [{"code": r.code, "th": r.th, "en": r.en} for r in risk.reasons]
        elif risk.level == b["level"] and risk.time_to_bank_hr is not None:
            cur = b["worst_ttb_hr"]
            if cur is None or risk.time_to_bank_hr < cur:
                b["worst_station"] = s.station.name
                b["worst_ttb_hr"] = risk.time_to_bank_hr
                b["reasons"] = [{"code": r.code, "th": r.th, "en": r.en} for r in risk.reasons]

    out = list(buckets.values())
    out.sort(key=lambda b: (-b["level"], b["worst_ttb_hr"] if b["worst_ttb_hr"] is not None else 1e9))
    return out
