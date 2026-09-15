"""Spike load test — the event this whole architecture exists for.

Normal traffic is near zero. On the worst day of the year it is orders of magnitude
higher, and that is the only day the system matters. This measures two paths
separately, because they fail in completely different ways:

  * READ  — static snapshots on a CDN. Should cost nothing and touch no origin.
  * WRITE — SOS submissions through one Worker and one D1 database. The real limit.

Not a unit test. Run it deliberately:
    python3 -m tests.loadtest            (from workers/)
    python3 -m tests.loadtest --submitters 400 --seconds 10
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seraphim import devserver as ds  # noqa: E402

# Cloudflare free tier, verified 2026-09-15.
WORKER_REQ_PER_DAY = 100_000
D1_FREE_MB = 500

# Thailand's 2011 floods affected roughly 13.6 million people. These are the shapes
# worth planning against, not comfortable ones.
SCENARIOS = {
    "provincial flood": {"affected": 500_000, "map_open_rate": 0.20, "sos_rate": 0.010},
    "regional flood":   {"affected": 3_000_000, "map_open_rate": 0.15, "sos_rate": 0.008},
    "national (2011)":  {"affected": 13_600_000, "map_open_rate": 0.12, "sos_rate": 0.005},
}


def measure_read_path(out_dir: Path) -> dict:
    """What one visitor actually costs us."""
    files = {}
    for name in ("stations.geojson", "meta.json", "tide.json", "areas.json"):
        p = out_dir / name
        if p.exists():
            raw = p.read_bytes()
            files[name] = {"raw": len(raw), "gz": len(gzip.compress(raw, 9))}
    shell = 0
    for name in ("index.html", "sos.html", "ops.html", "sw.js"):
        p = out_dir.parents[1] / "web" / name
        if p.exists():
            shell += len(gzip.compress(p.read_bytes(), 9))
    per_visit = shell + sum(f["gz"] for f in files.values())
    return {"files": files, "shell_gz": shell, "per_visit_gz": per_visit}


def submit_worker(db, lock, n, ip, results, stop_at):
    """One simulated citizen submitting repeatedly."""
    for i in range(n):
        if time.monotonic() > stop_at:
            return
        body = {
            "lat": 13.6 + (i % 100) * 0.001, "lon": 100.7 + (i % 97) * 0.001,
            "needs": ["rescue_boat"] if i % 3 else ["food"],
            "people_count": 1 + i % 8, "water_depth_cm": 40 + (i % 12) * 15,
            "consent": True, "client_id": f"{ip}-{i}",
            "province": "สมุทรปราการ", "district": "บางพลี",
            "contact_name": "ผู้แจ้ง", "contact_phone": "0800000000",
        }
        t0 = time.perf_counter()
        # One database, one writer lock — the same serialisation D1 imposes.
        with lock:
            status, _ = ds.submit(db, body, ip_hash=ip)
        results.append((status, (time.perf_counter() - t0) * 1000))


def measure_write_path(submitters: int, per_submitter: int, seconds: float) -> dict:
    db = ds.connect(":memory:")
    lock = threading.Lock()
    results: list[tuple[int, float]] = []
    stop_at = time.monotonic() + seconds

    threads = [
        threading.Thread(target=submit_worker,
                         args=(db, lock, per_submitter, f"ip-{i}", results, stop_at))
        for i in range(submitters)
    ]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    lat = sorted(ms for _, ms in results)
    codes: dict[int, int] = {}
    for status, _ in results:
        codes[status] = codes.get(status, 0) + 1
    stored = db.execute("SELECT COUNT(*) FROM sos_requests").fetchone()[0]
    page = db.execute("PRAGMA page_size").fetchone()[0]
    count = db.execute("PRAGMA page_count").fetchone()[0]

    return {
        "requests": len(results), "elapsed_s": elapsed,
        "throughput_rps": len(results) / elapsed if elapsed else 0,
        "codes": codes, "stored": stored,
        "p50_ms": statistics.median(lat) if lat else 0,
        "p95_ms": lat[int(len(lat) * 0.95)] if lat else 0,
        "p99_ms": lat[int(len(lat) * 0.99)] if lat else 0,
        "max_ms": lat[-1] if lat else 0,
        "bytes_per_row": (page * count) / stored if stored else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submitters", type=int, default=200)
    ap.add_argument("--each", type=int, default=25)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--out", type=Path, default=Path("../data/out"))
    args = ap.parse_args()

    print("=" * 74)
    print("SeRAPHIM spike load test")
    print("=" * 74)

    read = measure_read_path(args.out.resolve())
    print("\nREAD PATH — static files on a CDN")
    for name, f in read["files"].items():
        print(f"  {name:<20} {f['raw']:>9,} B raw  ->{f['gz']:>8,} B gz")
    print(f"  {'app shell (gz)':<20} {read['shell_gz']:>21,} B")
    print(f"  {'per first visit':<20} {read['per_visit_gz']:>21,} B gzipped")

    write = measure_write_path(args.submitters, args.each, args.seconds)
    print(f"\nWRITE PATH — {args.submitters} concurrent submitters, {write['elapsed_s']:.1f}s")
    print(f"  requests       : {write['requests']:,}")
    print(f"  throughput     : {write['throughput_rps']:,.0f} req/s (single writer)")
    print(f"  latency        : p50 {write['p50_ms']:.2f} ms · p95 {write['p95_ms']:.2f} ms · "
          f"p99 {write['p99_ms']:.2f} ms · max {write['max_ms']:.2f} ms")
    print(f"  status codes   : {write['codes']}")
    print(f"  rows stored    : {write['stored']:,}  ({write['bytes_per_row']:.0f} B/row)")
    accepted = write["codes"].get(201, 0)
    assert write["stored"] == accepted, "data loss: accepted requests were not stored"
    print("  integrity      : every accepted request was stored ✓")

    print("\nCAPACITY vs the Cloudflare free tier")
    print(f"  {'scenario':<20}{'map opens':>12}{'SOS':>10}{'worker req':>13}{'verdict':>22}")
    for name, s in SCENARIOS.items():
        opens = int(s["affected"] * s["map_open_rate"])
        sos = int(s["affected"] * s["sos_rate"])
        # Map reads are static files: they never reach the Worker. Only SOS does,
        # and each submission costs ~3 requests (submit + retries + summary polls).
        worker_req = sos * 3
        ok = worker_req <= WORKER_REQ_PER_DAY
        print(f"  {name:<20}{opens:>12,}{sos:>10,}{worker_req:>13,}"
              f"{('within free tier' if ok else 'EXCEEDS free tier'):>22}")

    biggest = max(SCENARIOS.values(), key=lambda s: s["affected"])
    gb = biggest["affected"] * biggest["map_open_rate"] * read["per_visit_gz"] / 1e9
    rows = int(biggest["affected"] * biggest["sos_rate"])
    mb = rows * max(write["bytes_per_row"], 400) / 1e6
    print(f"\n  national event CDN egress : {gb:,.1f} GB  "
          f"(Cloudflare Pages bandwidth is unmetered)")
    print(f"  national event D1 storage : {mb:,.1f} MB of {D1_FREE_MB} MB free  "
          f"({'fits' if mb < D1_FREE_MB else 'EXCEEDS'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
