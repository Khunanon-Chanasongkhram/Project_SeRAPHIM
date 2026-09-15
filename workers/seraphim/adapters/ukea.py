"""UK Environment Agency flood monitoring: the second country.

Keyless, open, ~4,200 level stations, and two bulk calls for the lot. It exists here to
prove the SourceAdapter seam does what Phase 0 claimed, and it immediately found the
assumption hiding in that claim.

**The UK publishes no bank level.** It publishes `typicalRangeHigh`, the top of a
station's normal operating range. Those are not the same thing and treating them as the
same would have the map announcing that 4,000 British rivers had burst their banks
whenever they ran high. So UK stations get a weaker, honestly-labelled signal: above or
below typical range, no freeboard, no time-to-bank, and never a level 5.

**Its levels are not in one datum either.** The feed mixes mASD (above the station's own
datum), mAOD (above Ordnance Datum, roughly MSL), plain m and mBDAT, and leaks a few
readings in mm, deg_C and m3/s. Rather than guess a conversion, levels are marked
`datum="local"` and only ever compared with thresholds from the same station.
"""

from __future__ import annotations

from datetime import datetime, timezone

from seraphim.adapters.base import (
    SourceAdapter,
    fetch_json,
    ident,
    num,
    register,
    text,
)
from seraphim.models import Admin, Observation, SourceHealth, Station

BASE = "https://environment.data.gov.uk/flood-monitoring"
STATIONS_URL = f"{BASE}/id/stations?parameter=level&_view=full&_limit=10000"
MEASURES_URL = f"{BASE}/id/measures?parameter=level&_limit=10000"

#: Only these are a river level. The feed also carries rainfall, temperature and flow.
LEVEL_UNITS = {"mASD", "mAOD", "m", "mBDAT"}
#: Downstream stage at a structure is a different thing from the river level upstream.
PREFERRED_QUALIFIER = "Stage"


class UKEnvironmentAgencyAdapter(SourceAdapter):
    id = "ukea"
    attribution = "UK Environment Agency flood monitoring (Open Government Licence)"
    country = "GB"

    def fetch(self) -> tuple[list[Station], list[Observation], SourceHealth]:
        health = SourceHealth(source=self.id, ok=False)
        try:
            stations_doc = fetch_json(STATIONS_URL, timeout=90)
            measures_doc = fetch_json(MEASURES_URL, timeout=90)
        except Exception as exc:  # noqa: BLE001
            health.error = str(exc)
            return [], [], health

        # --- pick one measure per station, preferring plain Stage ---
        latest: dict[str, dict] = {}
        skipped_units = 0
        for m in measures_doc.get("items", []):
            unit = text(m.get("unitName"))
            if unit not in LEVEL_UNITS:
                skipped_units += 1
                continue
            ref = ident(m.get("stationReference")) or _station_ref(m.get("station"))
            reading = m.get("latestReading")
            if not ref or not isinstance(reading, dict):
                continue
            value = num(reading.get("value"))
            if value is None:
                continue
            qualifier = text(m.get("qualifier")) or ""
            current = latest.get(ref)
            if current and current["qualifier"] == PREFERRED_QUALIFIER and qualifier != PREFERRED_QUALIFIER:
                continue
            latest[ref] = {"value": value, "unit": unit, "qualifier": qualifier,
                           "at": text(reading.get("dateTime"))}

        stations: list[Station] = []
        observations: list[Observation] = []
        no_geo = no_reading = no_time = 0

        for item in stations_doc.get("items", []):
            ref = ident(item.get("stationReference")) or ident(item.get("notation"))
            lat, lon = num(item.get("lat")), num(item.get("long"))
            if not ref:
                continue
            if lat is None or lon is None or not _plausible_gb(lat, lon):
                no_geo += 1
                continue
            reading = latest.get(ref)
            if reading is None:
                no_reading += 1
                continue
            observed_at = _parse_iso(reading["at"])
            if observed_at is None:
                no_time += 1
                continue

            scale = item.get("stageScale")
            scale = scale if isinstance(scale, dict) else {}
            name = text(item.get("label")) or f"station {ref}"
            river = text(item.get("riverName"))

            stations.append(Station(
                source=self.id,
                external_id=ref,
                name=f"{name}{f' ({river})' if river else ''}",
                name_en=name,
                lat=lat, lon=lon,
                # No bank level exists in this feed. Leaving it None is the whole point:
                # every downstream freeboard and time-to-bank check already handles it.
                bank_msl=None,
                datum="local",
                typical_high=num(scale.get("typicalRangeHigh")),
                typical_low=num(scale.get("typicalRangeLow")),
                record_high=num(scale.get("maxOnRecord")),
                basin=text(item.get("catchmentName")),
                agency="Environment Agency",
                admin=Admin(country="GB", province=text(item.get("town")) or river,
                            district=text(item.get("catchmentName"))),
            ))
            observations.append(Observation(
                station_id=stations[-1].id,
                observed_at=observed_at,
                level_msl=reading["value"],
            ))

        for label, count in (("missing/implausible coordinates", no_geo),
                             ("no latest reading", no_reading),
                             ("unparseable timestamp", no_time)):
            if count:
                health.warnings.append(f"{count} stations skipped: {label}")
        if skipped_units:
            health.warnings.append(
                f"{skipped_units} measures ignored: not a river level (rainfall, flow, temperature)")
        with_scale = sum(1 for s in stations if s.typical_high is not None)
        health.warnings.append(
            f"{len(stations) - with_scale} of {len(stations)} have no typical range, "
            "so they carry a level and a trend but no threshold to judge it against")

        health.ok = bool(stations)
        health.stations = len(stations)
        health.observations = len(observations)
        health.fetched_at = datetime.now(timezone.utc)
        if not stations:
            health.error = "no usable stations"
        return stations, observations, health


def _station_ref(url: object) -> str | None:
    s = text(url)
    return s.rstrip("/").rsplit("/", 1)[-1] if s else None


def _plausible_gb(lat: float, lon: float) -> bool:
    """Great Britain plus a margin. Catches 0,0 and swapped coordinates."""
    return 49.5 <= lat <= 61.0 and -9.0 <= lon <= 2.5


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


register(UKEnvironmentAgencyAdapter())
