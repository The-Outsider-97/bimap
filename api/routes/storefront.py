"""Public read-only BIMAP storefront routes."""

from __future__ import annotations

from mimetypes import guess_type

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import FileResponse

from ._shared import json_response
from ..utils.api_errors import APIConfigurationError, APINotFoundError, APIUnprocessableError
from ..utils.api_helpers import announce_api_action
from ...app.ports.store_catalog import StoreCatalog
from ...infra.store_catalog import StoreCatalogConfigurationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Storefront")
printer = PrettyPrinter()
_COMPONENT = "api_route_storefront"


class RouteStorefront:
    __slots__ = ("router", "_catalog")

    def __init__(self, catalog: StoreCatalog) -> None:
        if not isinstance(catalog, StoreCatalog):
            raise APIConfigurationError(
                "catalog must implement StoreCatalog.",
                component=_COMPONENT,
                operation="initialize",
                field="catalog",
                context={"received_type": type(catalog).__name__},
            )
        self._catalog = catalog
        router = APIRouter(prefix="/storefront", tags=["storefront"])
        router.add_api_route(
            "/{dimension}",
            self.list_products,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="list_storefront_products",
        )
        router.add_api_route(
            "/assets/{dimension}/{asset_path:path}",
            self.asset,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="storefront_asset",
        )
        self.router = router

    @staticmethod
    def _dimension(value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"2d", "3d"}:
            raise APIUnprocessableError(
                "Unsupported storefront dimension.",
                component=_COMPONENT,
                operation="validate_dimension",
                field="dimension",
                context={"received": normalized},
            )
        return normalized

    async def list_products(self, request: Request, dimension: str) -> Response:
        del request
        normalized = self._dimension(dimension)
        try:
            payload = self._catalog.list_products(normalized)
        except StoreCatalogConfigurationError as exc:
            raise APIConfigurationError(
                "Storefront catalog configuration is invalid.",
                component=_COMPONENT,
                operation="list_products",
                context={"dimension": normalized},
                cause=exc,
            ) from exc
        return json_response(list(payload))

    async def asset(self, request: Request, dimension: str, asset_path: str) -> Response:
        del request
        normalized = self._dimension(dimension)
        try:
            target = self._catalog.resolve_public_asset(normalized, asset_path)
        except FileNotFoundError as exc:
            raise APINotFoundError(
                "Requested storefront preview asset does not exist.",
                component=_COMPONENT,
                operation="get_asset",
                context={"dimension": normalized},
                cause=exc,
            ) from exc
        media_type, _ = guess_type(target.name)
        return FileResponse(
            path=target,
            media_type=media_type or "application/octet-stream",
            filename=None,
            headers={"Cache-Control": "public, max-age=3600"},
        )


__all__ = ["RouteStorefront"]
