"""Canonical data model.

Every adapter converts its source into these types. Nothing downstream ever sees a
source-specific field, which is what lets a second country plug in without a rewrite.

Datum rule: all water levels here are **metres above mean sea level (m MSL)**. Conversion
happens in the adapter, never later. Mixing m and m-MSL yields plausible, wrong, dangerous
numbers, so the field names are explicit about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class Admin:
    """Administrative location. Names are whatever the source provides, in its own language."""

    country: str  # ISO-3166 alpha-2, e.g. "TH"
    province: str | None = None
    province_code: str | None = None
    district: str | None = None  # amphoe in TH
    district_code: str | None = None
    subdistrict: str | None = None  # tambon in TH
    subdistrict_code: str | None = None


@dataclass(frozen=True, slots=True)
class Station:
    """A fixed water-level gauge. Metadata that changes rarely."""

    source: str  # adapter id, e.g. "thaiwater"
    external_id: str  # id within that source
    name: str  # local-language name as given
    lat: float
    lon: float

    name_en: str | None = None
    #: Overtopping threshold — water above this leaves the channel. The single most
    #: important number in the system after the level itself.
    bank_msl: float | None = None
    #: Source-declared critical level, where one exists. Kept separate from bank_msl
    #: because sources do not always agree with themselves.
    critical_msl: float | None = None
    ground_msl: float | None = None
    basin: str | None = None
    agency: str | None = None
    admin: Admin | None = None

    @property
    def id(self) -> str:
        """Globally unique id. Namespaced so two sources can never collide."""
        return f"{self.source}:{self.external_id}"


@dataclass(frozen=True, slots=True)
class Observation:
    """One reading from one station at one time."""

    station_id: str
    #: Always UTC. Sources hand us naive local strings; adapters localise then convert.
    observed_at: datetime

    level_msl: float | None = None
    #: The source's own previous reading. Retained for cross-checking only.
    #: NOT used to derive a rate of rise: the interval between it and level_msl is
    #: undocumented, and a rate computed over an assumed interval would be a guess
    #: presented as a measurement. Rate comes from our own stored history (Phase 2).
    level_msl_previous: float | None = None
    discharge_cms: float | None = None
    storage_percent: float | None = None
    #: Source's own severity rating, where provided. A cross-check on our scoring,
    #: never silently overridden.
    source_severity: int | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError(
                f"observed_at must be timezone-aware (station {self.station_id}); "
                "adapters localise at the source boundary"
            )


@dataclass(frozen=True, slots=True)
class StationState:
    """A station joined to its latest reading, plus what we can honestly derive right now."""

    station: Station
    observation: Observation
    generated_at: datetime

    @property
    def freeboard_m(self) -> float | None:
        """Metres of headroom below the bank. Negative means the bank is overtopped."""
        if self.station.bank_msl is None or self.observation.level_msl is None:
            return None
        return round(self.station.bank_msl - self.observation.level_msl, 3)

    @property
    def data_age_minutes(self) -> float:
        """How stale this reading is. Displayed everywhere; never hidden."""
        delta = self.generated_at - self.observation.observed_at
        return round(delta.total_seconds() / 60.0, 1)

    @property
    def is_stale(self) -> bool:
        """A gauge that stopped reporting during a flood is a signal, not a gap."""
        return self.data_age_minutes > STALE_AFTER_MINUTES


#: Beyond this, a reading is flagged stale in the UI rather than shown as current.
STALE_AFTER_MINUTES = 180.0


@dataclass(slots=True)
class SourceHealth:
    """Per-source outcome of a fetch cycle. Published so failures are visible, not silent."""

    source: str
    ok: bool
    stations: int = 0
    observations: int = 0
    error: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Non-fatal problems: unparseable rows, missing datums, cross-check disagreements.
    warnings: list[str] = field(default_factory=list)
