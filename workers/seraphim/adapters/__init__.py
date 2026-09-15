from seraphim.adapters.base import SourceAdapter, registry

# Importing an adapter registers it.
from seraphim.adapters import thaiwater  # noqa: F401

__all__ = ["SourceAdapter", "registry"]
