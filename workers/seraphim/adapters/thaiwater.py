"""ThaiWater / HII, Thailand's national hydroinformatics telemetry network.

~1,121 live gauges, keyless. Verified 2026-09-15, see docs/DATA_SOURCES.md.

Quirks this adapter absorbs so nothing downstream has to know about them:
  * numbers arrive as strings ("1.29"), and freely as null or ""
  * timestamps are naive Bangkok local wall-clock ("2026-09-15 14:00"), UTC+7
  * bank levels live nested under `station`, the reading sits at the top level
  * `waterlevel_m` and `waterlevel_msl` are different datums; `waterlevel_m` is usually null
  * the source publishes its own freeboard as `diff_wl_bank`, we cross-check against it
  * **`min_bank == 0` means "no bank level published", not "the bank is at 0 m MSL"**

THE ZERO-BANK TRAP (found 2026-09-16, after it had been shipping wrong numbers).
318 of 1,118 stations carry `min_bank: 0`. That is a missing-value sentinel, and the
source does not handle it either: for those rows `diff_wl_bank` is simply the water
level (a gauge reading 164.89 m MSL gets `diff_wl_bank: 164.89`) and
`diff_wl_bank_text` says "ล้นตลิ่ง", overflowing, for every one of them whose level is
above zero. That is 302 stations announced as overtopped purely for sitting above sea
level.

Taking `min_bank` at face value made this map show ~308 Thai rivers over their banks
when thaiwater.net showed a normal monsoon. Of the 800 stations with a real bank level,
**6** are actually over it.

The 2026-09-15 "1,121/1,121 signs agreed" cross-check did not catch this because it was
circular: our freeboard and the source's `diff_wl_bank_text` are both computed from the
same `min_bank`, so both were wrong in the same direction and agreed perfectly. The
independent signal is `storage_percent`, which the source declines to publish at all for
these stations, and which is now cross-checked separately below.

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
#: Thai terrain, generously bounded: the delta sits near 0 m and Doi Inthanon is
#: 2,565 m. Wide on purpose, this catches sensor faults, not unusual hydrology.
MIN_LEVEL_MSL, MAX_LEVEL_MSL = -20.0, 2600.0

#: How far a reading may sit below the station's own surveyed bed before it is refused.
#: Water cannot pool far under the river bed, so this is a physical impossibility rather
#: than a magic number. Measured 2026-09-16 across 993 stations that publish both: the
#: four readings of exactly -9.99 sit 7.7 to 23.4 m below their bed, while the worst
#: genuine reading is 1.84 m below and comes with a negative `storage_percent`, which
#: the source publishes quite deliberately for a channel drying below its surveyed bed.
#: A 2 m allowance separates the sentinels from the real dry channels cleanly.
MAX_BELOW_BED_M = 2.0

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
        storage_disagreements = 0
        implausible_level = 0
        no_bank = 0

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
            #
            # Zero is read as "not published", never as a bank at 0 m MSL: see the
            # module docstring. A station with no threshold gets None and takes the
            # same path as a UK or Dutch gauge, a level and a trend and no freeboard,
            # which is honest. Treating the sentinel as a number instead announced 302
            # Thai rivers as overtopped because they sit above sea level.
            bank_msl = threshold(st_raw.get("min_bank"))
            if bank_msl is None:
                left = threshold(st_raw.get("left_bank"))
                right = threshold(st_raw.get("right_bank"))
                candidates = [b for b in (left, right) if b is not None]
                bank_msl = min(candidates) if candidates else None
            if bank_msl is None:
                no_bank += 1

            station = Station(
                source=self.id,
                external_id=external_id,
                name=text(dig(st_raw, "tele_station_name", "th")) or f"station {external_id}",
                name_en=text(dig(st_raw, "tele_station_name", "en")),
                lat=lat,
                lon=lon,
                bank_msl=bank_msl,
                critical_msl=threshold(st_raw.get("critical_level_msl")),
                ground_msl=threshold(st_raw.get("ground_level")),
                basin=text(dig(row, "basin", "basin_name", "th")),
                agency=text(dig(row, "agency", "agency_shortname", "th")),
                admin=_admin(row.get("geocode")),
            )

            level_msl = num(row.get("waterlevel_msl"))
            # Refuse impossible readings rather than publishing them as facts. A gauge
            # reporting -875.7 m MSL, or -9.99 m when its own bed is at +13.42, is a
            # broken sensor. Left as None, the station shows "no reading", which is what
            # it has. Kept rather than dropped, because a gauge that has stopped telling
            # the truth is itself worth seeing on the map.
            if level_msl is not None:
                ground = threshold(st_raw.get("ground_level"))
                impossible = not (MIN_LEVEL_MSL <= level_msl <= MAX_LEVEL_MSL)
                if ground is not None and level_msl < ground - MAX_BELOW_BED_M:
                    impossible = True
                if impossible:
                    implausible_level += 1
                    level_msl = None

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
            # The source's own direction text is only an oracle where the source had a
            # real bank level to compute it from. Where `min_bank` is the sentinel 0 it
            # says "overflowing" for any gauge above sea level, so comparing against it
            # there would report ~290 disagreements and bury the ones that matter.
            source_bank = threshold(st_raw.get("min_bank"))
            if bank_msl is not None and level_msl is not None and source_bank is not None:
                direction = text(row.get("diff_wl_bank_text")) or ""
                if direction.startswith((OVERFLOW_TEXT_PREFIX, BELOW_TEXT_PREFIX)):
                    source_says_below = direction.startswith(BELOW_TEXT_PREFIX)
                    we_say_below = (bank_msl - level_msl) > 0
                    if source_says_below != we_say_below:
                        crosscheck_failures += 1

                # Independent check. `storage_percent` is the source's own bed-to-bank
                # fill fraction, and it is the signal that exposed the zero-bank trap:
                # the source withholds it exactly where it has no real bank level. If
                # we call a station overtopped, the source's own percentage should agree
                # it is past 100. Unlike diff_wl_bank_text this is not computed from the
                # field we are trying to validate alone, so it can actually disagree.

            # Independent check, and the one that would have caught the zero-bank trap.
            # `storage_percent` is the source's own bed-to-bank fill fraction, and it is
            # withheld exactly where there is no real bank. If we call a station over
            # bank, the source's own percentage should agree it is past 100.
            pct = num(row.get("storage_percent"))
            if (bank_msl is not None and level_msl is not None and pct is not None
                    and (bank_msl - level_msl) <= 0 and pct <= 100):
                storage_disagreements += 1

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
        if storage_disagreements:
            # The check that would have caught the zero-bank trap on day one.
            health.warnings.append(
                f"{storage_disagreements} stations: we call them over bank but the "
                f"source's own storage_percent is at or below 100, treat as suspect"
            )
        if implausible_level:
            health.warnings.append(
                f"{implausible_level} readings refused as physically impossible "
                f"(outside {MIN_LEVEL_MSL:g}..{MAX_LEVEL_MSL:g} m MSL, or more than "
                f"{MAX_BELOW_BED_M:g} m below the station's own bed)"
            )
        if no_bank:
            health.warnings.append(
                f"{no_bank} of {len(stations)} publish no bank level (min_bank is the "
                f"sentinel 0), so they carry a level and a trend but no freeboard and "
                f"no time-to-bank"
            )

        health.ok = bool(stations)
        health.stations = len(stations)
        health.observations = len(observations)
        health.fetched_at = datetime.now(timezone.utc)
        if not stations:
            health.error = "source returned no usable stations"
        return stations, observations, health


def threshold(value: object) -> float | None:
    """Parse a threshold level, reading the sentinel 0 as "not published".

    ThaiWater uses a literal 0 for thresholds it does not have, for `min_bank`,
    `left_bank`, `right_bank`, `critical_level_msl` and `ground_level` alike. Zero is
    never a real threshold in this network: it is metres above mean sea level, and a
    gauge whose bank genuinely sat at 0.00 m MSL would be a tidal outfall, not the 318
    inland stations that carry it. The source agrees, it refuses to publish a
    `storage_percent` for any of them.

    Reading it as a number is what made the map announce 302 Thai rivers as overtopped
    for the offence of sitting above sea level.
    """
    parsed = num(value)
    return None if parsed is None or parsed == 0 else parsed


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
