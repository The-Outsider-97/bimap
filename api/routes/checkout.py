"""
FastAPI checkout route for BIMAP orders.

The browser/API route may initiate a provider-neutral checkout, but it is never
a source of payment truth.  Verified provider webhooks remain authoritative for
payment state through ``HandlePayment`` and the ``Payment`` port.

This route delegates pricing, tier resolution, lifecycle transition legality,
idempotency, and provider checkout creation to ``BeginCheckout``/
``OrderService``.  It returns only the stable ``PaymentCheckout`` projection.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import APIConfigurationError
from ..utils.api_helpers import *
from ...app.commands.begin_checkout import BeginCheckout
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Checkout")
printer = PrettyPrinter()

_COMPONENT = "api_route_checkout"


class RouteCheckout:
    """Dependency-injected route group for creating a payment checkout."""

    __slots__ = ("router", "_begin_checkout", "_authorize")

    def __init__(self, begin_checkout: BeginCheckout, *, authorizer: RouteAuthorizer) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing checkout API routes",
            event="api_route_checkout_init_start",
        )
        if not isinstance(begin_checkout, BeginCheckout):
            raise APIConfigurationError(
                "begin_checkout must be a BeginCheckout command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="begin_checkout",
                context={"received_type": type(begin_checkout).__name__},
            )
        self._begin_checkout = begin_checkout
        self._authorize = require_route_authorizer(authorizer)

        router = APIRouter(prefix="/orders", tags=["checkout"])
        router.add_api_route(
            "/{order_id}/checkout",
            self.begin,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            response_class=Response,
            name="begin_checkout",
        )
        self.router = router
        logger.info(
            {
                "event": "api_route_checkout_initialized",
                "registered_route_count": 1,
            }
        )

    async def begin(self, request: Request, order_id: str) -> Response:
        """POST ``/orders/{order_id}/checkout`` -> create checkout session."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling begin-checkout request",
            event="api_route_checkout_begin_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="begin_checkout",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="begin_checkout",
            resource_id=target,
        )
        idempotency_key = require_idempotency_key(request)

        checkout = self._begin_checkout.execute(
            target,
            idempotency_key=idempotency_key,
            actor=actor,
        )
        logger.info(
            {
                "event": "api_route_checkout_begin_completed",
                "order_id": checkout.order_id,
                "provider_name": checkout.provider_name,
                "currency": checkout.currency,
                "has_customer_action": checkout.customer_action_url is not None,
                "has_expiry": checkout.expires_at is not None,
            }
        )
        return json_response(
            checkout.to_dict(),
            status_code=status.HTTP_201_CREATED,
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteCheckout"]