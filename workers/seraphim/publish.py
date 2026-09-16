"""Snapshot publishing.

The whole read path is static files on a CDN, so this module *is* the API. Its output
contract is what the web client depends on; change it deliberately.

Nothing here is committed to git, roughly 600 KB per snapshot, 48 times a day, would
add ~10 GB/year to the repo. Output goes to Cloudflare (R2/Pages); `data/` is ignored.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from seraphim.models import SourceHealth, StationState

#: Bump when the output shape changes in a way clients must notice.
SCHEMA_VERSION = 1

#: Risk level at or above which full reasoning is published per station.
REASONS_FROM_LEVEL = 2

# Health thresholds. Published in meta.json so any monitor can read one field rather
# than re-deriving judgement, and so "is it working" has a single agreed answer.
MIN_EXPECTED_STATIONS = 800        # ~1,121 normally; a big drop means a silent break
MAX_MEDIAN_AGE_MINUTES = 180.0
MAX_STALE_FRACTION = 0.60
MAX_SNAPSHOT_AGE_MINUTES = 90.0    # cron is every 30 min; 3 misses is a real fault


#: Station properties that must survive even when null, because the MapLibre *station*
#: layers read them in paint expressions. A `["get"]` on a missing key returns null, and
#: a null where the style expects a boolean or a number throws and takes the layer down.
#:
#: Only these four: the style's other `get` calls (`level`, `mag`, `alert`,
#: `below_gauge_m`) read province, earthquake, event and terrain features, which are
#: built elsewhere and never pass through here.
#:
#: Everything else is dropped when null. To the UI a missing key and a null key already
#: mean the same thing, and across 11,476 US gauges the nulls alone cost ~78 KB gzipped.
STYLE_CRITICAL = ("stale", "risk_level", "freeboard_m", "discharge_rise_ratio")


def _trim(feature: dict) -> dict:
    """Drop null properties the map style does not read, and a redundant English name.

    Adding the United States took one country file from 122 KB to 834 KB gzipped, the
    same problem a second country caused in Phase 7, and it deserves the same answer:
    a visitor should not download fields that say nothing.
    """
    props = feature["properties"]
    if props.get("name_en") == props.get("name"):
        # True for every US and Dutch gauge: the source publishes one name, not two.
        props.pop("name_en", None)
    feature["properties"] = {
        k: v for k, v in props.items() if v is not None or k in STYLE_CRITICAL
    }
    return feature


def build_geojson(states: list[StationState], risks: dict | None = None) -> dict:
    """One feature per station, carrying its latest reading and derived freeboard.

    GeoJSON because MapLibre consumes it directly with no tile server, which is the
    point of a static architecture. PMTiles replaces this if station counts ever make
    the payload too large for a single fetch.
    """
    risks = risks or {}
    features = []
    for s in states:
        st, ob, fc = s.station, s.observation, s.forecast
        rk = risks.get(st.id)
        features.append(
            _trim({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(st.lon, 6), round(st.lat, 6)]},
                "properties": {
                    "id": st.id,
                    "source": st.source,
                    "name": st.name,
                    "name_en": st.name_en,
                    "basin": st.basin,
                    "agency": st.agency,
                    "province": st.admin.province if st.admin else None,
                    "district": st.admin.district if st.admin else None,
                    "subdistrict": st.admin.subdistrict if st.admin else None,
                    # Levels, all metres above MSL.
                    "level_msl": ob.level_msl,
                    "bank_msl": st.bank_msl,
                    # Which datum those numbers are in. "MSL"/"NAP" compare between
                    # stations; "local" compares only with this station's own
                    # thresholds. Published so the UI can say so rather than implying
                    # every gauge on the map shares one vertical reference.
                    "datum": st.datum,
                    "freeboard_m": s.freeboard_m,
                    "discharge_cms": ob.discharge_cms,
                    "storage_percent": ob.storage_percent,
                    # Provenance and honesty.
                    # Forward-looking signals. Refreshed on a slower cadence than the
                    # level, so they carry their own age.
                    "rain_past_24h_mm": fc.rain_past_24h_mm if fc else None,
                    "rain_next_24h_mm": fc.rain_next_24h_mm if fc else None,
                    "rain_next_72h_mm": fc.rain_next_72h_mm if fc else None,
                    "discharge_now_cms": fc.discharge_now_cms if fc else None,
                    "discharge_max_7d_cms": fc.discharge_max_7d_cms if fc else None,
                    "discharge_rise_ratio": fc.discharge_rise_ratio if fc else None,
                    # A forecast of this gauge's own level, where its national network
                    # publishes one. Same datum and units as level_msl above, so the UI
                    # can put them side by side; None everywhere else.
                    "forecast_level": fc.forecast_level if fc else None,
                    "forecast_level_at": fc.forecast_level_at if fc else None,
                    "forecast_level_source": fc.forecast_level_source if fc else None,
                    "forecast_age_min": (
                        round((s.generated_at - fc.fetched_at).total_seconds() / 60, 1)
                        if fc else None
                    ),
                    "source_severity": ob.source_severity,
                    # Risk, with its reasoning attached. The explanation travels with
                    # the score by design: a bare colour badge is either ignored or
                    # causes panic, and neither is a safe outcome.
                    "risk_level": rk.level if rk else None,
                    "risk_th": rk.level_th if rk else None,
                    "risk_en": rk.level_en if rk else None,
                    "time_to_bank_hr": rk.time_to_bank_hr if rk else None,
                    "rate_m_per_hr": rk.rate_m_per_hr if rk else None,
                    "risk_confidence": rk.confidence if rk else None,
                    # Reasons ride along with the score, but only where the score
                    # asks something of the reader. At level 1 they are boilerplate
                    # ("2.4 m below bank"), and on a failing flood-network connection
                    # every kilobyte is a real cost to someone.
                    "reasons": (
                        [{"code": r.code, "th": r.th, "en": r.en} for r in rk.reasons]
                        if rk and rk.level >= REASONS_FROM_LEVEL else []
                    ),
                    "observed_at": ob.observed_at.isoformat(),
                    "data_age_min": s.data_age_minutes,
                    "stale": s.is_stale,
                },
            })
        )
    return {"type": "FeatureCollection", "features": features}


def build_tide(summaries: list[dict], generated_at: datetime) -> dict:
    """tide.json, coastal predictions.

    Published separately from stations because it has a different shape, a different
    refresh cadence, and two different consumers (coastal flood risk, and the fishing
    planner in Phase 3).
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "attribution": "Open-Meteo Marine (CC-BY 4.0)",
        "note": (
            "Tidal range is classified empirically against each location's own recent "
            "distribution, not from lunar phase: the Gulf of Thailand is mixed and "
            "mainly diurnal, where phase is a poor predictor of range."
        ),
        "points": summaries,
    }


