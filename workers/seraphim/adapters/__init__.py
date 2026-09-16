from seraphim.adapters.base import (
    ForecastAdapter,
    SourceAdapter,
    forecast_registry,
    registry,
)

# Importing an adapter registers it.
from seraphim.adapters import nwsriv, openmeteo, rws, thaiwater, ukea  # noqa: F401,E402

__all__ = ["SourceAdapter", "ForecastAdapter", "registry", "forecast_registry"]
