"""
FastAPI routes for BIMAP order creation, retrieval, and cancellation.

The route group is a Level-6 HTTP admission/presentation adapter.  It delegates
all state-changing behavior to application commands and all reads to application
queries.  It does not instantiate services/repositories, decide order-state
legality, calculate prices, or access concrete infrastructure.

The router is intentionally unversioned (``/orders``). ``api/app.py`` should
mount it under the deployment API prefix, e.g. ``/api/v1``.  This avoids
hard-coding the external API version into each route module.

Authorization is injected as a route-boundary hook because the current BIMAP
order aggregate does not define customer/account ownership.  Protected routes
therefore fail configuration if no authorization hook is supplied rather than
silently exposing order identifiers.
"""

from __future__ import annotations

from collections.abc import Iterable
from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.cancel_order import CancelOrder
from ...app.commands.create_order import CreateOrder
from ...app.queries.get_order import GetOrder
from ...app.queries.list_orders import ListOrders
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Orders")
printer = PrettyPrinter()

_COMPONENT = "api_route_orders"


class RouteOrders:
    """Dependency-injected FastAPI route group for supported order operations."""

    __slots__ = (
        "router",
        "_create_order",
        "_cancel_order",
        "_get_order",
        "_list_orders",
        "_authorize",
    )

    def __init__(
        self,
        create_order: CreateOrder,
        cancel_order: CancelOrder,
        get_order: GetOrder,
        list_orders: ListOrders,
        *,
        authorizer: RouteAuthorizer,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing orders API routes",
            event="api_route_orders_init_start",
        )

        dependencies = (
            ("create_order", create_order, CreateOrder),
            ("cancel_order", cancel_order, CancelOrder),
            ("get_order", get_order, GetOrder),
            ("list_orders", list_orders, ListOrders),
        )
        for field, value, expected in dependencies:
            if not isinstance(value, expected):
                raise APIConfigurationError(
                    f"{field} must be a {expected.__name__} handler.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=field,
                    context={"received_type": type(value).__name__},
                )

        self._create_order = create_order
        self._cancel_order = cancel_order
        self._get_order = get_order
        self._list_orders = list_orders
        self._authorize = require_route_authorizer(authorizer)

        router = APIRouter(prefix="/orders", tags=["orders"])
        router.add_api_route(
            "",
            self.create,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            response_class=Response,
            name="create_order",
        )
        router.add_api_route(
            "/{order_id}",
            self.get,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="get_order",
        )
        router.add_api_route(
            "/{order_id}/cancel",
            self.cancel,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="cancel_order",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_orders_initialized",
                "registered_route_count": 3,
            }
        )

    async def create(self, request: Request) -> Response:
        """POST ``/orders`` -> ``CreateOrder`` -> versioned ``OrderContract``."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling create-order request",
            event="api_route_orders_create_start",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="create_order",
            resource_id=None,
        )

        payload = validate_object_fields(
            await read_json_object(request),
            required=("product_code",),
            optional=("tier_code", "project_alias"),
        )
        product_code = require_api_text(
            payload["product_code"],
            field="product_code",
            component=_COMPONENT,
            operation="create_order",
        )
        tier_code = optional_route_text(payload.get("tier_code"), field="tier_code")
        project_alias = optional_route_text(
            payload.get("project_alias"),
            field="project_alias",
        )

        order = self._create_order.execute(
            product_code=product_code,
            tier_code=tier_code,
            project_alias=project_alias,
        )
        response_payload = order_to_public_dict(order)
        logger.info(
            {
                "event": "api_route_orders_create_completed",
                "order_id": order.order_id,
                "state": order.state.value,
            }
        )
        return json_response(
            response_payload,
            status_code=status.HTTP_201_CREATED,
            headers={"Cache-Control": "no-store"},
        )

    async def get(self, request: Request, order_id: str) -> Response:
        """GET ``/orders/{order_id}`` -> authorized immutable order view."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling get-order request",
            event="api_route_orders_get_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="get_order",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="get_order",
            resource_id=target,
        )

        result = self._get_order.find(target)
        if result is None:
            raise APINotFoundError(
                "Requested order does not exist.",
                component=_COMPONENT,
                operation="get_order",
                field="order_id",
                context={"order_id": target},
            )

        logger.info(
            {
                "event": "api_route_orders_get_completed",
                "order_id": result.order_id,
                "state": result.state.value,
                "version": result.version,
            }
        )
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def cancel(self, request: Request, order_id: str) -> Response:
        """POST ``/orders/{order_id}/cancel`` -> canonical cancellation command."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling cancel-order request",
            event="api_route_orders_cancel_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="cancel_order",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="cancel_order",
            resource_id=target,
        )
        idempotency_key = require_idempotency_key(request)

        # Authorize before existence lookup to avoid turning this endpoint into
        # an order-identifier enumeration oracle.
        if self._get_order.find(target) is None:
            raise APINotFoundError(
                "Requested order does not exist.",
                component=_COMPONENT,
                operation="cancel_order",
                field="order_id",
                context={"order_id": target},
            )

        payload = validate_object_fields(
            await read_json_object(request, required=False),
            optional=("reason",),
        )
        reason = optional_route_text(payload.get("reason"), field="reason")
        order = self._cancel_order.execute(
            target,
            idempotency_key=idempotency_key,
            reason=reason,
            actor=actor,
        )
        logger.info(
            {
                "event": "api_route_orders_cancel_completed",
                "order_id": order.order_id,
                "state": order.state.value,
                "version": order.version,
            }
        )
        return json_response(
            order_to_public_dict(order),
            headers={"Cache-Control": "no-store"},
        )

    async def resolve_authorized(
        self,
        request: Request,
        order_ids: Iterable[str],
    ) -> Response:
        """Resolve an explicit set of already-addressed orders via ``ListOrders``.

        This method is intentionally *not* registered as a public HTTP endpoint.
        The current BIMAP API specification does not define a global order-list
        endpoint or customer-ownership query port.  ``api/app.py`` or a future
        account/admin route may call this helper after deciding which order IDs
        the caller is entitled to resolve.
        """
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving authorized order identifiers",
            event="api_route_orders_resolve_start",
        )
        targets = tuple(order_ids)
        for order_id in targets:
            target = require_api_text(
                order_id,
                field="order_id",
                component=_COMPONENT,
                operation="resolve_authorized",
            )
            await authorize_request(
                self._authorize,
                request,
                operation="get_order",
                resource_id=target,
            )

        result = self._list_orders.execute(targets)
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteOrders"]