def build_areas(areas: list[dict], generated_at: datetime) -> dict:
    """areas.json, district-level rollup, ordered worst-first.

    A gauge is an instrument; a district is where somebody lives. This is the file an
    official or a resident actually reads.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "method": (
            "Each district takes the level of its worst station, not an average: one "
            "overtopping river is not cancelled out by three calm ones nearby."
        ),
        "areas": areas,
    }


def build_fishing_doc(spots: list[dict], generated_at: datetime) -> dict:
    """fishing.json, calm mode.

    The same tide, weather and lunar data that drives flood risk, answering the
    question people actually have on the other 350 days of the year.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "attribution": "Open-Meteo (CC-BY 4.0); tide from Open-Meteo Marine",
        "method": (
            "Transparent rule-based scoring, not a model. Fish feed on moving water, so "
            "the RATE of tidal change is weighted, not the height. Combined with solunar "
            "periods (lunar transit and antitransit for majors, moonrise and moonset for "
            "minors), low light at dawn and dusk, moon phase, and falling barometric "
            "pressure. Every score carries the factors that produced it."
        ),
        "caveat": (
            "Fishing conditions are folklore-rich and evidence-poor. Treat this as a "
            "starting point, not a promise. Reservoir drawdown is NOT modelled, it needs "
            "a dam dataset we do not have."
        ),
        "spots": spots,
    }


