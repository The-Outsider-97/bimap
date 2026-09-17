"""Filesystem-backed BIMAP storefront catalog.

The ``store`` tree is the authoritative source for saleable 2D/3D product
metadata and binaries.  Only metadata plus files beneath ``previews/`` and
``renders/`` are public.  Files beneath ``files/`` are deliberately never
resolved by this adapter's public-asset method.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from ..app.ports.store_catalog import StoreCatalog


_ALLOWED_DIMENSIONS = frozenset({"2d", "3d"})
_PUBLIC_ASSET_ROOTS = frozenset({"previews", "renders"})
_THREE_D_FORMATS = frozenset({
    "RFA", "RVT", "MAX", "STL", "DWG_3D", "OBJ", "GLB"
})
_TWO_D_KINDS = frozenset({"single", "pack"})
_THREE_D_TECHNICAL_KINDS = frozenset({"revit", "3ds-max", "mesh", "cad-3d"})


class StoreCatalogConfigurationError(RuntimeError):
    """Raised when a storefront manifest is malformed or inconsistent."""


class FilesystemStoreCatalog(StoreCatalog):
    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser().resolve()
        if not candidate.is_dir():
            raise StoreCatalogConfigurationError(
                f"BIMAP store root does not exist: {candidate}"
            )
        self._root = candidate

    def _dimension_root(self, dimension: str) -> Path:
        normalized = str(dimension).strip().lower()
        if normalized not in _ALLOWED_DIMENSIONS:
            raise ValueError("dimension must be '2d' or '3d'")
        target = (self._root / normalized).resolve()
        if target.parent != self._root or not target.is_dir():
            raise StoreCatalogConfigurationError(
                f"Store dimension is unavailable: {normalized}"
            )
        return target

    @staticmethod
    def _required_text(value: Any, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise StoreCatalogConfigurationError(f"{field} must be non-empty text")
        return value.strip()

    @staticmethod
    def _optional_text(value: Any, *, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise StoreCatalogConfigurationError(f"{field} must be text or null")
        return value.strip()

    @staticmethod
    def _string_list(value: Any, *, field: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise StoreCatalogConfigurationError(f"{field} must be a list")
        result: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise StoreCatalogConfigurationError(
                    f"{field}[{index}] must be non-empty text"
                )
            result.append(item.strip())
        return result

    @staticmethod
    def _mapping(value: Any, *, field: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise StoreCatalogConfigurationError(f"{field} must be a mapping")
        return dict(value)

    @staticmethod
    def _integer(value: Any, *, field: str) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StoreCatalogConfigurationError(
                f"{field} must be a non-negative integer or null"
            )
        return value

    @staticmethod
    def _price_label(value: Any, *, field: str) -> str | None:
        if value is None:
            return None
        data = FilesystemStoreCatalog._mapping(value, field=field)
        amount_raw = data.get("amount")
        currency = FilesystemStoreCatalog._required_text(
            data.get("currency"), field=f"{field}.currency"
        ).upper()
        try:
            amount = Decimal(str(amount_raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise StoreCatalogConfigurationError(
                f"{field}.amount must be numeric"
            ) from exc
        if not amount.is_finite() or amount < 0:
            raise StoreCatalogConfigurationError(
                f"{field}.amount must be a finite non-negative number"
            )
        symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency)
        separator = "" if symbol in {"€", "$", "£"} else " "
        return f"{symbol}{separator}{amount:.2f}"

    @staticmethod
    def _public_url(dimension: str, path_value: str | None) -> str | None:
        if path_value is None:
            return None
        relative = Path(path_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise StoreCatalogConfigurationError("Public asset path is unsafe")
        if not relative.parts or relative.parts[0] not in _PUBLIC_ASSET_ROOTS:
            raise StoreCatalogConfigurationError(
                "Public asset paths must begin with previews/ or renders/"
            )
        normalized = "/".join(relative.parts)
        return f"/api/v1/storefront/assets/{dimension}/{normalized}"

    def _load_root(self, dimension: str) -> dict[str, Any]:
        manifest = self._dimension_root(dimension) / "product.yaml"
        if not manifest.is_file():
            raise StoreCatalogConfigurationError(
                f"Missing storefront manifest: {manifest}"
            )
        try:
            payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise StoreCatalogConfigurationError(
                f"Could not read storefront manifest: {manifest}"
            ) from exc
        if payload is None:
            return {"schema_version": 1, "products": []}
        if not isinstance(payload, dict):
            raise StoreCatalogConfigurationError(
                f"Storefront manifest root must be a mapping: {manifest}"
            )
        if payload.get("schema_version") != 1:
            raise StoreCatalogConfigurationError(
                f"Unsupported storefront schema_version in {manifest}"
            )
        products = payload.get("products")
        if products is None:
            products = []
        if not isinstance(products, list):
            raise StoreCatalogConfigurationError(
                f"products must be a list in {manifest}"
            )
        return {"schema_version": 1, "products": products}

    def _normalize_preview(self, dimension: str, raw: Any, *, field: str) -> dict[str, Any]:
        data = self._mapping(raw, field=field)
        if dimension == "3d":
            return {
                "videoSrc": self._public_url(
                    dimension,
                    self._optional_text(data.get("video"), field=f"{field}.video"),
                ),
                "posterSrc": self._public_url(
                    dimension,
                    self._optional_text(data.get("poster"), field=f"{field}.poster"),
                ),
            }
        return {
            "src": self._public_url(
                dimension,
                self._optional_text(data.get("src"), field=f"{field}.src"),
            ),
            "alt": self._required_text(
                data.get("alt", "Product preview"), field=f"{field}.alt"
            ),
        }

    def _normalize_renders(self, dimension: str, raw: Any, *, field: str) -> list[dict[str, Any]]:
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise StoreCatalogConfigurationError(f"{field} must be a list")
        result: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            entry = self._mapping(item, field=f"{field}[{index}]")
            src = self._optional_text(entry.get("src"), field=f"{field}[{index}].src")
            result.append({
                "src": self._public_url(dimension, src),
                "alt": self._required_text(
                    entry.get("alt", f"Product render {index + 1}"),
                    field=f"{field}[{index}].alt",
                ),
                "caption": self._optional_text(
                    entry.get("caption"), field=f"{field}[{index}].caption"
                ),
            })
        return result

    def _normalize_three_d_technical(self, raw: Any, *, field: str) -> dict[str, Any]:
        data = self._mapping(raw, field=field)
        kind = self._required_text(data.get("kind"), field=f"{field}.kind").lower()
        if kind not in _THREE_D_TECHNICAL_KINDS:
            raise StoreCatalogConfigurationError(
                f"{field}.kind must be one of {sorted(_THREE_D_TECHNICAL_KINDS)}"
            )
        if kind == "revit":
            return {
                "kind": "revit",
                "revitVersion": self._optional_text(data.get("revit_version"), field=f"{field}.revit_version"),
                "parametric": data.get("parametric") if isinstance(data.get("parametric"), bool) else None,
                "ifc": self._mapping(data.get("ifc"), field=f"{field}.ifc"),
            }
        if kind == "3ds-max":
            return {
                "kind": "3ds-max",
                "maxVersion": self._optional_text(data.get("max_version"), field=f"{field}.max_version"),
                "vertices": self._integer(data.get("vertices"), field=f"{field}.vertices"),
                "polygons": self._integer(data.get("polygons"), field=f"{field}.polygons"),
                "materials": self._string_list(data.get("materials"), field=f"{field}.materials"),
            }
        if kind == "mesh":
            return {
                "kind": "mesh",
                "vertices": self._integer(data.get("vertices"), field=f"{field}.vertices"),
                "edges": self._integer(data.get("edges"), field=f"{field}.edges"),
                "polygons": self._integer(data.get("polygons"), field=f"{field}.polygons"),
                "units": self._optional_text(data.get("units"), field=f"{field}.units"),
                "materials": self._string_list(data.get("materials"), field=f"{field}.materials"),
                "texturesIncluded": data.get("textures_included") if isinstance(data.get("textures_included"), bool) else None,
            }
        return {
            "kind": "cad-3d",
            "dwgVersion": self._optional_text(data.get("dwg_version"), field=f"{field}.dwg_version"),
            "units": self._optional_text(data.get("units"), field=f"{field}.units"),
            "solids": self._integer(data.get("solids"), field=f"{field}.solids"),
            "surfaces": self._integer(data.get("surfaces"), field=f"{field}.surfaces"),
            "meshes": self._integer(data.get("meshes"), field=f"{field}.meshes"),
            "layers": self._integer(data.get("layers"), field=f"{field}.layers"),
        }

    def _normalize_two_d_technical(self, raw: Any, *, field: str) -> dict[str, Any]:
        data = self._mapping(raw, field=field)
        return {
            "dwgVersion": self._optional_text(data.get("dwg_version"), field=f"{field}.dwg_version"),
            "units": self._optional_text(data.get("units"), field=f"{field}.units"),
            "drawingScale": self._optional_text(data.get("drawing_scale"), field=f"{field}.drawing_scale"),
            "layerCount": self._integer(data.get("layer_count"), field=f"{field}.layer_count"),
            "fileCount": self._integer(data.get("file_count"), field=f"{field}.file_count"),
            "modelSpace": data.get("model_space") if isinstance(data.get("model_space"), bool) else None,
            "paperSpaceLayouts": self._integer(data.get("paper_space_layouts"), field=f"{field}.paper_space_layouts"),
        }

    def _normalize_product(self, dimension: str, raw: Any, *, index: int) -> dict[str, Any]:
        field = f"products[{index}]"
        data = self._mapping(raw, field=field)
        active = data.get("active", True)
        if not isinstance(active, bool):
            raise StoreCatalogConfigurationError(f"{field}.active must be boolean")
        if not active:
            return {}

        common = {
            "id": self._required_text(data.get("id"), field=f"{field}.id"),
            "slug": self._required_text(data.get("slug"), field=f"{field}.slug"),
            "title": self._required_text(data.get("title"), field=f"{field}.title"),
            "shortDescription": self._required_text(data.get("short_description"), field=f"{field}.short_description"),
            "description": self._required_text(data.get("description"), field=f"{field}.description"),
            "category": self._required_text(data.get("category"), field=f"{field}.category"),
            "tags": self._string_list(data.get("tags"), field=f"{field}.tags"),
            "preview": self._normalize_preview(dimension, data.get("preview"), field=f"{field}.preview"),
            "renders": self._normalize_renders(dimension, data.get("renders"), field=f"{field}.renders"),
            "priceLabel": self._price_label(data.get("price"), field=f"{field}.price"),
        }

        if dimension == "3d":
            formats = [item.upper() for item in self._string_list(data.get("formats"), field=f"{field}.formats")]
            unsupported = sorted(set(formats) - _THREE_D_FORMATS)
            if unsupported:
                raise StoreCatalogConfigurationError(
                    f"{field}.formats contains unsupported values: {unsupported}"
                )
            if not formats:
                raise StoreCatalogConfigurationError(f"{field}.formats cannot be empty")
            common.update({
                "formats": formats,
                "technical": self._normalize_three_d_technical(
                    data.get("technical"), field=f"{field}.technical"
                ),
            })
        else:
            kind = self._required_text(data.get("kind"), field=f"{field}.kind").lower()
            if kind not in _TWO_D_KINDS:
                raise StoreCatalogConfigurationError(
                    f"{field}.kind must be 'single' or 'pack'"
                )
            common.update({
                "kind": kind,
                "technical": self._normalize_two_d_technical(
                    data.get("technical"), field=f"{field}.technical"
                ),
            })
        return common

    def list_products(self, dimension: str) -> tuple[dict[str, Any], ...]:
        normalized = str(dimension).strip().lower()
        payload = self._load_root(normalized)
        result: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_slugs: set[str] = set()
        for index, raw in enumerate(payload["products"]):
            product = self._normalize_product(normalized, raw, index=index)
            if not product:
                continue
            if product["id"] in seen_ids:
                raise StoreCatalogConfigurationError(
                    f"Duplicate product id: {product['id']}"
                )
            if product["slug"] in seen_slugs:
                raise StoreCatalogConfigurationError(
                    f"Duplicate product slug: {product['slug']}"
                )
            seen_ids.add(product["id"])
            seen_slugs.add(product["slug"])
            result.append(product)
        return tuple(result)

    def resolve_public_asset(self, dimension: str, relative_path: str) -> Path:
        dimension_root = self._dimension_root(dimension)
        raw = Path(str(relative_path).replace("\\", "/"))
        if raw.is_absolute() or ".." in raw.parts or not raw.parts:
            raise FileNotFoundError("Unsafe storefront asset path")
        if raw.parts[0] not in _PUBLIC_ASSET_ROOTS:
            raise FileNotFoundError("Store sale files are not public assets")
        target = (dimension_root / raw).resolve()
        try:
            target.relative_to(dimension_root)
        except ValueError as exc:
            raise FileNotFoundError("Storefront asset escaped store root") from exc
        if not target.is_file():
            raise FileNotFoundError("Storefront asset does not exist")
        return target


__all__ = [
    "FilesystemStoreCatalog",
    "StoreCatalogConfigurationError",
]
