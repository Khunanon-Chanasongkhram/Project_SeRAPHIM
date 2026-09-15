#!/usr/bin/env python3
"""Check a SeRAPHIM deployment, one piece at a time.

Run it after each step of docs/DEPLOY.md so you find out immediately which part is
wrong, instead of loading the map and seeing a blank screen with no explanation.

    python3 scripts/check_deploy.py --data https://pub-xxxx.r2.dev/latest
    python3 scripts/check_deploy.py --data ... --api https://seraphim-sos.you.workers.dev
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn "}[status]
    print(f"[{mark}] {name}" + (f"\n          {detail}" if detail else ""))


def get(url: str, origin: str | None = None, timeout: int = 25):
    """Fetch a URL, optionally pretending to be a browser on another origin."""
    headers = {"User-Agent": "SeRAPHIM-deploy-check/1.0"}
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:  # noqa: BLE001
        return None, {}, str(e).encode()


def check_data(base: str) -> None:
    base = base.rstrip("/")
    print(f"\nSnapshots at {base}\n" + "-" * 66)

    status, headers, body = get(f"{base}/meta.json")
    if status != 200:
        record(FAIL, "meta.json reachable",
               f"got {status}. Check the bucket is public and the URL ends in /latest")
        return
    record(PASS, "meta.json reachable")

    # Browsers send an Origin header. Without a matching Allow-Origin on the response
    # the fetch fails silently in the console and the map just stays empty.
    _, cors_headers, _ = get(f"{base}/meta.json", origin="https://example.pages.dev")
    allow = cors_headers.get("Access-Control-Allow-Origin")
    if allow:
        record(PASS, "CORS allows browsers", f"Access-Control-Allow-Origin: {allow}")
    else:
        record(FAIL, "CORS allows browsers",
               "No Access-Control-Allow-Origin. Add the CORS policy in R2 > Settings, "
               "or the map will load nothing and show no error.")

    ctype = headers.get("Content-Type", "")
    if "json" in ctype:
        record(PASS, "content type", ctype)
    else:
        record(WARN, "content type",
               f"got '{ctype}'. Expected JSON. Cloudflare will not compress it, "
               "so payloads stay ~10x bigger than they need to be.")

    try:
        meta = json.loads(body)
    except json.JSONDecodeError:
        record(FAIL, "meta.json parses", "not valid JSON")
        return

    stations = meta.get("counts", {}).get("stations", 0)
    if stations >= 800:
        record(PASS, "station count", f"{stations} stations")
    else:
        record(FAIL, "station count", f"only {stations}. Expected around 1,121.")

    gen = meta.get("generated_at")
    if gen:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds() / 60
        if age < 90:
            record(PASS, "snapshot freshness", f"{age:.0f} min old")
        else:
            record(WARN, "snapshot freshness",
                   f"{age:.0f} min old. Is the ingest cron running?")

    health = meta.get("health", {})
    if health.get("status") == "pass":
        record(PASS, "pipeline health", "all checks pass")
    elif health:
        bad = [c["detail"] for c in health.get("checks", []) if c["status"] != "pass"]
        record(WARN, "pipeline health", f"{health.get('status')}: " + "; ".join(bad))

    ttb = meta.get("risk", {}).get("with_time_to_bank", 0)
    if ttb:
        record(PASS, "time to bank", f"{ttb} stations, soonest "
               f"{meta['risk'].get('soonest_to_bank_hr')} h")
    else:
        record(WARN, "time to bank",
               "none yet. Normal for the first 1-2 hours while the archive builds.")

    for name in ("stations.geojson", "tide.json", "areas.json", "fishing.json"):
        s, _, _ = get(f"{base}/{name}")
        record(PASS if s == 200 else FAIL, f"{name}", "" if s == 200 else f"got {s}")


def check_api(base: str) -> None:
    base = base.rstrip("/")
    print(f"\nSOS Worker at {base}\n" + "-" * 66)

    status, _, body = get(f"{base}/api/health")
    if status != 200:
        record(FAIL, "worker responding", f"got {status}")
        return
    record(PASS, "worker responding")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        record(FAIL, "health returns JSON")
        return

    if "1784" in json.dumps(payload):
        record(PASS, "disclaimer attached", "1784 / 191 present on responses")
    else:
        record(WARN, "disclaimer attached", "expected the 1784 / 191 notice")

    # The queue holds names, phone numbers and locations of people in danger.
    # It must never answer an unauthenticated caller.
    status, _, _ = get(f"{base}/api/sos")
    if status == 401:
        record(PASS, "queue requires auth", "unauthenticated read returns 401")
    else:
        record(FAIL, "queue requires auth",
               f"got {status}, expected 401. Personal data may be exposed.")

    status, _, body = get(f"{base}/api/public/summary")
    if status == 200:
        blob = body.decode("utf-8", "replace")
        leaky = any(k in blob for k in ('"contact_phone"', '"contact_name"', '"note"'))
        record(FAIL if leaky else PASS, "public summary has no personal data",
               "found personal fields" if leaky else "aggregated only")
    else:
        record(WARN, "public summary", f"got {status}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="R2 base URL, e.g. https://pub-xxxx.r2.dev/latest")
    ap.add_argument("--api", help="Worker URL, e.g. https://seraphim-sos.you.workers.dev")
    args = ap.parse_args()
    if not args.data and not args.api:
        ap.error("give --data and/or --api")

    if args.data:
        check_data(args.data)
    if args.api:
        check_api(args.api)

    fails = sum(1 for s, _, _ in results if s == FAIL)
    warns = sum(1 for s, _, _ in results if s == WARN)
    print("\n" + "=" * 66)
    print(f"{len(results) - fails - warns} passed, {warns} warnings, {fails} failed")
    if fails:
        print("\nFix the failures above before moving to the next step.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