def split_by_country(states, risks, areas: list[dict]) -> tuple[dict, dict]:
    """One station file per country, plus a small index.

    Adding a second country quadrupled the payload every visitor downloads, which is
    the wrong way round: a reader in Bangkok should not pay for British gauges they
    will never look at. Splitting keeps each country's cost flat as more are added,
    which is the only way this scales past two.
    """
    by_country: dict[str, list] = {}
    for st in states:
        cc = (st.station.admin.country if st.station.admin else None) or "XX"
        by_country.setdefault(cc, []).append(st)

    files: dict[str, dict] = {}
    index = {"countries": [], "generated_at": None}
    for cc, group in sorted(by_country.items()):
        gj = build_geojson(group, risks)
        files[f"stations-{cc.lower()}.geojson"] = gj
        rows = [a for a in areas if a.get("country") == cc]
        if rows:
            files[f"areas-{cc.lower()}.json"] = {
                "schema_version": SCHEMA_VERSION, "country": cc, "areas": rows}
        lats = [s.station.lat for s in group]
        lons = [s.station.lon for s in group]
        index["countries"].append({
            "code": cc,
            "stations": len(group),
            "with_bank_level": sum(1 for s in group if s.station.bank_msl is not None),
            "at_or_over_bank": sum(1 for s in group
                                   if s.freeboard_m is not None and s.freeboard_m <= 0),
            "areas": len(rows),
            "bbox": [round(min(lons), 3), round(min(lats), 3),
                     round(max(lons), 3), round(max(lats), 3)],
            "stations_file": f"stations-{cc.lower()}.geojson",
            "areas_file": f"areas-{cc.lower()}.json" if rows else None,
        })
    return files, index


def build_provinces(states, areas: list[dict], generated_at: datetime) -> dict | None:
    """provinces.geojson: the risk picture as areas rather than dots.

    A pin on a gauge tells you about an instrument. A shaded province tells you where
    the trouble is, which is the question people actually open the map with. Province
    is as fine as the free boundary data goes; district shading would be better and is
    the obvious next step if a licensed amphoe layer turns up.
    """
    from seraphim.geo import load_provinces, map_provinces

    mapping, report = map_provinces(states)
    if not mapping:
        return None

    worst: dict[str, dict] = {}
    for a in areas:
        province = a.get("province")
        if not province:
            continue
        cur = worst.get(province)
        if cur is None or a["level"] > cur["level"]:
            worst[province] = a

    counts: dict[str, dict] = {}
    for a in areas:
        province = a.get("province")
        if not province:
            continue
        c = counts.setdefault(province, {"districts": 0, "stations": 0, "over_bank": 0})
        c["districts"] += 1
        c["stations"] += a.get("stations", 0)
        c["over_bank"] += a.get("over_bank", 0)

    shapes = load_provinces()
    features = []
    for f in shapes["features"]:
        english = f["properties"]["name"]
        thai = mapping.get(english)
        a = worst.get(thai) if thai else None
        c = counts.get(thai, {}) if thai else {}
        features.append({
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {
                "name": english,
                "name_th": thai,
                # None, not 1: "no gauges here" and "gauges here, all calm" are
                # different states and must look different on the map.
                "level": a["level"] if a else None,
                "worst_district": a.get("district") if a else None,
                "worst_ttb_hr": a.get("worst_ttb_hr") if a else None,
                "districts": c.get("districts", 0),
                "stations": c.get("stations", 0),
                "over_bank": c.get("over_bank", 0),
            },
        })

    return {
        "type": "FeatureCollection",
        "generated_at": generated_at.isoformat(),
        "attribution": "Province outlines: apisit/thailand.json (MIT)",
        "join": report,
        "features": features,
    }


