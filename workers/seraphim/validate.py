"""Backtesting: does the prediction actually predict?

The project tells people a river reaches its bank in about N hours. Nothing until now
measured whether that was true. This replays the archive, makes predictions using only
data that existed at the time, and compares them to what happened next.

The single most important number here is **skill against persistence**. Persistence is
the naive forecast "the level stays exactly where it is now". It is free, it requires no
trend, and it is surprisingly hard to beat over short horizons. If the linear
extrapolation cannot beat it, then time-to-bank is not just imprecise, it is worse than
doing nothing, and it should be withdrawn rather than dressed in a confidence label.

Nothing here is tuned to make the engine look good. The cut points are every reading
that has enough history, not a chosen subset.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from seraphim.adapters import registry
from seraphim.forecast import displacement, hours_to_level
from seraphim.history import fit_trend

#: Horizons to score. Beyond half a day a linear river forecast is not worth defending.
LEAD_HOURS = (1.0, 2.0, 3.0, 6.0, 12.0)
#: The observed series is interpolated to the target time rather than snapped to the
#: nearest reading. Snapping looked fine until the numbers were checked: 738 of the
#: observation gaps are exactly 180 minutes, so origin+3h always landed on a reading
#: while origin+2h always landed between two and scored nothing. Each lead was then
#: measured on a different population of stations, which made the leads incomparable
#: and the whole comparison worthless.
#: Interpolation is only trusted when the two bracketing readings are close enough
#: together that a straight line between them means something.
MAX_BRACKET_HOURS = 3.5
#: A river that barely moves is trivially easy to "predict". Skill is therefore also
#: reported for the subset that actually did something over the lead time.
MOVING_THRESHOLD_M = 0.05
#: Minimum history before a prediction is allowed, matching the live engine.
MIN_HISTORY_POINTS = 4


@dataclass(slots=True)
class Scored:
    lead: float
    error: float          # |predicted - actual|, metres
    linear_error: float   # what straight-line extrapolation would have scored
    baseline_error: float # |last known level - actual|, metres (persistence)
    confidence: str
    rising: bool
    moved: float          # |actual - level at origin|: how much the river actually did
    station: str


@dataclass(slots=True)
class BankCall:
    """A prediction that the water would reach bank level within the horizon."""

    predicted_hours: float
    actual_hours: float | None   # None when it never got there in the observed window
    observable_hours: float      # how far ahead we can actually see, for honesty


def load_timelines(archive_root: Path) -> tuple[dict[str, list], dict[str, float]]:
    """Per-station [(observed_at, level)] and its bank level, from every archived file."""
    levels: dict[str, dict[datetime, float]] = {}
    banks: dict[str, float] = {}
    for path in sorted(archive_root.rglob("*.json.gz")):
        try:
            payload = json.loads(gzip.open(path, "rb").read())
        except Exception:  # noqa: BLE001
            continue
        for feature in payload.get("features", []):
            p = feature.get("properties") or {}
            sid, level, observed = p.get("id"), p.get("level_msl"), p.get("observed_at")
            if not sid or level is None or not observed:
                continue
            try:
                ts = datetime.fromisoformat(observed)
            except ValueError:
                continue
            levels.setdefault(sid, {})[ts] = float(level)
            if p.get("bank_msl") is not None:
                banks[sid] = float(p["bank_msl"])
    return {k: sorted(v.items()) for k, v in levels.items()}, banks


def country_of(station_id: str) -> str | None:
    """Country for a namespaced station id, e.g. "nwsriv:AAIT2" -> "US".

    The id prefix is the adapter id and every adapter declares one country, so this
    needs no extra field in the archive and works on files written before per-country
    reporting existed.
    """
    source = station_id.split(":", 1)[0] if ":" in station_id else station_id
    adapter = registry.get(source)
    country = getattr(adapter, "country", None)
    return country if country and country != "*" else None


def _actual_at(series: list, target: datetime) -> float | None:
    """Observed level at `target`, linearly interpolated between bracketing readings.

    Returns None when the target falls outside the series, or when the two readings
    around it are too far apart for a straight line between them to be credible.
    """
    if not series or target < series[0][0] or target > series[-1][0]:
        return None
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        if t0 <= target <= t1:
            span = (t1 - t0).total_seconds() / 3600
            if span > MAX_BRACKET_HOURS:
                return None
            if span <= 0:
                return v0
            f = (target - t0).total_seconds() / 3600 / span
            return v0 + (v1 - v0) * f
    return None


def backtest(archive_root: Path) -> dict:
    timelines, banks = load_timelines(archive_root)
    scored: list[Scored] = []
    calls: list[BankCall] = []

    for sid, series in timelines.items():
        if len(series) < MIN_HISTORY_POINTS + 1:
            continue
        last_observed = series[-1][0]

        # Every reading with enough history behind it becomes an origin. No cherry-picking.
        for i in range(MIN_HISTORY_POINTS, len(series)):
            origin_time, origin_level = series[i]
            history = series[max(0, i - 12):i + 1]
            trend = fit_trend(history)
            rate = trend.rate_m_per_hr
            if rate is None:
                continue

            for lead in LEAD_HOURS:
                target = origin_time + timedelta(hours=lead)
                if target > last_observed:
                    break  # cannot score beyond what we have seen
                actual = _actual_at(series, target)
                if actual is None:
                    continue
                # The model we actually ship. `linear_error` keeps the straight line
                # it replaced in view, so the change stays measured rather than assumed.
                predicted = origin_level + displacement(rate, lead)
                linear = origin_level + rate * lead
                scored.append(Scored(
                    lead=lead,
                    error=abs(predicted - actual),
                    linear_error=abs(linear - actual),
                    baseline_error=abs(origin_level - actual),
                    confidence=trend.confidence,
                    rising=rate > 0,
                    moved=abs(actual - origin_level),
                    station=sid,
                ))

            # Did a "reaches bank within N hours" call come true?
            bank = banks.get(sid)
            freeboard = None if bank is None else bank - origin_level
            if (freeboard is not None and freeboard > 0 and rate >= 0.01
                    and trend.confidence in ("good", "fair")):
                hours = hours_to_level(0.0, freeboard, trend)
                if hours is not None and hours <= max(LEAD_HOURS):
                    observable = (last_observed - origin_time).total_seconds() / 3600
                    crossed = next((
                        (ts - origin_time).total_seconds() / 3600
                        for ts, v in series if ts > origin_time and v >= bank), None)
                    calls.append(BankCall(round(hours, 2), crossed, round(observable, 2)))

    return _summarise(scored, calls, timelines)


def _summarise(scored: list[Scored], calls: list[BankCall], timelines: dict) -> dict:
    def stats(rows):
        if not rows:
            return None
        errs = sorted(s.error for s in rows)
        base = sorted(s.baseline_error for s in rows)
        med, med_base = median(errs), median(base)
        skill = None if med_base <= 0 else round((med_base - med) / med_base, 3)
        return {
            "n": len(rows),
            "stations": len({s.station for s in rows}),
            "median_error_m": round(med, 3),
            "p90_error_m": round(errs[int(len(errs) * 0.9)], 3),
            "median_persistence_error_m": round(med_base, 3),
            "skill_vs_persistence": skill,
            "beats_persistence": bool(skill is not None and skill > 0),
            # The straight line this model replaced, kept in the published output so
            # the change stays a measurement rather than a claim in a commit message.
            "median_linear_error_m": round(median(sorted(s.linear_error for s in rows)), 3),
        }

    by_lead = []
    for lead in LEAD_HOURS:
        rows = [s for s in scored if s.lead == lead]
        if not rows:
            continue
        entry = {"lead_hours": lead, **stats(rows)}
        # The honest test: rivers that actually moved. Predicting a flat river correctly
        # is not skill, and flat rivers dominate the raw median.
        moving = stats([s for s in rows if s.moved >= MOVING_THRESHOLD_M])
        entry["moving_only"] = moving
        by_lead.append(entry)

    # --- per country ------------------------------------------------------
    # The headline skill number blended four countries. A river in Iowa reports on a
    # different interval, against a different datum, with a different threshold
    # convention than one in Ayutthaya, and publishing one accuracy figure earned
    # mostly on Thai rivers as if it applied to all of them overstates what is known
    # about the others. Country comes from the station id prefix, which is the adapter
    # id, which maps one-to-one onto a country.
    by_country = []
    for code in sorted({country_of(s.station) for s in scored} - {None}):
        rows = [s for s in scored if country_of(s.station) == code and s.lead <= 3.0]
        entry = stats(rows)
        if entry is None:
            continue
        entry["country"] = code
        entry["moving_only"] = stats([s for s in rows if s.moved >= MOVING_THRESHOLD_M])
        by_country.append(entry)

    by_conf = []
    for conf in ("good", "fair", "poor", "steady"):
        rows = [s for s in scored if s.confidence == conf and s.lead <= 3.0]
        if rows:
            errs = sorted(s.error for s in rows)
            by_conf.append({
                "confidence": conf,
                "n": len(rows),
                "median_error_m": round(median(errs), 3),
                "p90_error_m": round(errs[int(len(errs) * 0.9)], 3),
            })

    resolved = [c for c in calls if c.actual_hours is not None
                or c.observable_hours >= c.predicted_hours]
    correct = [c for c in resolved if c.actual_hours is not None]
    timing = [abs(c.actual_hours - c.predicted_hours) for c in correct]

    return {
        "predictions_scored": len(scored),
        "stations_with_timeline": len(timelines),
        "by_lead": by_lead,
        "by_country": by_country,
        "by_confidence": by_conf,
        "bank_calls": {
            "made": len(calls),
            "resolvable": len(resolved),
            "came_true": len(correct),
            "hit_rate": round(len(correct) / len(resolved), 3) if resolved else None,
            "median_timing_error_hr": round(median(timing), 2) if timing else None,
        },
        "method": (
            "Every archived reading with at least 4 prior readings becomes an origin. A "
            "trend is fitted using only data available at that moment, a level is "
            "predicted at each lead time, and compared with the observed series "
            "interpolated to that exact time. Persistence is the naive forecast that the "
            "level does not change; skill is the fraction of its error the forecast "
            "removes, so a negative value means the forecast is worse than standing "
            "still. 'moving_only' repeats the measurement on cases where the river "
            "actually changed by at least 5 cm, because predicting a flat river "
            "correctly is not skill. No cut points are chosen by hand."
        ),
        "caveat": (
            "Accuracy measured against our own archive, which is short and sampled at "
            "the cadence we happen to poll. It is not a hydrological validation and no "
            "hydrologist has reviewed it."
        ),
    }


def format_report(result: dict) -> str:
    out = [f"scored {result['predictions_scored']:,} predictions across "
           f"{result['stations_with_timeline']} stations", ""]
    out.append(f"  {'lead':>5}{'n':>7}{'stns':>6}{'median':>9}{'linear':>9}{'persist':>9}{'skill':>7}"
               f"   | {'moving n':>9}{'median':>9}{'skill':>7}")
    for r in result["by_lead"]:
        sk = "n/a" if r["skill_vs_persistence"] is None else f"{r['skill_vs_persistence']:+.0%}"
        m = r.get("moving_only")
        msk = "n/a" if not m or m["skill_vs_persistence"] is None else f"{m['skill_vs_persistence']:+.0%}"
        mn = f"{m['n']:,}" if m else "-"
        mmed = f"{m['median_error_m']:.3f}m" if m else "-"
        out.append(f"  {r['lead_hours']:>4.0f}h{r['n']:>7,}{r['stations']:>6}"
                   f"{r['median_error_m']:>8.3f}m{r.get('median_linear_error_m', 0):>8.3f}m"
                   f"{r['median_persistence_error_m']:>8.3f}m{sk:>7}"
                   f"   | {mn:>9}{mmed:>9}{msk:>7}")
    if result.get("by_country"):
        out += ["", "  by country (lead <= 3h):"]
        for c in result["by_country"]:
            sk = "n/a" if c["skill_vs_persistence"] is None else f"{c['skill_vs_persistence']:+.0%}"
            m = c.get("moving_only")
            msk = ("n/a" if not m or m["skill_vs_persistence"] is None
                   else f"{m['skill_vs_persistence']:+.0%}")
            out.append(f"    {c['country']:<4} n={c['n']:>6,}  median {c['median_error_m']:.3f} m"
                       f"  skill {sk:>5}  |  moving n={m['n'] if m else 0:>5} skill {msk:>5}")

    if result["by_confidence"]:
        out += ["", "  confidence tiers (lead <= 3h):"]
        for c in result["by_confidence"]:
            out.append(f"    {c['confidence']:<6} n={c['n']:>6,}  median {c['median_error_m']:.3f} m"
                       f"  p90 {c['p90_error_m']:.3f} m")
    b = result["bank_calls"]
    out += ["", f"  bank calls: {b['made']} made, {b['resolvable']} resolvable, "
                f"{b['came_true']} came true"
                + (f", hit rate {b['hit_rate']:.0%}" if b["hit_rate"] is not None else "")]
    if b["median_timing_error_hr"] is not None:
        out.append(f"    median timing error {b['median_timing_error_hr']} h")
    return "\n".join(out)
