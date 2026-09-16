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
    #: Overtopping threshold, water above this leaves the channel. The single most
    #: important number in the system after the level itself.
    bank_msl: float | None = None
    #: What the level numbers actually mean. "MSL" is metres above mean sea level and is
    #: comparable between stations. "local" is a station-relative datum, comparable only
    #: with that station's own thresholds. Mixing the two silently puts gauges tens of
    #: metres out, which is why it is recorded rather than assumed.
    datum: str = "MSL"
    #: Top and bottom of the station's normal operating range, in the SAME datum as its
    #: observations. NOT a bank level: above it means "unusually high for here", not
    #: "out of the channel". Many networks publish this and no overtopping threshold.
    typical_high: float | None = None
    typical_low: float | None = None
    #: Highest level ever recorded here. Context, not a threshold.
    record_high: float | None = None
    #: Source-declared critical level, where one exists. Kept separate from bank_msl
    #: because sources do not always agree with themselves.
    critical_msl: float | None = None
    ground_msl: float | None = None
    #: True where the level here is driven by the tide rather than by catchment flow.
    #:
    #: A tidal gauge rises and falls twice a day, so fitting a rate of rise to it and
    #: extending that forward is not a forecast, it is a description of the last three
    #: hours pointed at tomorrow. A North Sea platform reading +13 cm/h is a flooding
    #: tide that will turn within hours, not a river coming up.
    #:
    #: Set by adapters that can actually tell. The US publishes a SHEF code per gauge;
    #: the Dutch offshore platforms are identifiable by their datum. Where a network
    #: does not say, `history` catches it after a full tidal cycle of archive instead.
    tidal: bool = False
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
class Forecast:
    """Forward-looking signals for one station.

    Refreshed on a slower cadence than water levels: GloFAS is a daily product and
    weather models update roughly 6-hourly, so refetching these every 30 minutes would
    burn API quota to receive identical numbers.
    """

    #: When the upstream model was queried. Displayed, because a 6-hour-old forecast
    #: is fine while a 3-day-old one is not.
    fetched_at: datetime
    rain_past_24h_mm: float | None = None
    rain_next_24h_mm: float | None = None
    rain_next_72h_mm: float | None = None
    #: GloFAS river discharge, m³/s.
    discharge_now_cms: float | None = None
    discharge_max_7d_cms: float | None = None
    #: A forecast of the gauge's own level, where the national network publishes one.
    #: This is a different and much stronger thing than rainfall: the US NWS runs a
    #: hydrological model per gauge, so where it exists it beats inferring a rise from
    #: precipitation over a grid cell.
    #:
    #: In the SAME datum and units as that station's observations (metres, and `local`
    #: wherever the network publishes no national datum), so it is only ever compared
    #: with thresholds from the same station.
    #: Weeks-ahead outlook, from GloFAS. River FLOW in m3/s, never a level: converting
    #: discharge to metres needs a rating curve per gauge that nobody publishes and our
    #: archive is far too short to fit. A level weeks out would be the most confident
    #: wrong number in the project.
    discharge_outlook_cms: list[float] | None = None
    discharge_peak_cms: float | None = None
    #: Days from today to the modelled peak.
    discharge_peak_day: int | None = None
    #: Peak flow divided by today's.
    discharge_outlook_ratio: float | None = None
    #: Ensemble max/min at the far end. Near 1 means the members agree; large means the
    #: outlook is a shrug wearing a curve, and the UI says so.
    discharge_outlook_spread: float | None = None

    forecast_level: float | None = None
    #: Valid time of that forecast level, and who issued it. Both displayed: a forecast
    #: for 06:00 tomorrow issued this morning is worth more than one issued two days ago.
    #:
    #: An ISO-8601 UTC string rather than a datetime, because forecast fields round-trip
    #: through the on-disk JSON forecast cache between builds, and a datetime does not
    #: survive that trip.
    forecast_level_at: str | None = None
    forecast_level_source: str | None = None

    @property
    def discharge_rise_ratio(self) -> float | None:
        """Peak forecast discharge ÷ today's. >1 means the model expects a rise.

        Returns None rather than a ratio when today's discharge is ~0, because dividing
        by a near-zero baseline produces enormous meaningless numbers on dry channels.
        """
        now, peak = self.discharge_now_cms, self.discharge_max_7d_cms
        if now is None or peak is None or now < 0.1:
            return None
        return round(peak / now, 2)


@dataclass(frozen=True, slots=True)
class TideExtreme:
    """A predicted high or low water."""

    at: datetime
    height_m: float
    kind: str  # "high" | "low"


@dataclass(frozen=True, slots=True)
class TideSeries:
    """Predicted sea level at one coastal point.

    Powers two different things from one dataset: the fishing planner (Phase 3) and
    coastal backwater flooding, where a high tide blocks river drainage (Phase 2).
    """

    point_id: str
    name: str
    name_th: str
    lat: float
    lon: float
    fetched_at: datetime
    times: list[datetime] = field(default_factory=list)
    heights_m: list[float] = field(default_factory=list)
    extremes: list[TideExtreme] = field(default_factory=list)

    @property
    def range_m(self) -> float | None:
        """Tidal range over the forecast window."""
        if not self.heights_m:
            return None
        return round(max(self.heights_m) - min(self.heights_m), 2)

    def next_extremes(self, now: datetime, limit: int = 4) -> list[TideExtreme]:
        return [e for e in self.extremes if e.at >= now][:limit]


@dataclass(frozen=True, slots=True)
class StationState:
    """A station joined to its latest reading, plus what we can honestly derive right now."""

    station: Station
    observation: Observation
    generated_at: datetime
    forecast: Forecast | None = None

    @property
    def freeboard_m(self) -> float | None:
        """Metres of headroom below the bank. Negative means the bank is overtopped."""
        if self.station.bank_msl is None or self.observation.level_msl is None:
            return None
        return round(self.station.bank_msl - self.observation.level_msl, 3)

    @property
    def above_typical_m(self) -> float | None:
        """Metres above the station's normal operating range, where one is published.

        This is the weaker cousin of freeboard, for networks that publish a typical
        range and no overtopping threshold. Above it means "unusually high here", not
        "out of the channel", and the two must never be shown as the same thing.
        """
        high = self.station.typical_high
        if high is None or self.observation.level_msl is None:
            return None
        return round(self.observation.level_msl - high, 3)

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
    #: True when this source is absent BY CONFIGURATION rather than broken, e.g. an
    #: optional layer with no API key. A deliberate absence must not mark the whole
    #: snapshot unhealthy: that pins the "data may be out of date" banner on for ever,
    #: and a warning that is always showing is one nobody reads when it matters.
    optional: bool = False
