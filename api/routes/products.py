"""
FastAPI route for the configured BIMAP product catalog.

``RouteProducts`` is read-only.  Product definitions, tiers, prices and limits
are supplied to ``GetProducts`` by bootstrap/composition; this route never opens
configuration files and never hard-codes commercial values.

The router is intentionally unversioned (``/products``). ``api/app.py`` should
mount it under the deployment prefix such as ``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ._shared import json_response
from ..utils.api_errors import APIConfigurationError
from ..utils.api_helpers import announce_api_action
from ...app.queries.get_products import GetProducts
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Products")
printer = PrettyPrinter()

_COMPONENT = "api_route_products"


class RouteProducts:
    """Dependency-injected read-only product-catalog route group."""

    __slots__ = ("router", "_get_products")

    def __init__(self, get_products: GetProducts) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing products API routes",
            event="api_route_products_init_start",
        )
        if not isinstance(get_products, GetProducts):
            raise APIConfigurationError(
                "get_products must be a GetProducts query handler.",
                component=_COMPONENT,
                operation="initialize",
                field="get_products",
                context={"received_type": type(get_products).__name__},
            )
        self._get_products = get_products

        router = APIRouter(prefix="/products", tags=["products"])
        router.add_api_route(
            "",
            self.list,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="list_products",
        )
        self.router = router
        logger.info(
            {
                "event": "api_route_products_initialized",
                "registered_route_count": 1,
            }
        )

    async def list(self, request: Request) -> Response:
        """GET ``/products`` -> configured products, tiers, and exact limits."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling list-products request",
            event="api_route_products_list_start",
        )
        # ``request`` is intentionally accepted for FastAPI/observability
        # symmetry even though this public read currently needs no request data.
        del request

        products = self._get_products.execute()
        payload = [view.to_dict() for view in products]
        logger.info(
            {
                "event": "api_route_products_list_completed",
                "product_count": len(payload),
            }
        )
        return json_response(payload)


__all__ = ["RouteProducts"]