def build_meta(
    states: list[StationState],
    health: list[SourceHealth],
    generated_at: datetime,
    tide_points: int = 0,
    risks: dict | None = None,
    validation: dict | None = None,
) -> dict:
    """Machine-readable health. Published so a broken feed is visible, not silent."""
    ages = [s.data_age_minutes for s in states]
    overtopped = [s for s in states if s.freeboard_m is not None and s.freeboard_m <= 0]
    with_bank = [s for s in states if s.freeboard_m is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "counts": {
            "stations": len(states),
            "with_bank_level": len(with_bank),
            "stale": sum(1 for s in states if s.is_stale),
            "at_or_over_bank": len(overtopped),
            "with_forecast": sum(1 for s in states if s.forecast is not None),
            "tide_points": tide_points,
        },
        "risk": _risk_counts(risks or {}),
        "validation": _validation_headline(validation),
        "health": build_health(states, health, {
            "stations": len(states),
            "stale": sum(1 for s in states if s.is_stale),
        }),
        "data_age_minutes": {
            "min": round(min(ages), 1) if ages else None,
            "median": round(sorted(ages)[len(ages) // 2], 1) if ages else None,
            "max": round(max(ages), 1) if ages else None,
        },
        "sources": [
            {
                **{k: v for k, v in asdict(h).items() if k != "fetched_at"},
                "fetched_at": h.fetched_at.isoformat(),
            }
            for h in health
        ],
        "attribution": sorted({h.source for h in health}),
        "disclaimer": (
            "SeRAPHIM is not an official emergency channel. In an emergency in Thailand "
            "call 1784 (DDPM) or 191."
        ),
    }


def build_health(states, health, meta_counts: dict) -> dict:
    """An explicit verdict, not a pile of numbers.

    The failure this guards against is the quiet one: the pipeline keeps running, the
    map keeps rendering, and the data behind it stopped being true hours ago. Each
    check names what is wrong in words a human can act on.
    """
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str, severity: str = "fail") -> None:
        checks.append({"check": name, "status": "pass" if ok else severity,
                       "detail": detail})

    total = meta_counts["stations"]
    check("station_count", total >= MIN_EXPECTED_STATIONS,
          f"{total} stations (expect >= {MIN_EXPECTED_STATIONS})")

    failed = [h.source for h in health if not h.ok and not h.optional]
    omitted = [h.source for h in health if not h.ok and h.optional]
    detail = "all sources ok" if not failed else f"failed: {', '.join(failed)}"
    if omitted:
        # Named, not hidden: the layer really is missing and the UI should say so.
        # It just does not make the snapshot unhealthy.
        detail += f" (not configured: {', '.join(omitted)})"
    check("sources", not failed, detail)

    ages = [s.data_age_minutes for s in states]
    median = sorted(ages)[len(ages) // 2] if ages else None
    check("data_freshness", median is not None and median <= MAX_MEDIAN_AGE_MINUTES,
          f"median reading age {median} min (limit {MAX_MEDIAN_AGE_MINUTES:g})",
          severity="warn")

    stale_fraction = (meta_counts["stale"] / total) if total else 1.0
    check("reporting_rate", stale_fraction <= MAX_STALE_FRACTION,
          f"{stale_fraction:.0%} of stations not reporting (limit {MAX_STALE_FRACTION:.0%})",
          severity="warn")

    worst = "pass"
    for c in checks:
        if c["status"] == "fail":
            worst = "fail"
            break
        if c["status"] == "warn":
            worst = "warn"
    return {
        "status": worst,
        "checks": checks,
        "snapshot_stale_after_minutes": MAX_SNAPSHOT_AGE_MINUTES,
        "note": (
            "A client whose snapshot is older than snapshot_stale_after_minutes should "
            "tell the reader the data is stale rather than render it as current."
        ),
    }


def _validation_headline(validation: dict | None) -> dict | None:
    """The one number a reader deserves next to a prediction: does it beat doing nothing?

    Reported for rivers that actually moved, because predicting a flat river correctly
    is not skill and flat rivers dominate the raw figure.
    """
    if not validation or not validation.get("by_lead"):
        return None
    rows = [r for r in validation["by_lead"] if r.get("moving_only")]
    if not rows:
        return None
    best = max(rows, key=lambda r: r["moving_only"]["n"])
    m = best["moving_only"]
    return {
        "lead_hours": best["lead_hours"],
        "samples": m["n"],
        "median_error_m": m["median_error_m"],
        "skill_vs_persistence": m["skill_vs_persistence"],
        "hit_rate": (validation.get("bank_calls") or {}).get("hit_rate"),
        "note": "measured on rivers that actually moved; see validation.json",
    }


def _risk_counts(risks: dict) -> dict:
    by_level = {str(i): 0 for i in range(1, 6)}
    ttbs = []
    for r in risks.values():
        by_level[str(r.level)] = by_level.get(str(r.level), 0) + 1
        if r.time_to_bank_hr is not None:
            ttbs.append(r.time_to_bank_hr)
    return {
        "by_level": by_level,
        "with_time_to_bank": len(ttbs),
        "soonest_to_bank_hr": round(min(ttbs), 1) if ttbs else None,
        "trend_confidence": {
            c: sum(1 for r in risks.values() if r.trend_confidence == c)
            for c in ("good", "fair", "poor", "none")
        },
    }


def write_snapshot(
    out_dir: Path,
    geojson: dict,
    meta: dict,
    tide: dict | None = None,
    areas: dict | None = None,
    fishing: dict | None = None,
    provinces: dict | None = None,
    extra: dict[str, dict] | None = None,
) -> list[Path]:
    """Write the current snapshot. Gzip alongside: it is ~10x smaller and CDN-friendly."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # The combined file is still built, because the archive and the backtest need one
    # timeline per station regardless of country, but it is no longer published: it
    # would double what a visitor downloads for no benefit once the per-country files
    # exist.
    items = [("meta.json", meta)]
    if geojson is not None:
        items.insert(0, ("stations.geojson", geojson))
    if tide is not None:
        items.append(("tide.json", tide))
    if areas is not None:
        items.append(("areas.json", areas))
    if fishing is not None:
        items.append(("fishing.json", fishing))
    if provinces is not None:
        items.append(("provinces.geojson", provinces))
    for name, payload in (extra or {}).items():
        if payload is not None:
            items.append((name, payload))
    for name, payload in items:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = out_dir / name
        path.write_bytes(raw)
        gz = out_dir / f"{name}.gz"
        gz.write_bytes(gzip.compress(raw, 9))
        written.extend([path, gz])
    return written


def prune_archive(root: Path, now: datetime, keep_hours: float) -> int:
    """Delete archive files older than the window the risk engine actually reads.

    The archive is restored into CI from a cache between runs, so without a bound it
    would grow by ~48 files a day forever. The risk engine only fits trends over the
    last few hours; durable history lives in R2, not here.
    """
    if keep_hours <= 0 or not root.is_dir():
        return 0
    cutoff = now - timedelta(hours=keep_hours)
    removed = 0
    for path in root.rglob("*.json.gz"):
        try:
            # Path is YYYY/MM/DD/HHMM.json.gz, parse rather than trust mtime, which
            # a cache restore resets.
            stamp = datetime.strptime(
                f"{path.parent.parent.parent.name}{path.parent.parent.name}"
                f"{path.parent.name}{path.stem.split('.')[0]}",
                "%Y%m%d%H%M",
            ).replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    for d in sorted(root.rglob("*"), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return removed


def archive_path(root: Path, generated_at: datetime) -> Path:
    """Time-partitioned archive. History is what makes rate-of-rise possible in Phase 2."""
    ts = generated_at.astimezone(timezone.utc)
    return root / f"{ts:%Y/%m/%d}" / f"{ts:%H%M}.json.gz"


def write_archive(root: Path, geojson: dict, generated_at: datetime) -> Path:
    path = archive_path(root, generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(geojson, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(gzip.compress(raw, 9))
    return path
