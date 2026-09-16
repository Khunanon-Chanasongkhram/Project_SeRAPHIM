"""Thai reservoirs and dams, from ThaiWater's dam feed.

This is the other half of what thaiwater.net shows: river gauges answer "is the channel
full", dams answer "is the reservoir full and is it about to release". They are different
things and are deliberately NOT modelled as gauges. A dam has no bank level and no
time-to-bank; it has a storage percentage, an inflow and a release rate.

Endpoint: `api-v3.thaiwater.net/api/v1/thaiwater30/analyst/dam`, keyless, ~1 MB.
Probed 2026-09-16. The `public/` namespace has no dam route at all; every
`public/dam*` guess 404s. This path came from the site's own JS bundle, not a guess.

Four groups arrive together and they are not equivalent:

    dam_daily       50 large dams, daily, all with coordinates
    dam_medium     862 medium dams, 857 with coordinates
    dam_small_tele  60 small telemetered dams, NO coordinates at all
    dam_hourly      17 hourly, storage percent is 0 for every one of them

Only the first two can go on a map, so only those are published. The other two are
dropped rather than plotted at a guessed location.

**Zero is a missing-value sentinel here too**, exactly as `min_bank` is on the gauge
feed. 35 of the 50 large dams report `dam_level: 0`, and every one of the 17 hourly rows
reports `dam_storage_percent: 0` while carrying a real level. Published as "no reading"
rather than as an empty reservoir, which is what the number literally says and would be
alarming and wrong.

**Storage percent is of usable capacity, so it exceeds 100.** 88 of the 862 medium dams
are over 100% (max 208%), which is normal in September: it means the reservoir is above
its normal full level and is likely spilling. It is NOT a percentage of a physical
maximum, and it is not comparable to the gauge feed's `storage_percent`, which is a
bed-to-bank channel fill.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from seraphim.adapters.base import dig, fetch_json, ident, num, text
from seraphim.models import SourceHealth

DAM_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/analyst/dam"

#: Groups that carry coordinates and can be put on a map, with the size label each
#: gets in the UI. `dam_small_tele` has no coordinates and `dam_hourly` has no storage
#: percentage, so neither is published.
GROUPS = {"dam_daily": "large", "dam_medium": "medium"}

#: A dam row older than this is history, not a reading.
#:
#: The feed mixes them, exactly as the Dutch gauge feed does. Measured 2026-09-16:
#: `dam_medium` has 448 rows from the last day and then NOTHING until a year out, after
#: which come 317 rows dated to the 1970 epoch and 76 from around 2021. Sorting by
#: fullest-first without this gate put reservoirs last read in 2021 and 2022 at the top
#: of the map, labelled as spilling today. The gap between one day and one year is wide
#: enough that any cutoff inside it behaves identically; seven days is generous for a
#: daily product and unambiguous.
MAX_READING_AGE_DAYS = 7.0

#: Thailand is UTC+7 year-round. `dam_date` is local wall-clock with no offset.
TH_UTC_OFFSET_HOURS = 7.0

#: Above this a reservoir is above its normal full level and is likely spilling.
SPILLING_PERCENT = 100.0
#: Above this it is worth looking at before the rain arrives.
HIGH_PERCENT = 80.0

#: Thailand, generously bounded. Catches 0,0 and swapped coordinates.
LAT_RANGE, LON_RANGE = (5.0, 21.0), (96.0, 106.5)


def _reading(value: object) -> float | None:
    """Parse a dam reading, treating 0 as "not reported".

    Same sentinel discipline as the gauge feed. A dam is not at 0.00% of capacity and
    not at 0.00 m MSL; those are placeholders, and publishing them would show a full
    reservoir as empty or an empty one as full.
    """
    parsed = num(value)
    return None if parsed is None or parsed == 0 else parsed


def _dam_time(value: object) -> datetime | None:
    """Parse `dam_date`, which is Bangkok local and often has no time at all.

    The daily groups publish a bare date ("2026-09-16"); the hourly group publishes
    "2026-09-16 14:00". The shared `parse_local_naive` deliberately refuses a bare date,
    so this handles it locally rather than loosening a parser other adapters rely on
    being strict. A bare date is read as local midnight, which makes the published age
    the honest worst case for that day rather than flattering it.
    """
    raw = text(value)
    if raw is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return (naive - timedelta(hours=TH_UTC_OFFSET_HOURS)).replace(tzinfo=timezone.utc)
    return None


def fetch_dams() -> tuple[dict | None, SourceHealth]:
    health = SourceHealth(source="thaiwater_dam", ok=False)
    try:
        payload = fetch_json(DAM_URL, timeout=90)
    except Exception as exc:  # noqa: BLE001
        health.error = str(exc)
        return None, health

    data = dig(payload, "data")
    if not isinstance(data, dict):
        health.error = "unexpected response shape: no data object"
        return None, health

    features: list[dict] = []
    seen: set[str] = set()
    no_geo = no_reading = too_old = no_time = 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_READING_AGE_DAYS)

    for group, size in GROUPS.items():
        for row in data.get(group) or []:
            if not isinstance(row, dict):
                continue
            dam = row.get("dam") or {}
            dam_id = ident(dam.get("id"))
            lat, lon = num(dam.get("dam_lat")), num(dam.get("dam_long"))
            if dam_id is None:
                continue
            key = f"{size}:{dam_id}"
            if key in seen:
                continue
            if (lat is None or lon is None
                    or not LAT_RANGE[0] <= lat <= LAT_RANGE[1]
                    or not LON_RANGE[0] <= lon <= LON_RANGE[1]):
                no_geo += 1
                continue

            observed_at = _dam_time(row.get("dam_date"))
            if observed_at is None:
                no_time += 1
                continue
            if observed_at < cutoff:
                too_old += 1
                continue

            percent = _reading(row.get("dam_storage_percent"))
            level = _reading(row.get("dam_level"))
            if percent is None and level is None:
                # Nothing to say about this reservoir today.
                no_reading += 1
                continue
            seen.add(key)

            status = (
                "spilling" if percent is not None and percent > SPILLING_PERCENT
                else "high" if percent is not None and percent >= HIGH_PERCENT
                else "normal" if percent is not None
                else "unknown"
            )
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": {
                    "id": f"thaidam:{dam_id}",
                    "name": text(dig(dam, "dam_name", "th")) or f"dam {dam_id}",
                    "name_en": text(dig(dam, "dam_name", "en")),
                    "size": size,
                    # Percent of USABLE capacity, so >100 is normal and means the
                    # reservoir is above its normal full level.
                    "storage_percent": round(percent, 1) if percent is not None else None,
                    "storage_mcm": _reading(row.get("dam_storage")),
                    # Metres above MSL. Absent for most medium dams.
                    "level_msl": round(level, 2) if level is not None else None,
                    "inflow_mcm": _reading(row.get("dam_inflow")),
                    "released_mcm": _reading(row.get("dam_released")),
                    "spilled_mcm": _reading(row.get("dam_spilled")),
                    "status": status,
                    "observed_at": observed_at.isoformat(),
                    "age_hours": round((now - observed_at).total_seconds() / 3600, 1),
                    "basin": text(dig(row, "basin", "basin_name", "th")),
                    "province": text(dig(row, "geocode", "province_name", "th")),
                    "agency": text(dig(row, "agency", "agency_shortname", "th")),
                },
            })

    # Fullest first: the ones worth looking at are the ones near or over capacity.
    features.sort(key=lambda f: -(f["properties"]["storage_percent"] or -1))

    if too_old:
        health.warnings.append(
            f"{too_old} dam rows skipped: last reading older than "
            f"{MAX_READING_AGE_DAYS:g} days (history, not a current reading)")
    if no_time:
        health.warnings.append(f"{no_time} dams skipped: unparseable date")
    if no_geo:
        health.warnings.append(f"{no_geo} dams skipped: missing/implausible coordinates")
    if no_reading:
        health.warnings.append(
            f"{no_reading} dams skipped: neither a storage percentage nor a level "
            f"(both reported as the sentinel 0)")
    spilling = sum(1 for f in features if f["properties"]["status"] == "spilling")
    health.warnings.append(
        f"{spilling} reservoirs above their normal full level (storage > 100% of "
        f"usable capacity, which means spilling, not overflowing a dam wall)")

    health.ok = bool(features)
    health.stations = len(features)
    health.fetched_at = datetime.now(timezone.utc)
    if not features:
        health.error = "no usable dams"
        return None, health

    return ({
        "type": "FeatureCollection",
        "attribution": "ThaiWater / HII, National Hydroinformatics Data Center",
        "note": ("storage_percent is percent of USABLE capacity; above 100 means the "
                 "reservoir is above its normal full level and likely spilling"),
        "features": features,
    }, health)
