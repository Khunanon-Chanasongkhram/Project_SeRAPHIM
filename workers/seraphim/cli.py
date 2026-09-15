"""Entry point. `python -m seraphim.cli build` is what the cron job runs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from seraphim import cache
from seraphim.adapters import forecast_registry, registry
from seraphim.adapters.marine import TideAdapter, summarise
from seraphim.models import Forecast, SourceHealth, StationState
from seraphim.history import fit_trend, load_history, merge_current
from seraphim.adapters.spots import all_spots, fetch_weather
from seraphim.fishing import build_fishing
from seraphim.publish import (
    build_areas,
    build_fishing_doc,
    build_geojson,
    build_meta,
    build_tide,
    write_archive,
    write_snapshot,
)
from seraphim.risk import assess, rollup


def _forecasts(
    stations,
    cache_root: Path,
    generated_at: datetime,
    health: list[SourceHealth],
    scale: float,
) -> tuple[dict[str, dict], datetime]:
    """Per-station forward-looking fields, from cache where still fresh."""
    points = [(s.id, s.lat, s.lon) for s in stations]
    merged: dict[str, dict] = {}
    oldest = generated_at

    for name, adapter in forecast_registry.items():
        max_age = adapter.refresh_hours * scale
        hit = cache.load(cache_root, name, max_age)
        if hit:
            data = hit["data"]
            fetched = datetime.fromisoformat(hit["fetched_at"])
            age = (generated_at - fetched).total_seconds() / 3600
            print(f"[{name}] cache hit ({age:.1f} h old, limit {max_age:g} h)")
        else:
            print(f"[{name}] refreshing {len(points)} points (limit {max_age:g} h)…", flush=True)
            data, h = adapter.fetch_for(points)
            health.append(h)
            for w in h.warnings:
                print(f"[{name}] warning: {w}", file=sys.stderr)
            if not h.ok:
                print(f"[{name}] FAILED: {h.error}", file=sys.stderr)
                continue
            fetched = cache.save(cache_root, name, data)
            print(f"[{name}] {len(data)} points enriched", flush=True)

        oldest = min(oldest, fetched)
        for sid, fields in data.items():
            merged.setdefault(sid, {}).update(fields)

    return merged, oldest


def _tide(
    cache_root: Path, generated_at: datetime, health: list[SourceHealth], scale: float
) -> list[dict]:
    adapter = TideAdapter()
    max_age = adapter.refresh_hours * scale
    hit = cache.load(cache_root, adapter.id, max_age)
    if hit:
        age = (generated_at - datetime.fromisoformat(hit["fetched_at"])).total_seconds() / 3600
        print(f"[{adapter.id}] cache hit ({age:.1f} h old, limit {max_age:g} h)")
        return hit["data"]

    print(f"[{adapter.id}] refreshing coastal predictions…", flush=True)
    series, th = adapter.fetch()
    health.append(th)
    for w in th.warnings:
        print(f"[{adapter.id}] warning: {w}", file=sys.stderr)
    if not series:
        print(f"[{adapter.id}] FAILED: {th.error}", file=sys.stderr)
        return []
    tide = [summarise(s, generated_at) for s in series]
    cache.save(cache_root, adapter.id, tide)
    print(f"[{adapter.id}] {len(tide)} points", flush=True)
    return tide


def build(
    out_root: Path,
    sources: list[str] | None = None,
    archive: bool = True,
    forecasts: bool = True,
    refresh_scale: float = 1.0,
) -> int:
    generated_at = datetime.now(timezone.utc)
    selected = {k: v for k, v in registry.items() if not sources or k in sources}
    if not selected:
        print(f"no adapters matched {sources!r}; available: {sorted(registry)}", file=sys.stderr)
        return 2

    all_stations = []
    observations_by_id = {}
    health: list[SourceHealth] = []

    for name, adapter in selected.items():
        print(f"[{name}] fetching…", flush=True)
        stations, observations, h = adapter.fetch()
        health.append(h)
        if not h.ok:
            # Degrade, never abort: a partial map during a flood beats no map.
            print(f"[{name}] FAILED: {h.error}", file=sys.stderr)
            continue
        all_stations.extend(stations)
        observations_by_id.update({o.station_id: o for o in observations})
        print(f"[{name}] {h.stations} stations, {h.observations} observations", flush=True)
        for w in h.warnings:
            print(f"[{name}] warning: {w}", file=sys.stderr)

    # --- forecasts: cached, because they do not change on the level cadence ---
    forecast_fields: dict[str, dict] = {}
    tide_points: list[dict] = []
    forecast_fetched_at = generated_at
    if forecasts and all_stations:
        cache_root = out_root / "cache"
        forecast_fields, forecast_fetched_at = _forecasts(
            all_stations, cache_root, generated_at, health, refresh_scale
        )
        tide_points = _tide(cache_root, generated_at, health, refresh_scale)

    states: list[StationState] = []
    for st in all_stations:
        ob = observations_by_id.get(st.id)
        if ob is None:
            continue
        fields = forecast_fields.get(st.id)
        fc = Forecast(fetched_at=forecast_fetched_at, **fields) if fields else None
        states.append(
            StationState(station=st, observation=ob, generated_at=generated_at, forecast=fc)
        )

    # --- risk: needs history, which lives in the archive we have already written ---
    history = load_history(out_root / "archive", generated_at)
    history = merge_current(history, states)
    station_trends = {sid: fit_trend(pts) for sid, pts in history.items()}

    risks = {
        s.station.id: assess(s, station_trends.get(s.station.id), tide_points, generated_at)
        for s in states
    }
    areas = rollup(states, risks)
    conf = {}
    for r in risks.values():
        conf[r.trend_confidence] = conf.get(r.trend_confidence, 0) + 1
    print(
        f"[risk] {len(risks)} assessed | trend confidence: "
        + ", ".join(f"{k}={v}" for k, v in sorted(conf.items()))
        + f" | {sum(1 for r in risks.values() if r.time_to_bank_hr is not None)} with time-to-bank"
    )

    # --- calm mode: the same data, answering the question people have on quiet days ---
    fishing_doc = None
    if forecasts and tide_points:
        spots = all_spots()
        hit = cache.load(out_root / "cache", "spot_weather", 6.0 * refresh_scale)
        if hit:
            spot_weather = hit["data"]
            print(f"[fishing] spot weather cache hit ({len(spot_weather)} spots)")
        else:
            spot_weather, wh = fetch_weather(spots)
            health.append(wh)
            if wh.ok:
                cache.save(out_root / "cache", "spot_weather", spot_weather)
            else:
                print(f"[fishing] weather FAILED: {wh.error}", file=sys.stderr)
        tide_by_id = {t["id"]: t for t in tide_points}
        plans = build_fishing(spots, tide_by_id, spot_weather, generated_at.date())
        fishing_doc = build_fishing_doc(plans, generated_at)
        peak = max((d["peak_score"] for p in plans for d in p["days"]), default=0)
        print(f"[fishing] {len(plans)} spots x {len(plans[0]['days'])} days | best score {peak}")

    geojson = build_geojson(states, risks)
    meta = build_meta(states, health, generated_at, tide_points=len(tide_points), risks=risks)
    tide = build_tide(tide_points, generated_at) if tide_points else None
    areas_doc = build_areas(areas, generated_at)

    written = write_snapshot(out_root / "out", geojson, meta, tide, areas_doc, fishing_doc)
    if archive and states:
        written.append(write_archive(out_root / "archive", geojson, generated_at))
    for p in written:
        print(f"wrote {p} ({p.stat().st_size:,} bytes)")

    c = meta["counts"]
    rl = meta["risk"]["by_level"]
    print(
        f"\nrisk levels: 5={rl['5']} 4={rl['4']} 3={rl['3']} 2={rl['2']} 1={rl['1']}"
        f" | soonest to bank: {meta['risk']['soonest_to_bank_hr']} h"
        f" | districts: {len(areas)}"
    )
    print(
        f"{c['stations']} stations | {c['with_bank_level']} with bank level | "
        f"{c['at_or_over_bank']} at/over bank | {c['stale']} stale | "
        f"{c['with_forecast']} with forecast | {c['tide_points']} tide points | "
        f"median age {meta['data_age_minutes']['median']} min"
    )

    if not any(h.ok for h in health):
        print("all sources failed", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seraphim", description="SeRAPHIM snapshot builder")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="fetch sources and write a snapshot")
    b.add_argument("--out", type=Path, default=Path("../data"), help="output root (default: ../data)")
    b.add_argument("--source", action="append", dest="sources", help="limit to adapter id (repeatable)")
    b.add_argument("--no-archive", action="store_true", help="skip the time-partitioned archive")
    b.add_argument("--no-forecast", action="store_true", help="water levels only, no upstream forecast calls")
    b.add_argument(
        "--refresh-scale",
        type=float,
        default=1.0,
        metavar="X",
        help="multiply every adapter's refresh interval (e.g. 0 forces a refetch)",
    )

    sub.add_parser("sources", help="list registered adapters")

    args = parser.parse_args(argv)
    if args.command == "sources":
        for name, a in sorted(registry.items()):
            print(f"  level    {name:<18} {a.country:<3} {a.attribution}")
        for name, a in sorted(forecast_registry.items()):
            print(f"  forecast {name:<18} *   {a.attribution}")
        print(f"  tide     {TideAdapter.id:<18} TH  {TideAdapter.attribution}")
        return 0
    return build(
        args.out.resolve(),
        args.sources,
        archive=not args.no_archive,
        forecasts=not args.no_forecast,
        refresh_scale=args.refresh_scale,
    )


if __name__ == "__main__":
    raise SystemExit(main())
