"""Per-source forecast cache.

Water levels change every 30 minutes; forecasts do not. Refetching them on the level
cadence would spend ~100,000 Open-Meteo calls a day to receive identical numbers,
against a 10,000/day free allowance.

Each source is cached separately under its own key, because they go stale at genuinely
different rates, rainfall models refresh ~6-hourly, GloFAS is a daily product, and
tide is a harmonic prediction that barely moves. Caching them together would force the
slowest-changing source to be refetched at the fastest source's cadence.

In CI the cache directory is restored and saved with actions/cache: free, and no
database required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(root: Path, key: str, max_age_hours: float) -> dict | None:
    """Return a cached entry, or None if absent, unreadable or stale.

    Any error is a cache miss. A corrupt cache must trigger a refetch, never poison a
    snapshot with unparseable data.
    """
    try:
        raw = json.loads((root / f"{key}.json").read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
    except Exception:  # noqa: BLE001
        return None
    if fetched_at.tzinfo is None:
        return None
    if datetime.now(timezone.utc) - fetched_at > timedelta(hours=max_age_hours):
        return None
    return raw


def save(root: Path, key: str, data: object) -> datetime:
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    (root / f"{key}.json").write_text(
        json.dumps(
            {"fetched_at": now.isoformat(), "data": data},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return now
