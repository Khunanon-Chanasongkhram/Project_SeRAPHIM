from seraphim.adapters.base import (
    ForecastAdapter,
    SourceAdapter,
    forecast_registry,
    registry,
)

# Importing an adapter registers it.
from seraphim.adapters import openmeteo, thaiwater, ukea  # noqa: F401,E402

__all__ = ["SourceAdapter", "ForecastAdapter", "registry", "forecast_registry"]
