"""SourceAdapter — the seam that makes global scaling a config change, not a rewrite.

Every data source implements this interface. The rest of the system only ever sees
canonical models, so adding Vietnam or the Philippines means writing one adapter, not
touching the risk engine, the snapshot format or the map.

It exists with one implementation on purpose. Retrofitting an abstraction after three
sources have leaked their quirks everywhere is the expensive path.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from seraphim.models import Observation, SourceHealth, Station

USER_AGENT = (
    "SeRAPHIM/0.1 (open-source flood monitoring; +https://github.com/seraphim) "
    "Python-urllib"
)


class SourceAdapter(ABC):
    """Fetches one upstream source and returns canonical stations + observations."""

    #: Stable identifier, used to namespace station ids. Never change it casually:
    #: it is half of every station's primary key.
    id: str
    #: Human-readable, for attribution in the UI. Data licences generally require this.
    attribution: str
    #: ISO-3166 alpha-2 of the country covered, or "*" for global sources.
    country: str = "*"

    @abstractmethod
    def fetch(self) -> tuple[list[Station], list[Observation], SourceHealth]:
        """Retrieve current data.

        Must not raise for ordinary upstream problems — return a failed SourceHealth
        instead. One dead source must never take down the whole publish cycle, because
        a partial map during a flood beats no map.
        """


class ForecastAdapter(ABC):
    """Enriches stations that already exist, rather than producing new ones.

    Separate from SourceAdapter on purpose: these run on a slower cadence (forecasts
    do not change every 30 minutes) and they answer "what is coming" rather than
    "what is here now".
    """

    id: str
    attribution: str
    #: How long this source's output stays useful. Set per adapter because rainfall
    #: models, a daily discharge product and a harmonic tide prediction go stale at
    #: very different rates — and refetching the slow ones at the fast one's cadence
    #: is what burns an API allowance for nothing.
    refresh_hours: float = 6.0

    @abstractmethod
    def fetch_for(
        self, points: list[tuple[str, float, float]]
    ) -> tuple[dict[str, dict], SourceHealth]:
        """Given (station_id, lat, lon) triples, return {station_id: {field: value}}.

        Like SourceAdapter.fetch, must degrade rather than raise: a missing forecast
        should grey out one panel, not blank the map.
        """


# ---------------------------------------------------------------------------
# Parsing helpers.
#
# Upstream fields arrive as null, "", "-", or numeric strings, interchangeably and
# without warning. A null that silently becomes 0.0 turns "no reading" into "river at
# zero" — which reads as safe. These helpers refuse to guess.
# ---------------------------------------------------------------------------

_NULLISH = {"", "-", "null", "none", "n/a", "na", "nan"}


def num(value: object) -> float | None:
    """Parse a number, or return None. Never coerces missing data to zero."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if value != value else float(value)  # NaN check
    if isinstance(value, str):
        v = value.strip()
        if v.lower() in _NULLISH:
            return None
        try:
            f = float(v)
        except ValueError:
            return None
        return None if f != f else f
    return None


def text(value: object) -> str | None:
    """Parse a non-empty string, or return None."""
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return None


def ident(value: object) -> str | None:
    """Parse an identifier that may arrive as int or str.

    Sources are inconsistent about this — ThaiWater sends station ids as ints while
    sending admin codes as strings. Ids are stringified rather than kept numeric so
    that a namespaced key like "thaiwater:528052" is stable regardless of source type.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    if isinstance(value, str):
        v = value.strip()
        return v if v and v.lower() not in _NULLISH else None
    return None


def dig(obj: object, *path: str) -> object:
    """Walk nested dicts safely. Upstream nests inconsistently and omits keys freely."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def parse_local_naive(value: object, tz_offset_hours: float) -> datetime | None:
    """Parse a naive local timestamp such as '2026-09-15 14:00' into aware UTC.

    Sources hand out local wall-clock time with no offset. Treating that as UTC shifts
    every reading by the offset — 7 hours for Thailand — which would quietly corrupt
    every rate-of-rise and staleness calculation downstream.
    """
    s = text(value)
    if s is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            naive = datetime.strptime(s, fmt)
        except ValueError:
            continue
        # Shift local wall-clock to UTC explicitly. We never call .timestamp() here:
        # that would interpret the value in the *runner's* timezone, so the same data
        # would parse differently on a laptop in Bangkok and a CI runner in UTC.
        return (naive - timedelta(hours=tz_offset_hours)).replace(tzinfo=timezone.utc)
    return None


def fetch_json(url: str, timeout: int = 60) -> object:
    """GET JSON with a declared User-Agent. Raises on failure; callers convert to health."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} from {url}")
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"fetch failed for {url}: {exc}") from exc


# ---------------------------------------------------------------------------

registry: dict[str, SourceAdapter] = {}
forecast_registry: dict[str, ForecastAdapter] = {}


def register_forecast(adapter: ForecastAdapter) -> ForecastAdapter:
    if adapter.id in forecast_registry:
        raise ValueError(f"duplicate forecast adapter id: {adapter.id}")
    forecast_registry[adapter.id] = adapter
    return adapter


def register(adapter: SourceAdapter) -> SourceAdapter:
    if adapter.id in registry:
        raise ValueError(f"duplicate adapter id: {adapter.id}")
    registry[adapter.id] = adapter
    return adapter
