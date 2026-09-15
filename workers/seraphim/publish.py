"""Snapshot publishing.

The whole read path is static files on a CDN, so this module *is* the API. Its output
contract is what the web client depends on; change it deliberately.

Nothing here is committed to git — roughly 600 KB per snapshot, 48 times a day, would
add ~10 GB/year to the repo. Output goes to Cloudflare (R2/Pages); `data/` is ignored.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from seraphim.models import SourceHealth, StationState

#: Bump when the output shape changes in a way clients must notice.
SCHEMA_VERSION = 1


def build_geojson(states: list[StationState]) -> dict:
    """One feature per station, carrying its latest reading and derived freeboard.

    GeoJSON because MapLibre consumes it directly with no tile server — which is the
    point of a static architecture. PMTiles replaces this if station counts ever make
    the payload too large for a single fetch.
    """
    features = []
    for s in states:
        st, ob, fc = s.station, s.observation, s.forecast
        features.append(
            {
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
                    # Levels — all metres above MSL.
                    "level_msl": ob.level_msl,
                    "bank_msl": st.bank_msl,
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
                    "forecast_age_min": (
                        round((s.generated_at - fc.fetched_at).total_seconds() / 60, 1)
                        if fc else None
                    ),
                    "source_severity": ob.source_severity,
                    "observed_at": ob.observed_at.isoformat(),
                    "data_age_min": s.data_age_minutes,
                    "stale": s.is_stale,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def build_tide(summaries: list[dict], generated_at: datetime) -> dict:
    """tide.json — coastal predictions.

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


def build_meta(
    states: list[StationState],
    health: list[SourceHealth],
    generated_at: datetime,
    tide_points: int = 0,
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


def write_snapshot(
    out_dir: Path, geojson: dict, meta: dict, tide: dict | None = None
) -> list[Path]:
    """Write the current snapshot. Gzip alongside: it is ~10x smaller and CDN-friendly."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    items = [("stations.geojson", geojson), ("meta.json", meta)]
    if tide is not None:
        items.append(("tide.json", tide))
    for name, payload in items:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = out_dir / name
        path.write_bytes(raw)
        gz = out_dir / f"{name}.gz"
        gz.write_bytes(gzip.compress(raw, 9))
        written.extend([path, gz])
    return written


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
