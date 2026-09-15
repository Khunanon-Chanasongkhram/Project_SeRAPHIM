"""Tide analysis: turning an hourly sea-level series into decisions.

Two consumers, one engine:
  * flood, a high tide blocks river drainage, so peak coastal risk is a discharge peak
    landing on a spring high water
  * fishing, fish feed on *moving* water, so the steep part of the curve matters more
    than the peak (Phase 3)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from seraphim.models import TideExtreme

#: Mean synodic month, new moon to new moon.
SYNODIC_MONTH_DAYS = 29.530588853
#: A well-determined new moon, used as the phase epoch.
NEW_MOON_EPOCH = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)


def moon_phase(when: datetime) -> float:
    """Fraction through the lunar cycle: 0.0 = new moon, 0.5 = full moon."""
    days = (when - NEW_MOON_EPOCH).total_seconds() / 86400.0
    return (days % SYNODIC_MONTH_DAYS) / SYNODIC_MONTH_DAYS


def moon_illumination(when: datetime) -> float:
    """Fraction of the disc lit, 0.0-1.0."""
    return round((1.0 - math.cos(2.0 * math.pi * moon_phase(when))) / 2.0, 3)


def spring_neap(when: datetime) -> str:
    """Classify the lunar phase regime.

    ⚠️ This describes the MOON, not the tide range. Spring tides coincide with new and
    full moon in a semi-diurnal regime, but the upper Gulf of Thailand is mixed and
    mainly diurnal, where range is modulated chiefly by lunar *declination* rather than
    phase. Measured at Chao Phraya mouth over 25 days (2026-09-15): mean daily range was
    2.17 m on "spring" days versus 1.99 m on "neap" days, a 9% difference, and two days
    within 4 days of the same new moon ranged 2.48 m and 1.81 m.

    So: use this for solunar fishing periods, where lunar phase genuinely matters. Do
    NOT use it to predict tidal range, use `range_regime`, which measures the series
    itself and is therefore correct in any tidal regime, anywhere in the world.
    """
    p = moon_phase(when)
    # Distance to the nearest syzygy (0.0 or 0.5), expressed in cycle fractions.
    d = min(p, abs(p - 0.5), 1.0 - p)
    if d < 0.12:
        return "spring"
    if d > 0.20:
        return "neap"
    return "transitional"


def find_extremes(
    times: list[datetime], heights: list[float | None]
) -> list[TideExtreme]:
    """Locate high and low waters in an hourly series.

    Hourly sampling alone would round every high water to the nearest hour, which is
    too coarse to plan around, so each turning point is refined by fitting a parabola
    through its three neighbouring samples. That recovers sub-hourly timing from
    hourly data, which is what makes "high tide 18:40" possible rather than "18:00".
    """
    pts = [(t, h) for t, h in zip(times, heights) if h is not None]
    if len(pts) < 3:
        return []

    out: list[TideExtreme] = []
    for i in range(1, len(pts) - 1):
        (_, y0), (t1, y1), (_, y2) = pts[i - 1], pts[i], pts[i + 1]
        is_high = y1 >= y0 and y1 >= y2 and (y1 > y0 or y1 > y2)
        is_low = y1 <= y0 and y1 <= y2 and (y1 < y0 or y1 < y2)
        if not (is_high or is_low):
            continue

        denom = y0 - 2.0 * y1 + y2
        if abs(denom) < 1e-9:
            offset, peak = 0.0, y1
        else:
            offset = 0.5 * (y0 - y2) / denom
            # Guard against a degenerate fit throwing the turning point into the
            # neighbouring hour, which would be worse than not refining at all.
            offset = max(-0.5, min(0.5, offset))
            peak = y1 - 0.25 * (y0 - y2) * offset

        step = (pts[i + 1][0] - pts[i - 1][0]) / 2
        out.append(
            TideExtreme(
                at=t1 + step * offset,
                height_m=round(peak, 3),
                kind="high" if is_high else "low",
            )
        )
    return out


def tide_state(
    times: list[datetime], heights: list[float | None], when: datetime
) -> tuple[str, float | None]:
    """Current direction and rate of change, in metres per hour.

    Rate matters more than height for fishing: slack water at the turn is when fish
    stop feeding, and the fastest flow is roughly midway between high and low.
    """
    pts = [(t, h) for t, h in zip(times, heights) if h is not None]
    for i in range(len(pts) - 1):
        t0, y0 = pts[i]
        t1, y1 = pts[i + 1]
        if t0 <= when < t1:
            hours = (t1 - t0).total_seconds() / 3600.0
            if hours <= 0:
                return "unknown", None
            rate = (y1 - y0) / hours
            if abs(rate) < 0.02:
                return "slack", round(rate, 3)
            return ("rising" if rate > 0 else "falling"), round(rate, 3)
    return "unknown", None


def daily_ranges(
    times: list[datetime], heights: list[float | None]
) -> dict[str, float]:
    """Tidal range for each day present in the series, keyed by ISO date."""
    buckets: dict[str, list[float]] = {}
    for t, h in zip(times, heights):
        if h is not None:
            buckets.setdefault(t.date().isoformat(), []).append(h)
    # A partial day at either end of the window would report a falsely small range.
    return {
        day: round(max(v) - min(v), 3) for day, v in buckets.items() if len(v) >= 20
    }


def range_regime(day_range: float, all_ranges: list[float]) -> tuple[str, int]:
    """Rank one day's tidal range against this location's own recent distribution.

    Empirical rather than astronomical, which is the point: it needs no assumption
    about whether a coast is semi-diurnal, diurnal or mixed, so it is equally valid in
    the Gulf of Thailand, the Andaman Sea, or anywhere the project expands to.

    Returns a label and the percentile (0-100).
    """
    usable = [r for r in all_ranges if r is not None]
    if len(usable) < 5:
        return "unknown", -1
    below = sum(1 for r in usable if r < day_range)
    pct = int(round(100.0 * below / len(usable)))
    if pct >= 70:
        return "large", pct
    if pct <= 30:
        return "small", pct
    return "average", pct


def coincidence_window(
    extremes: list[TideExtreme], when: datetime, hours: float = 3.0
) -> bool:
    """Is `when` within `hours` of a high water?

    This is the coastal-flood compounding test: river discharge arriving during a high
    tide has nowhere to drain. Bangkok's worst flooding is often this, not rainfall alone.
    """
    return any(
        e.kind == "high" and abs((e.at - when).total_seconds()) <= hours * 3600
        for e in extremes
    )
