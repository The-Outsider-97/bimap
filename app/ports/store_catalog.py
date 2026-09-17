"""Application-facing contract for the BIMAP digital storefront catalog."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StoreCatalogError(RuntimeError):
    """Expected storefront catalog/configuration failure."""


class StoreCatalog(ABC):
    """Read-only catalog boundary used by the public storefront API."""

    @abstractmethod
    def list_products(self, dimension: str) -> tuple[dict[str, Any], ...]:
        """Return public product metadata for ``2d`` or ``3d``."""
        raise NotImplementedError

    @abstractmethod
    def resolve_public_asset(self, dimension: str, relative_path: str) -> Path:
        """Resolve one public preview/render asset without exposing sale files."""
        raise NotImplementedError


__all__ = [
    "StoreCatalog",
    "StoreCatalogError",
]
