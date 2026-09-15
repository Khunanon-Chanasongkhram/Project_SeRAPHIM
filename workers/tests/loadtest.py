"""Spike load test: what one visitor costs, and what a flood costs.

Normal traffic is near zero. On the worst day of the year it is orders of magnitude
higher, and that is the only day this matters. Everything served is a static file on a
CDN, so there is no origin to fall over. The question is bandwidth.

Not a unit test. Run it deliberately, from workers/:

    python3 -m tests.loadtest
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: GitHub Pages soft bandwidth limit. Soft, so GitHub throttles or emails rather than
#: billing, but it is the ceiling the read path now lives under.
PAGES_BANDWIDTH_GB = 100

# Thailand's 2011 floods affected roughly 13.6 million people. These are the shapes
# worth planning against, not comfortable ones.
SCENARIOS = {
    "quiet month":      {"affected": 20_000, "map_open_rate": 1.00},
    "provincial flood": {"affected": 500_000, "map_open_rate": 0.20},
    "regional flood":   {"affected": 3_000_000, "map_open_rate": 0.15},
    "national (2011)":  {"affected": 13_600_000, "map_open_rate": 0.12},
}


def measure(out_dir: Path, web_dir: Path) -> dict:
    """What one visitor actually downloads."""
    files: dict[str, dict] = {}
    for name in ("stations.geojson", "meta.json", "tide.json", "areas.json", "fishing.json"):
        p = out_dir / name
        if p.exists():
            raw = p.read_bytes()
            files[name] = {"raw": len(raw), "gz": len(gzip.compress(raw, 9))}

    shell = 0
    for p in sorted(web_dir.glob("*.html")):
        shell += len(gzip.compress(p.read_bytes(), 9))
    for p in sorted(web_dir.glob("*.js")):
        shell += len(gzip.compress(p.read_bytes(), 9))

    # The map is the common entry point. The fishing page is a separate visit and
    # pulls its own, larger file, so it is counted on its own.
    map_visit = shell + sum(
        files[n]["gz"] for n in ("stations.geojson", "meta.json", "tide.json", "areas.json")
        if n in files
    )
    fish_visit = shell + files.get("fishing.json", {}).get("gz", 0) + \
        files.get("tide.json", {}).get("gz", 0)
    return {"files": files, "shell_gz": shell,
            "map_visit_gz": map_visit, "fish_visit_gz": fish_visit}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("../data/out"))
    ap.add_argument("--web", type=Path, default=Path("../web"))
    args = ap.parse_args()

    m = measure(args.out.resolve(), args.web.resolve())

    print("=" * 70)
    print("SeRAPHIM read path: what a visit costs")
    print("=" * 70)
    for name, f in m["files"].items():
        ratio = f["raw"] / f["gz"] if f["gz"] else 0
        print(f"  {name:<20}{f['raw']:>10,} B raw ->{f['gz']:>9,} B gz  ({ratio:.1f}x)")
    print(f"  {'app shell (gz)':<20}{m['shell_gz']:>31,} B")
    print()
    print(f"  map visit   {m['map_visit_gz']:>9,} B gzipped")
    print(f"  fishing     {m['fish_visit_gz']:>9,} B gzipped")

    print(f"\nAgainst GitHub Pages' {PAGES_BANDWIDTH_GB} GB/month soft limit")
    print(f"  {'scenario':<20}{'map opens':>12}{'bandwidth':>13}{'verdict':>22}")
    for name, s in SCENARIOS.items():
        opens = int(s["affected"] * s["map_open_rate"])
        gb = opens * m["map_visit_gz"] / 1e9
        if gb < PAGES_BANDWIDTH_GB * 0.6:
            verdict = "comfortable"
        elif gb < PAGES_BANDWIDTH_GB:
            verdict = "close to the line"
        else:
            verdict = "OVER, needs a CDN"
        print(f"  {name:<20}{opens:>12,}{gb:>11,.1f} GB{verdict:>22}")

    print("\n  Every file above is static and served from a CDN, so there is no origin")
    print("  to overload. Going over the limit means throttling, not an outage or a bill.")
    print("  If it ever happens: put Cloudflare in front, or move to Cloudflare Pages,")
    print("  whose bandwidth is unmetered. Neither needs a code change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
