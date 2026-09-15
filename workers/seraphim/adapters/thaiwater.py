"""ThaiWater / HII, Thailand's national hydroinformatics telemetry network.

~1,121 live gauges, keyless. Verified 2026-09-15, see docs/DATA_SOURCES.md.

Quirks this adapter absorbs so nothing downstream has to know about them:
  * numbers arrive as strings ("1.29"), and freely as null or ""
  * timestamps are naive Bangkok local wall-clock ("2026-09-15 14:00"), UTC+7
  * bank levels live nested under `station`, the reading sits at the top level
  * `waterlevel_m` and `waterlevel_msl` are different datums; `waterlevel_m` is usually null
  * the source publishes its own freeboard as `diff_wl_bank`, we cross-check against it

ATTRIBUTION / LICENCE: terms of use are not yet confirmed with HII. Required before any
public launch. See docs/DATA_SOURCES.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from seraphim.adapters.base import (
    SourceAdapter,
    dig,
    fetch_json,
    ident,
    num,
    parse_local_naive,
    register,
    text,
)
from seraphim.models import Admin, Observation, SourceHealth, Station

BASE = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"
WATERLEVEL_URL = f"{BASE}/waterlevel"

#: Thailand is UTC+7 year-round, no DST. Source timestamps are local wall-clock.
TH_UTC_OFFSET_HOURS = 7.0

#: The source reports freeboard as an unsigned magnitude (`diff_wl_bank`) plus a Thai
#: direction string (`diff_wl_bank_text`). The direction is the part worth checking: a
#: sign error would invert "safe" and "overtopped", which is the worst failure this
#: system could have. Verified 2026-09-15: 1121/1121 signs agreed.
OVERFLOW_TEXT_PREFIX = "\u0e25\u0e49\u0e19"  # "ล้น…", overflowing
BELOW_TEXT_PREFIX = "\u0e15\u0e48\u0e33\u0e01\u0e27\u0e48\u0e32"  # "ต่ำกว่า…", below


class ThaiWaterAdapter(SourceAdapter):
    id = "thaiwater"
    attribution = "Hydro-Informatics Institute (HII) / ThaiWater, thaiwater.net"
    country = "TH"

    def fetch(self) -> tuple[list[Station], list[Observation], SourceHealth]:
        health = SourceHealth(source=self.id, ok=False)
        try:
            payload = fetch_json(WATERLEVEL_URL)
        except Exception as exc:  # noqa: BLE001, a dead source must not stop the cycle
            health.error = str(exc)
            return [], [], health

        rows = dig(payload, "data")
        if not isinstance(rows, list):
            health.error = f"unexpected payload shape: {type(payload).__name__} without 'data' list"
            return [], [], health

        stations: list[Station] = []
        observations: list[Observation] = []
        seen: set[str] = set()
        skipped_no_geo = 0
        skipped_no_time = 0
        crosscheck_failures = 0

        for row in rows:
            if not isinstance(row, dict):
                continue
            st_raw = row.get("station")
            if not isinstance(st_raw, dict):
                continue

            lat = num(st_raw.get("tele_station_lat"))
            lon = num(st_raw.get("tele_station_long"))
            if lat is None or lon is None or not _plausible_th(lat, lon):
                # A gauge we cannot place cannot be mapped or joined to terrain.
                skipped_no_geo += 1
                continue

            # `station.id` is the stable gauge identifier. `row.id` is per-reading and
            # changes every cycle, using it would create a new station every 30 minutes.
            external_id = ident(st_raw.get("id"))
            if external_id is None or external_id in seen:
                continue
            seen.add(external_id)

            observed_at = parse_local_naive(row.get("waterlevel_datetime"), TH_UTC_OFFSET_HOURS)
            if observed_at is None:
                # A reading with no trustworthy time cannot be aged or trended.
                skipped_no_time += 1
                continue

            # `min_bank` is the lower of the two banks, the level at which water first
            # leaves the channel. That is the threshold that matters for flooding.
            bank_msl = num(st_raw.get("min_bank"))
            if bank_msl is None:
                left, right = num(st_raw.get("left_bank")), num(st_raw.get("right_bank"))
                candidates = [b for b in (left, right) if b is not None]
                bank_msl = min(candidates) if candidates else None

            station = Station(
                source=self.id,
                external_id=external_id,
                name=text(dig(st_raw, "tele_station_name", "th")) or f"station {external_id}",
                name_en=text(dig(st_raw, "tele_station_name", "en")),
                lat=lat,
                lon=lon,
                bank_msl=bank_msl,
                critical_msl=num(st_raw.get("critical_level_msl")),
                ground_msl=num(st_raw.get("ground_level")),
                basin=text(dig(row, "basin", "basin_name", "th")),
                agency=text(dig(row, "agency", "agency_shortname", "th")),
                admin=_admin(row.get("geocode")),
            )

            level_msl = num(row.get("waterlevel_msl"))
            observation = Observation(
                station_id=station.id,
                observed_at=observed_at,
                level_msl=level_msl,
                level_msl_previous=num(row.get("waterlevel_msl_previous")),
                discharge_cms=num(row.get("discharge")) or num(row.get("flow_rate")),
                storage_percent=num(row.get("storage_percent")),
                source_severity=_severity(row.get("situation_level")),
            )

            # Cross-check the *sign* of our freeboard against the source's own direction
            # text. Inverting this would turn "overtopped" into "safe", so it is checked
            # on every row rather than trusted.
            if bank_msl is not None and level_msl is not None:
                direction = text(row.get("diff_wl_bank_text")) or ""
                if direction.startswith((OVERFLOW_TEXT_PREFIX, BELOW_TEXT_PREFIX)):
                    source_says_below = direction.startswith(BELOW_TEXT_PREFIX)
                    we_say_below = (bank_msl - level_msl) > 0
                    if source_says_below != we_say_below:
                        crosscheck_failures += 1

            stations.append(station)
            observations.append(observation)

        if skipped_no_geo:
            health.warnings.append(f"{skipped_no_geo} rows skipped: missing/implausible coordinates")
        if skipped_no_time:
            health.warnings.append(f"{skipped_no_time} rows skipped: unparseable timestamp")
        if crosscheck_failures:
            health.warnings.append(
                f"{crosscheck_failures} stations: our freeboard SIGN disagrees with the "
                f"source's own direction text, datum mismatch, treat output as suspect"
            )

        health.ok = bool(stations)
        health.stations = len(stations)
        health.observations = len(observations)
        health.fetched_at = datetime.now(timezone.utc)
        if not stations:
            health.error = "source returned no usable stations"
        return stations, observations, health


def _plausible_th(lat: float, lon: float) -> bool:
    """Thailand's bounding box, generously padded. Catches 0,0 and swapped lat/lon."""
    return 5.0 <= lat <= 21.0 and 96.0 <= lon <= 106.5


def _severity(value: object) -> int | None:
    n = num(value)
    if n is None:
        return None
    i = int(n)
    return i if 1 <= i <= 5 else None


def _admin(geocode: object) -> Admin | None:
    if not isinstance(geocode, dict):
        return Admin(country="TH")
    return Admin(
        country="TH",
        province=text(dig(geocode, "province_name", "th")),
        province_code=ident(geocode.get("province_code")),
        district=text(dig(geocode, "amphoe_name", "th")),
        district_code=ident(geocode.get("amphoe_code")),
        subdistrict=text(dig(geocode, "tumbon_name", "th")),
        subdistrict_code=ident(geocode.get("tumbon_code")),
    )


register(ThaiWaterAdapter())
