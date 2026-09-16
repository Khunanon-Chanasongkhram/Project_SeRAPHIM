"""Netherlands, Rijkswaterstaat: the fourth country, and the one that punishes
trusting a field name.

Keyless, open, ~700 water-level locations, one catalogue call plus one bulk POST.

**The old host is gone.** `waterwebservices.rijkswaterstaat.nl` now 301s to a migration
notice (which itself 404s). The live service is DDAPI 2.0 at
`ddapi20-waterwebservices.rijkswaterstaat.nl`, found from the Rijkswaterstaat open-data
pages and probed before anything here was written.

**"OphalenLaatsteWaarnemingen" means "the latest value of every series", not "current
readings".** Probed 2026-09-16: of 2,244 series returned, the oldest "latest" reading is
dated **1740-01-01**, and the median is about 27 years old. Rijkswaterstaat keeps
historical series in the same endpoint as live telemetry and distinguishes them only by
timestamp. Publishing that unfiltered would have put 286-year-old marks on a live flood
map, every one of them looking like a perfectly ordinary reading. So this adapter
applies a hard recency gate: a series whose newest measurement is older than
MAX_READING_AGE_HOURS is not a working gauge and is dropped, not shown as stale.
After the gate: **318 live locations, median age 22 minutes**, which is fresher than
any other source in this project.

**One location, up to 18 series.** Different measuring methods run in parallel, and they
are not equivalent: alongside "arithmetic mean over the previous 5 and next 5 minutes"
sits "Visuele aflezing van blad" (visual reading off a board) and "Indompeling gedurende
1 minuut" (one-minute immersion). The newest measurement per location wins.

**Centimetres.** Every water height here is in cm, converted to metres at this boundary
per the datum rule. Shipping cm into a system whose contract is metres would read as a
river a hundred times deeper than it is.

**Four vertical datums, as in the UK.** NAP (Normaal Amsterdams Peil, the Dutch national
datum, within a few cm of mean sea level), MSL for the North Sea platforms, TAW (the
Belgian datum, about 2.33 m below NAP) and PLAATSLR (a local reference). TAW and
PLAATSLR are marked `local` rather than offset onto NAP, because an approximate
conversion nobody here has verified is worse than declining to compare.

**No bank level exists in this feed**, so, like the UK, Dutch gauges carry a level and a
trend, never a freeboard, a time-to-bank or a level 5. The risk engine already handles
that, keyed on the absence of a threshold rather than on a country name.

**A plausibility gate that nearly threw away real data.** The fresh values span -146 cm
to 11,968 cm NAP, and 119.68 m looked like an obvious error in a country famous for
being flat. It is not: `epen.geul.cottessen` gauges the Geul in South Limburg, where the
valley floor genuinely sits above 100 m. The gate is therefore set against Dutch terrain
(-7 m in the Zuidplaspolder to 322 m at the Vaalserberg), not against the stereotype.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from seraphim import cache
from seraphim.adapters.base import (
    SourceAdapter,
    num,
    post_json,
    register,
    text,
)
from seraphim.models import Admin, Observation, SourceHealth, Station

BASE = "https://ddapi20-waterwebservices.rijkswaterstaat.nl"
CATALOGUE_URL = f"{BASE}/METADATASERVICES/OphalenCatalogus"
LATEST_URL = f"{BASE}/ONLINEWAARNEMINGENSERVICES/OphalenLaatsteWaarnemingen"

#: Water height in surface water. "Grootheid" is the measured quantity.
QUANTITY = "WATHTE"
COMPARTMENT = "OW"
UNIT_CM = "cm"

#: Vertical datums this source publishes water heights against, mapped onto our own
#: vocabulary. NAP and MSL are nationally comparable; the other two are not.
DATUMS: dict[str, str] = {
    "NAP": "NAP",
    "MSL": "MSL",
    "TAW": "local",
    "PLAATSLR": "local",
}

#: A series whose newest reading is older than this is a historical archive, not a
#: gauge. See the module docstring: without this, readings from 1740 reach the map.
MAX_READING_AGE_HOURS = 24.0

#: The catalogue is 7.3 MB of station metadata that changes rarely. Refetching it every
#: 15-minute build would pull ~700 MB a day off a public service to learn nothing.
CATALOGUE_CACHE_HOURS = 24.0

CM_TO_M = 0.01
#: Values at or beyond this are RWS "no data" sentinels, not measurements.
SENTINEL = 1e5
#: Dutch terrain, generously bounded: -7 m NAP in the Zuidplaspolder to 322 m at the
#: Vaalserberg. Wide on purpose, it exists to catch sentinels and unit mistakes, not to
#: second-guess a hydrologist.
MIN_LEVEL_M, MAX_LEVEL_M = -20.0, 350.0


class RijkswaterstaatAdapter(SourceAdapter):
    id = "rws"
    attribution = "Rijkswaterstaat Waterinfo (CC0 / Dutch open data)"
    country = "NL"

    def fetch(self) -> tuple[list[Station], list[Observation], SourceHealth]:
        health = SourceHealth(source=self.id, ok=False)
        try:
            locations = self._catalogue(health)
        except Exception as exc:  # noqa: BLE001
            health.error = str(exc)
            return [], [], health
        if not locations:
            health.error = "catalogue returned no water-level locations"
            return [], [], health

        try:
            doc = post_json(LATEST_URL, {
                "AquoPlusWaarnemingMetadataLijst": [
                    {"AquoMetadata": {
                        "Compartiment": {"Code": COMPARTMENT},
                        "Grootheid": {"Code": QUANTITY},
                        "Hoedanigheid": {"Code": code},
                        "Eenheid": {"Code": UNIT_CM},
                    }} for code in DATUMS
                ],
                "LocatieLijst": [
                    {"X": lon, "Y": lat, "Code": code}
                    for code, (_, lat, lon) in sorted(locations.items())
                ],
            }, timeout=240)
        except Exception as exc:  # noqa: BLE001
            health.error = str(exc)
            return [], [], health

        if not isinstance(doc, dict) or not doc.get("Succesvol", False):
            health.error = f"service reported failure: {str(doc)[:200]}"
            return [], [], health

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=MAX_READING_AGE_HOURS)
        best: dict[str, tuple[datetime, float, str]] = {}
        too_old = sentinel = implausible = no_time = 0

        for series in doc.get("WaarnemingenLijst") or []:
            if not isinstance(series, dict):
                continue
            loc = series.get("Locatie") or {}
            code = text(loc.get("Code"))
            hoedanigheid = text(
                ((series.get("AquoMetadata") or {}).get("Hoedanigheid") or {}).get("Code"))
            if not code or hoedanigheid not in DATUMS:
                continue

            for m in series.get("MetingenLijst") or []:
                if not isinstance(m, dict):
                    continue
                at = _parse_iso(text(m.get("Tijdstip")))
                if at is None:
                    no_time += 1
                    continue
                if at < cutoff:
                    too_old += 1
                    continue
                raw_cm = num((m.get("Meetwaarde") or {}).get("Waarde_Numeriek"))
                if raw_cm is None or abs(raw_cm) >= SENTINEL:
                    sentinel += 1
                    continue
                level_m = round(raw_cm * CM_TO_M, 3)
                if not MIN_LEVEL_M <= level_m <= MAX_LEVEL_M:
                    implausible += 1
                    continue
                current = best.get(code)
                if current is None or at > current[0]:
                    best[code] = (at, level_m, DATUMS[hoedanigheid])

        stations: list[Station] = []
        observations: list[Observation] = []
        for code, (at, level_m, datum) in sorted(best.items()):
            name, lat, lon = locations[code]
            stations.append(Station(
                source=self.id,
                external_id=code,
                name=name,
                name_en=name,
                lat=lat, lon=lon,
                # Rijkswaterstaat publishes no overtopping threshold in this API, and
                # none is invented from a neighbour. Level and trend only.
                bank_msl=None,
                datum=datum,
                basin=None,
                agency="Rijkswaterstaat",
                # The MSL-datum locations are North Sea platforms (K13a, A12,
                # Hollandse Kust), not river gauges: what they measure is the tide.
                # Before this they were publishing a "rising 13 cm/h" trend and a
                # 12-hour projection of +1.2 m, which is a flooding tide read as a
                # flood. The NAP-datum inland stations are left alone.
                tidal=(datum == "MSL"),
                kind=("sea" if datum == "MSL" else "river"),
                admin=Admin(country="NL", province=_place_from_code(code)),
            ))
            observations.append(Observation(
                station_id=stations[-1].id,
                observed_at=at,
                level_msl=level_m,
            ))

        for label, count in (
                (f"latest reading older than {MAX_READING_AGE_HOURS:g} h "
                 "(historical series, not a live gauge)", too_old),
                ("no-data sentinel or unparseable value", sentinel),
                ("level outside plausible Dutch range", implausible),
                ("unparseable timestamp", no_time)):
            if count:
                health.warnings.append(f"{count} measurements skipped: {label}")
        health.warnings.append(
            f"{len(stations)} of {len(locations)} catalogued locations are reporting; "
            "none has a published bank level, so all carry a level and a trend only")

        health.ok = bool(stations)
        health.stations = len(stations)
        health.observations = len(observations)
        health.fetched_at = now
        if not stations:
            health.error = "no location reported within the freshness window"
        return stations, observations, health

    # -- catalogue ---------------------------------------------------------

    def _catalogue(self, health: SourceHealth) -> dict[str, tuple[str, float, float]]:
        """{location code: (name, lat, lon)} for everything measuring water height.

        Cached on disk: this is which gauges exist, not what they read.
        """
        root = self.cache_root
        if root is not None:
            hit = cache.load(Path(root), f"{self.id}_catalogue", CATALOGUE_CACHE_HOURS)
            if hit is not None:
                return {k: (v[0], v[1], v[2]) for k, v in hit["data"].items()}

        doc = post_json(CATALOGUE_URL, {
            "CatalogusFilter": {"Grootheden": True, "Parameters": True,
                                "Eenheden": True, "Compartimenten": True,
                                "Hoedanigheden": True},
        }, timeout=180)
        if not isinstance(doc, dict):
            raise RuntimeError(f"unexpected catalogue shape from {CATALOGUE_URL}")

        # Which metadata ids describe a water height, in a datum we accept.
        wanted = {
            meta.get("AquoMetadata_MessageID")
            for meta in doc.get("AquoMetadataLijst") or []
            if isinstance(meta, dict)
            and text((meta.get("Grootheid") or {}).get("Code")) == QUANTITY
            and text((meta.get("Hoedanigheid") or {}).get("Code")) in DATUMS
        }
        by_id = {
            loc.get("Locatie_MessageID"): loc
            for loc in doc.get("LocatieLijst") or []
            if isinstance(loc, dict)
        }

        out: dict[str, tuple[str, float, float]] = {}
        for pair in doc.get("AquoMetadataLocatieLijst") or []:
            if not isinstance(pair, dict) or pair.get("AquoMetaData_MessageID") not in wanted:
                continue
            loc = by_id.get(pair.get("Locatie_MessageID"))
            if not loc:
                continue
            code = text(loc.get("Code"))
            lat, lon = num(loc.get("Lat")), num(loc.get("Lon"))
            if not code or lat is None or lon is None or not _plausible_nl(lat, lon):
                continue
            out.setdefault(code, (text(loc.get("Naam")) or code, lat, lon))

        if root is not None:
            cache.save(Path(root), f"{self.id}_catalogue",
                       {k: list(v) for k, v in out.items()})
        health.warnings.append(f"catalogue: {len(out)} water-level locations")
        return out


def _parse_iso(value: str | None) -> datetime | None:
    """Parse '2026-09-16T02:50:00.000+01:00'. The offset is always present here."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _place_from_code(code: str) -> str | None:
    """Group by the place name that RWS codes already start with.

    Codes read like 'dronten.roggebotsluis.vossemeer' or 'werkendam.kooike', so the
    first segment is a town. This is a grouping label derived from the station code, NOT
    an official boundary: the API publishes no administrative geography at all, and
    inventing one would be worse than a rough grouping that is labelled as such.
    """
    head = code.split(".")[0].strip()
    return head.replace("-", " ").title() if head else None


def _plausible_nl(lat: float, lon: float) -> bool:
    """The Netherlands plus its North Sea waters. Catches 0,0 and swapped coordinates."""
    return 50.5 <= lat <= 56.0 and 2.5 <= lon <= 7.5


register(RijkswaterstaatAdapter())
