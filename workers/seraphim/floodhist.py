"""Flood history: how unusual is today's water, measured against this place's own past.

**What this is.** For every forecast grid cell we already fetch a GloFAS forecast for,
this pulls the same cell's *historical* daily river discharge back to 2014 and reduces
it to a handful of numbers: where today sits in that record, what an ordinary high year
looks like here, how often high-flow episodes happen, and when the worst one was.

**What this is not, and the distinction matters.** GloFAS discharge is a *modelled* river
flow on a ~5 km grid. A cell exceeding its own 5-year flow level means "this river is
carrying more water than it usually does in a given year", which is a real and useful
statement about flood exposure. It is **not** an observation that anywhere flooded, it
carries no flood extent, and it says nothing about depth. Every string this module
produces says "high flow", never "flood", and the UI repeats the distinction. Calling a
modelled discharge percentile a flood would be the most confident wrong number this
project could publish, and the project has already made that mistake twice with terrain.

**Why the same cell matters more than the right cell.** Verified 2026-09-16: the archive
and the forecast endpoints resolve to the *same* grid cell and agree in magnitude (a
Bangkok cell reads 7,429 m³/s peak historically and 4,275 m³/s today; an off-channel cell
reads 5.7 and 1.1). So a percentile computed from a cell's own record and compared with
that same cell's own forecast is self-consistent even where the cell is not the main
channel. That self-consistency, not absolute accuracy, is what this feature rests on.

**Cost, which is why this is a separate command.** Open-Meteo weights a request by
locations x timesteps, not by request count. Measured 2026-09-16: 100 cells x 20 years is
refused outright ("requests too much data"); 50 cells x 12 years succeeds at 4.2 MB, but
two of them inside one minute trip the 600/minute limit. So this runs at roughly one
request a minute and cannot live inside the 15-minute build. It is budgeted and
incremental, exactly like `terrain`, and the result is cached for months because a
twelve-year climatology does not move.
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timezone

from seraphim.adapters.base import fetch_json, num
from seraphim.models import SourceHealth

URL = "https://flood-api.open-meteo.com/v1/flood"

#: Cache key holding the accumulated per-cell climatology.
CACHE_KEY = "flood_climatology"

#: Directory, relative to the data root, holding that cache.
#:
#: Deliberately NOT data/cache, which every other cached thing shares. In CI the two
#: are restored and saved by different workflows on different schedules: the 15-minute
#: build owns data/cache and data/archive, while the daily top-up owns this. Sharing a
#: directory would mean whichever workflow saved last overwrote the other's work, and
#: the thing most likely to be lost that way is the gauge archive, which is what every
#: trend and every time-to-bank is fitted to.
CACHE_DIR = "climate"

#: The build reads this cache with an effectively unlimited ceiling on purpose. Deciding
#: what is due for a refresh is the `floodhist` command's job (see REFRESH_DAYS); the
#: build must never drop a twelve-year climatology just because the top-up is overdue,
#: because the alternative is silently losing flood history from the map.
CACHE_HOURS = 24.0 * 365 * 10

#: How old a cell's climatology may get before `floodhist` re-fetches it. Twelve years
#: of record do not move meaningfully in six months, and each refetch costs a rate-
#: limited multi-megabyte request.
REFRESH_DAYS = 180

#: Years of history per cell. Long enough to fit a 2- and 5-year return level with
#: annual maxima, short enough that one request stays inside the volume cap.
CLIMATOLOGY_YEARS = 12

#: GloFAS v4 reanalysis does not extend indefinitely, and a start date before the record
#: just returns nulls. 2014 keeps us comfortably inside it.
EARLIEST_START = date(2014, 1, 1)

#: Cells per request. 50 x 12 years = 4.2 MB and passes; 100 is refused by the API.
BATCH = 50

#: Seconds between requests. One 50-cell request is ~600 weighted calls against a
#: 600/minute allowance, so two inside a minute fail. Measured, not guessed.
PACE_SECONDS = 65

#: Retries per batch, after waiting out the rate limit once. See the call site.
RETRIES = 1

#: Below this, GloFAS is not resolving a river at all: the cell is hillside or coast
#: that happens to contain a gauge. Publishing a percentile of near-zero noise would
#: dress up a rounding error as a flood signal.
MIN_RIVER_CMS = 1.0

#: Independent high-flow episodes must be separated by at least this long. Without it
#: one three-week monsoon peak counts as twenty floods.
EPISODE_GAP_DAYS = 7

#: Minimum annual maxima before a return level is fitted at all.
MIN_YEARS_FOR_RETURN = 5

#: Minimum before a 10-year level is offered. With fewer, a 10-year estimate is an
#: extrapolation past the end of the record wearing a statistic's clothes.
MIN_YEARS_FOR_10Y = 10

#: Version of the summary these statistics are computed to. Cells cached at an older
#: version are re-fetched by `floodhist` rather than read, because a statistic that was
#: never computed cannot be back-filled from the summary it is missing from.
#:
#: v2 added `high_days_per_year` and `growth_ratio` and rebuilt `prone_level` on them.
#: v1 classified flood-proneness by episodes per decade, which measured **nothing**: the
#: 2-year return level is by definition exceeded about once every two years, so episode
#: frequency is pinned near 5 per decade for every river on Earth. Measured across the
#: first 246 real Thai cells it ran 2.4 to 11.0 with a median of 5.5, i.e. scatter around
#: the value the arithmetic forces, and the flood-prone classes built on it were sorting
#: rivers by fitting error. Duration does vary: over 31 cells with full daily series,
#: days-a-year-above-the-2-year-level ran 0.7 to 11.3 (median 2.6), a 16x spread, and the
#: longest single episode in twelve years ran from 2 days to 161. That is the difference
#: between a stream that peaks for an afternoon and a delta that stays high for months,
#: and it is a real difference between places rather than an artefact of the threshold.
STATS_SCHEMA = 2


def cache_root(data_root):
    """Where the climatology cache lives under a data root."""
    return data_root / CACHE_DIR


def window(today: date | None = None) -> tuple[date, date]:
    """Inclusive [start, end] of the history window."""
    today = today or datetime.now(timezone.utc).date()
    # Yesterday: today's value is a forecast in this endpoint, not a reanalysis.
    end = date.fromordinal(today.toordinal() - 1)
    start = date(max(end.year - CLIMATOLOGY_YEARS, EARLIEST_START.year), 1, 1)
    if start < EARLIEST_START:
        start = EARLIEST_START
    return start, end


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated quantile of an already-sorted list."""
    if not sorted_values:
        raise ValueError("empty")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def gumbel_return_level(maxima: list[float], period_years: float) -> float | None:
    """Flow expected to be exceeded once every `period_years`, from annual maxima.

    Gumbel (EV1) fitted by method of moments, which is the standard first pass in flood
    frequency analysis and needs no external library. Returns None when the record is
    too short or degenerate (every year identical), because a return level from a
    zero-variance sample is a straight face on no information.
    """
    n = len(maxima)
    if n < MIN_YEARS_FOR_RETURN or period_years <= 1:
        return None
    mean = sum(maxima) / n
    var = sum((v - mean) ** 2 for v in maxima) / (n - 1)
    if var <= 0:
        return None
    sd = math.sqrt(var)
    alpha = sd * math.sqrt(6.0) / math.pi
    u = mean - 0.5772156649 * alpha
    level = u - alpha * math.log(-math.log(1.0 - 1.0 / period_years))
    return round(level, 2) if math.isfinite(level) else None


