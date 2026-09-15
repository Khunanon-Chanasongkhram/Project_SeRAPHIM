"""Open-Meteo — global, keyless forecast backbone.

Two adapters share one batching implementation:
  * RainAdapter      — hourly precipitation, past 24 h and next 72 h
  * DischargeAdapter — GloFAS river discharge, 7-day outlook

These are the reason global scaling is already half-solved: they work at any
coordinate on Earth, so a country with no national gauge network still gets a
forecast layer. Verified 2026-09-15, see docs/DATA_SOURCES.md.

Licence: CC-BY 4.0, free for non-commercial use. Attribution is mandatory and is
carried into every published snapshot. Quota: 10,000 calls/day, 5,000/hour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from seraphim.adapters.base import ForecastAdapter, fetch_json, num, register_forecast
from seraphim.models import SourceHealth

#: Locations per request. Verified working at 150; kept lower for URL-length headroom
#: and to stay well inside the per-minute rate limit.
BATCH_SIZE = 100

#: Batches that fail are retried once, then given up on. One bad batch must not lose
#: the other 1,000 stations.
RETRIES = 1


def _batched(
    base_url: str,
    points: list[tuple[str, float, float]],
    params: str,
    health: SourceHealth,
    timeout: int = 90,
) -> dict[str, dict]:
    """Fetch many coordinates in as few requests as possible.

    Open-Meteo accepts comma-separated coordinates and returns results **in request
    order**, which is the only thing tying a response back to a station id — so the
    ordering assumption is asserted rather than trusted.
    """
    out: dict[str, dict] = {}
    for start in range(0, len(points), BATCH_SIZE):
        chunk = points[start : start + BATCH_SIZE]
        lats = ",".join(f"{lat:.4f}" for _, lat, _ in chunk)
        lons = ",".join(f"{lon:.4f}" for _, _, lon in chunk)
        url = f"{base_url}?latitude={lats}&longitude={lons}&{params}"

        payload = None
        for attempt in range(RETRIES + 1):
            try:
                payload = fetch_json(url, timeout=timeout)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == RETRIES:
                    health.warnings.append(
                        f"batch {start // BATCH_SIZE} ({len(chunk)} points) failed: {exc}"
                    )
        if payload is None:
            continue

        # A single-location request returns an object; many return a list.
        results = payload if isinstance(payload, list) else [payload]
        if len(results) != len(chunk):
            health.warnings.append(
                f"batch {start // BATCH_SIZE}: expected {len(chunk)} results, got {len(results)}"
                " — skipped rather than risk misaligning forecasts with stations"
            )
            continue
        for (sid, _, _), result in zip(chunk, results):
            if isinstance(result, dict):
                out[sid] = result
    return out


def _sum_window(
    times: list[str], values: list, start: datetime, end: datetime
) -> float | None:
    """Sum hourly values inside [start, end). Returns None if nothing usable is present.

    None is not 0.0 here: "no forecast data" and "no rain forecast" must stay distinct.
    """
    total = 0.0
    seen = False
    for t, v in zip(times, values):
        n = num(v)
        if n is None:
            continue
        try:
            ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if start <= ts < end:
            total += n
            seen = True
    return round(total, 1) if seen else None


class RainAdapter(ForecastAdapter):
    id = "openmeteo_rain"
    attribution = "Open-Meteo (CC-BY 4.0)"
    #: Weather models publish roughly every 6 hours; this is the time-sensitive input,
    #: so it gets the fastest refresh.
    refresh_hours = 6.0

    URL = "https://api.open-meteo.com/v1/forecast"
    #: UTC everywhere. Requesting a local timezone here would force us to reason about
    #: offsets in two places instead of one.
    PARAMS = "hourly=precipitation&past_days=1&forecast_days=4&timezone=UTC"

    def fetch_for(self, points):
        health = SourceHealth(source=self.id, ok=False)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        raw = _batched(self.URL, points, self.PARAMS, health)

        out: dict[str, dict] = {}
        for sid, result in raw.items():
            hourly = result.get("hourly") or {}
            times, vals = hourly.get("time") or [], hourly.get("precipitation") or []
            if not times:
                continue
            out[sid] = {
                "rain_past_24h_mm": _sum_window(times, vals, now - timedelta(hours=24), now),
                "rain_next_24h_mm": _sum_window(times, vals, now, now + timedelta(hours=24)),
                "rain_next_72h_mm": _sum_window(times, vals, now, now + timedelta(hours=72)),
            }
        health.ok = bool(out)
        health.stations = len(out)
        if not out:
            health.error = "no rain forecasts returned"
        return out, health


class DischargeAdapter(ForecastAdapter):
    """GloFAS river discharge — the medium-range signal gauges cannot give us.

    Coverage is a ~5 km global model, so it is meaningful on real rivers and empty on
    small canals and gates. Missing values are left as None rather than zero.
    """

    id = "openmeteo_flood"
    attribution = "Open-Meteo / GloFAS (CC-BY 4.0)"
    #: GloFAS is a once-daily product. Refetching it more often returns identical
    #: numbers and wastes roughly 3,400 location-calls a day.
    refresh_hours = 24.0

    URL = "https://flood-api.open-meteo.com/v1/flood"
    PARAMS = "daily=river_discharge&forecast_days=7"

    def fetch_for(self, points):
        health = SourceHealth(source=self.id, ok=False)
        raw = _batched(self.URL, points, self.PARAMS, health)

        out: dict[str, dict] = {}
        no_river = 0
        for sid, result in raw.items():
            daily = result.get("daily") or {}
            series = [num(v) for v in (daily.get("river_discharge") or [])]
            series = [v for v in series if v is not None]
            if not series:
                no_river += 1
                continue
            out[sid] = {
                "discharge_now_cms": round(series[0], 2),
                "discharge_max_7d_cms": round(max(series), 2),
            }
        if no_river:
            health.warnings.append(
                f"{no_river} points have no GloFAS river (expected for canals, gates and headwaters)"
            )
        health.ok = bool(out)
        health.stations = len(out)
        if not out:
            health.error = "no discharge forecasts returned"
        return out, health


register_forecast(RainAdapter())
register_forecast(DischargeAdapter())
