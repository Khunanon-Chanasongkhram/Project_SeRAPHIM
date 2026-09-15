"""Entry point. `python -m seraphim.cli build` is what the cron job runs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from seraphim.adapters import registry
from seraphim.models import SourceHealth, StationState
from seraphim.publish import build_geojson, build_meta, write_archive, write_snapshot


def build(out_root: Path, sources: list[str] | None = None, archive: bool = True) -> int:
    generated_at = datetime.now(timezone.utc)
    selected = {k: v for k, v in registry.items() if not sources or k in sources}
    if not selected:
        print(f"no adapters matched {sources!r}; available: {sorted(registry)}", file=sys.stderr)
        return 2

    states: list[StationState] = []
    health: list[SourceHealth] = []

    for name, adapter in selected.items():
        print(f"[{name}] fetching…", flush=True)
        stations, observations, h = adapter.fetch()
        health.append(h)
        if not h.ok:
            # Degrade, never abort: a partial map during a flood beats no map.
            print(f"[{name}] FAILED: {h.error}", file=sys.stderr)
            continue
        by_id = {o.station_id: o for o in observations}
        for st in stations:
            ob = by_id.get(st.id)
            if ob is not None:
                states.append(StationState(station=st, observation=ob, generated_at=generated_at))
        print(f"[{name}] {h.stations} stations, {h.observations} observations", flush=True)
        for w in h.warnings:
            print(f"[{name}] warning: {w}", file=sys.stderr)

    geojson = build_geojson(states)
    meta = build_meta(states, health, generated_at)

    written = write_snapshot(out_root / "out", geojson, meta)
    if archive and states:
        written.append(write_archive(out_root / "archive", geojson, generated_at))

    for p in written:
        print(f"wrote {p} ({p.stat().st_size:,} bytes)")

    c = meta["counts"]
    print(
        f"\n{c['stations']} stations | {c['with_bank_level']} with bank level | "
        f"{c['at_or_over_bank']} at/over bank | {c['stale']} stale | "
        f"median age {meta['data_age_minutes']['median']} min"
    )

    # Every source failing is a real failure; some succeeding is not.
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
    b.add_argument("--no-archive", action="store_true", help="skip writing the time-partitioned archive")

    sub.add_parser("sources", help="list registered adapters")

    args = parser.parse_args(argv)
    if args.command == "sources":
        for name, a in sorted(registry.items()):
            print(f"{name:<12} {a.country:<3} {a.attribution}")
        return 0
    return build(args.out.resolve(), args.sources, archive=not args.no_archive)


if __name__ == "__main__":
    raise SystemExit(main())