def _water_year_start_month(by_month: list[float]) -> int:
    """The driest month, used as the year boundary for annual maxima.

    A calendar year splits a monsoon that runs October to January, which is exactly how
    Thailand's worst floods behave, and splitting one flood into two annual maxima
    halves its apparent size. Picking the boundary from the cell's own seasonal minimum
    works in either hemisphere without a hardcoded regional rule.
    """
    usable = [(v, i) for i, v in enumerate(by_month) if v is not None]
    if not usable:
        return 1
    return min(usable)[1] + 1


def summarise(times: list[str], values: list) -> dict | None:
    """Reduce one cell's daily discharge record to its flood climatology.

    Returns None when the cell carries no resolvable river, so the caller can record
    that fact once rather than publishing a percentile of noise.
    """
    series: list[tuple[date, float]] = []
    for t, v in zip(times, values):
        n = num(v)
        if n is None or n < 0:
            continue
        try:
            d = date.fromisoformat(str(t)[:10])
        except ValueError:
            continue
        series.append((d, n))
    if len(series) < 365:
        return None
    series.sort()

    flows = sorted(v for _, v in series)
    peak = max(series, key=lambda x: x[1])
    if peak[1] < MIN_RIVER_CMS:
        return {"no_river": True}

    # Seasonal shape, and with it the water-year boundary.
    month_sum = [0.0] * 12
    month_n = [0] * 12
    for d, v in series:
        month_sum[d.month - 1] += v
        month_n[d.month - 1] += 1
    by_month = [round(month_sum[i] / month_n[i], 2) if month_n[i] else None
                for i in range(12)]
    wy_start = _water_year_start_month([v if v is not None else float("inf")
                                        for v in by_month])

    # Annual maxima over water years, keeping only years that are actually complete
    # enough to hold a maximum. A part-year at either end would drag the fit down.
    by_year: dict[int, list[float]] = {}
    for d, v in series:
        wy = d.year if d.month >= wy_start else d.year - 1
        by_year.setdefault(wy, []).append(v)
    maxima = [max(vs) for wy, vs in sorted(by_year.items()) if len(vs) >= 300]

    r2 = gumbel_return_level(maxima, 2)
    r5 = gumbel_return_level(maxima, 5)
    r10 = gumbel_return_level(maxima, 10) if len(maxima) >= MIN_YEARS_FOR_10Y else None

    # High-flow episodes: runs above the 2-year level, merged across short dips so one
    # long monsoon peak is one episode and not twenty.
    episodes: list[tuple[date, date, float]] = []
    if r2 is not None:
        run_start = run_end = None
        run_peak = 0.0
        for d, v in series:
            if v >= r2:
                if run_start is None:
                    run_start, run_peak = d, v
                elif (d.toordinal() - run_end.toordinal()) > EPISODE_GAP_DAYS:
                    episodes.append((run_start, run_end, run_peak))
                    run_start, run_peak = d, v
                run_end = d
                run_peak = max(run_peak, v)
            # Days below the threshold do not end the run immediately; the gap test
            # above does, so a two-day dip inside a flood does not split it.
        if run_start is not None:
            episodes.append((run_start, run_end, run_peak))

    span_years = (series[-1][0].toordinal() - series[0][0].toordinal()) / 365.25
    out = {
        "v": STATS_SCHEMA,
        "days": len(series),
        "from": series[0][0].isoformat(),
        "to": series[-1][0].isoformat(),
        "years": round(span_years, 1),
        "water_year_start_month": wy_start,
        "median_cms": round(_quantile(flows, 0.5), 2),
        "p90_cms": round(_quantile(flows, 0.90), 2),
        "p95_cms": round(_quantile(flows, 0.95), 2),
        "p99_cms": round(_quantile(flows, 0.99), 2),
        "max_cms": round(peak[1], 2),
        "max_on": peak[0].isoformat(),
        "by_month_cms": by_month,
        "annual_maxima": len(maxima),
    }
    if r2 is not None:
        out["return_2y_cms"] = r2
    if r5 is not None:
        out["return_5y_cms"] = r5
    if r10 is not None:
        out["return_10y_cms"] = r10
    if r2 is None:
        # No fitted 2-year level means no yardstick, so there is nothing to count
        # episodes against. Leaving the keys absent makes prone_level() answer
        # "unknown" instead of "zero episodes", which would render as "no history of
        # high water here" on the strength of a measurement we failed to make.
        return out
    out["episodes"] = len(episodes)
    out["episodes_per_decade"] = round(len(episodes) / max(span_years, 1) * 10, 1)

    # How long this river spends unusually high, which unlike the episode COUNT is not
    # fixed by the definition of the threshold. This is the statistic flood-proneness
    # is built on: a place whose river sits above its 2-year level for two months a year
    # is exposed in a way a place that peaks for an afternoon is not.
    high_days = sum(1 for _, v in series if v >= r2)
    out["high_days_per_year"] = round(high_days / max(span_years, 1), 1)

    # Steepness of the flood growth curve: how much worse a rare year is than an
    # ordinary one. A river whose 10-year flood is four times its 2-year flood has far
    # more room to surprise people than one where it is 1.2 times.
    top = out.get("return_10y_cms") or out.get("return_5y_cms")
    if top and r2 > 0:
        # Normalised so a 5-year level stands in for a 10-year one on the same scale,
        # rather than quietly reading as a flatter river for want of a longer record.
        ratio = top / r2
        if not out.get("return_10y_cms"):
            ratio = 1.0 + (ratio - 1.0) * 1.55     # 5y->10y on a Gumbel growth curve
        out["growth_ratio"] = round(ratio, 2)

    if episodes:
        out["last_episode_on"] = episodes[-1][0].isoformat()
        worst = max(episodes, key=lambda e: e[2])
        out["worst_episode_on"] = worst[0].isoformat()
        out["worst_episode_days"] = worst[1].toordinal() - worst[0].toordinal() + 1
        durations = sorted(e[1].toordinal() - e[0].toordinal() + 1 for e in episodes)
        out["median_episode_days"] = durations[len(durations) // 2]
    return out


def percentile_of(stats: dict, flow_cms: float | None) -> float | None:
    """Where a flow sits in this cell's record, 0-100, from the published quantiles.

    Interpolated between the five quantiles we keep rather than the full series, which
    is not shipped. Good to a percent or two in the tail, which is the only part anyone
    reads, and it costs five numbers instead of four thousand.
    """
    if flow_cms is None or not stats or stats.get("no_river"):
        return None
    pts = [(0.0, 0.0)]
    for key, pct in (("median_cms", 50.0), ("p90_cms", 90.0),
                     ("p95_cms", 95.0), ("p99_cms", 99.0), ("max_cms", 100.0)):
        v = stats.get(key)
        if v is None:
            continue
        pts.append((float(v), pct))
    pts.sort()
    if flow_cms >= pts[-1][0]:
        return 100.0
    for i in range(1, len(pts)):
        lo_v, lo_p = pts[i - 1]
        hi_v, hi_p = pts[i]
        if flow_cms <= hi_v:
            if hi_v <= lo_v:
                return round(hi_p, 1)
            frac = (flow_cms - lo_v) / (hi_v - lo_v)
            return round(lo_p + frac * (hi_p - lo_p), 1)
    return 100.0


#: Days a year above the 2-year level that separate the exposure classes.
#:
#: Read off the measured distribution rather than chosen in advance, and the first guess
#: was wrong in an instructive way. It was set from `worst_episode_days`, which spans
#: 2-161 days, so 7/21/45 looked like sensible cut points. But that is the single worst
#: episode in twelve years, not an annual figure: measured over 31 real cells with full
#: daily series, `high_days_per_year` runs 0.7 to 11.3 with a median of 2.6, and those
#: thresholds put 27 of the 31 in one class and left classes 2 and 3 permanently empty.
#:
#: These sit near the 60th, 85th and 97th percentiles of that sample. ⚠️ 31 cells,
#: mostly Thai, is a thin basis: revisit once the climatology covers more of the world,
#: because a classifier calibrated on one country's rivers is exactly the kind of thing
#: that quietly stops meaning anything somewhere else.
PRONE_DAYS = (3.0, 6.0, 10.0)
#: A steep growth curve promotes a cell one class. A river whose rare floods are three
#: times its ordinary ones has room to surprise people that a flat one does not.
PRONE_STEEP_GROWTH = 2.5


def prone_level(stats: dict | None) -> int | None:
    """0-3 flood exposure from this cell's own record, or None if unknown.

    Deliberately about the *past*, not today's water: it answers "does this place have a
    history of high water" and reads the same in the dry season.

    Built on how many days a year the river runs above its 2-year level, plus how steep
    its flood growth curve is. NOT on how often it crosses that level, which is fixed at
    roughly five times a decade by the definition of a 2-year return period and so says
    the same thing about every river there is.

    Returns None, never 0, when the statistics are missing or were computed by an older
    version. "Not measured here" and "measured, and calm" must never render alike.
    """
    if not stats or stats.get("no_river"):
        return None
    if stats.get("v") != STATS_SCHEMA:
        return None
    days = stats.get("high_days_per_year")
    if days is None:
        return None
    level = 0
    for i, cut in enumerate(PRONE_DAYS):
        if days >= cut:
            level = i + 1
    if level and (stats.get("growth_ratio") or 0) >= PRONE_STEEP_GROWTH:
        level = min(3, level + 1)
    return level


def forecast_exceedance(stats: dict | None, outlook_cms: list | None) -> dict | None:
    """First day the 30-day flow outlook passes this cell's own 2- or 5-year level.

    This is the "flood-prone when predicted" signal: not that a number went up, but that
    it is forecast to reach a level this particular river reaches about once every two
    (or five) years. The comparison is against the same grid cell that produced the
    forecast, so the two numbers are the same quantity from the same model.
    """
    if not stats or stats.get("no_river") or not outlook_cms:
        return None
    r2 = stats.get("return_2y_cms")
    r5 = stats.get("return_5y_cms")
    if r2 is None:
        return None
    peak = None
    day_2y = day_5y = None
    for i, raw in enumerate(outlook_cms):
        v = num(raw)
        if v is None:
            continue
        if peak is None or v > peak:
            peak = v
        if day_2y is None and v >= r2:
            day_2y = i
        if r5 is not None and day_5y is None and v >= r5:
            day_5y = i
    if day_2y is None:
        return None
    out = {"day_2y": day_2y, "return_2y_cms": r2}
    if day_5y is not None:
        out["day_5y"] = day_5y
        out["return_5y_cms"] = r5
    if peak is not None:
        out["peak_cms"] = round(peak, 2)
        # How far past an ordinary annual high the peak goes. 1.0 is "a normal big
        # year for this river"; the number is the point, not a colour.
        if r2 > 0:
            out["peak_vs_2y"] = round(peak / r2, 2)
    return out


def fetch_climatology(
    points: list[tuple[str, float, float]],
    health: SourceHealth,
    budget: int = 300,
    sleep=time.sleep,
    today: date | None = None,
) -> dict[str, dict]:
    """Fetch and reduce history for up to `budget` cells.

    Paced at one request a minute because the API's limit is on data volume, not
    request count. Returns only what it managed to get: the caller merges it into a
    cache that fills up across runs, so a rate-limited run costs progress, never data.
    """
    start, end = window(today)
    params = f"daily=river_discharge&start_date={start.isoformat()}&end_date={end.isoformat()}"
    out: dict[str, dict] = {}
    todo = points[:budget]
    no_river = 0

    for offset in range(0, len(todo), BATCH):
        chunk = todo[offset : offset + BATCH]
        if offset:
            sleep(PACE_SECONDS)
        lats = ",".join(f"{lat:.4f}" for _, lat, _ in chunk)
        lons = ",".join(f"{lon:.4f}" for _, _, lon in chunk)
        url = f"{URL}?latitude={lats}&longitude={lons}&{params}"
        payload = None
        for attempt in range(RETRIES + 1):
            try:
                payload = fetch_json(url, timeout=180)
                break
            except Exception as exc:  # noqa: BLE001
                # A 429 here is the *rate* limit, not the daily quota, and it clears on
                # the next minute boundary. Waiting once costs a minute and saves the
                # whole run; the alternative is that one unlucky first batch aborts a
                # scheduled job that only runs once a day. A second failure is taken at
                # face value, because past that it is probably the daily quota, and
                # hammering a free API we depend on is not a fix.
                if attempt == RETRIES:
                    health.warnings.append(
                        f"batch at offset {offset} ({len(chunk)} cells) failed: {exc}")
                    break
                sleep(PACE_SECONDS)
        if payload is None:
            continue

        results = payload if isinstance(payload, list) else [payload]
        if len(results) != len(chunk):
            # Order is the only thing tying a result to a cell, same as the forecast
            # adapter. A length mismatch means we cannot know which is which.
            health.warnings.append(
                f"batch at offset {offset}: expected {len(chunk)} results, got "
                f"{len(results)}, dropped rather than misalign history with cells")
            continue

        for (cid, _, _), result in zip(chunk, results):
            if not isinstance(result, dict):
                continue
            daily = result.get("daily") or {}
            stats = summarise(daily.get("time") or [], daily.get("river_discharge") or [])
            if stats is None:
                continue
            if stats.get("no_river"):
                no_river += 1
            out[cid] = stats

    if no_river:
        health.warnings.append(
            f"{no_river} cells carry no resolvable GloFAS river (peak below "
            f"{MIN_RIVER_CMS} m3/s), recorded so they are not re-fetched")
    health.stations = len(out)
    health.ok = bool(out)
    if not out:
        health.error = "no flood climatology returned"
    return out
