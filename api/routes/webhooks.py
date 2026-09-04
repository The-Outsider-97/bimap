"""
FastAPI payment-webhook route for BIMAP.

This module preserves the raw request body and delegates provider-specific
signature verification/normalization to ``HandlePayment`` -> ``Payment`` port.
It does not parse provider JSON, recognize provider event names, or treat browser
checkout redirects as proof of payment.

The provider signature header name is injected by composition because BIMAP's
payment port is intentionally provider-neutral.  Hard-coding a Stripe-, Adyen-,
or other provider-specific header here would violate that boundary.

Successful handling returns 204 No Content.  The provider does not need BIMAP's
order/payment internals in the response, and withholding them reduces accidental
information disclosure.  Audit enqueueing is deliberately not performed here:
the current ``HandlePayment`` command explicitly separates payment handling from
construction/submission of an authoritative ``AuditJob``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.handle_payment import HandlePayment
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Webhooks")
printer = PrettyPrinter()

_COMPONENT = "api_route_webhooks"


class RouteWebhooks:
    """Dependency-injected provider-neutral payment webhook route group."""

    __slots__ = ("router", "_handle_payment", "_signature_header")

    def __init__(self, handle_payment: HandlePayment, *, signature_header: str) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing webhook API routes",
            event="api_route_webhooks_init_start",
        )
        if not isinstance(handle_payment, HandlePayment):
            raise APIConfigurationError(
                "handle_payment must be a HandlePayment command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="handle_payment",
                context={"received_type": type(handle_payment).__name__},
            )
        self._handle_payment = handle_payment
        self._signature_header = require_header_name(
            signature_header,
            field="signature_header",
        )

        router = APIRouter(prefix="/webhooks", tags=["webhooks"])
        router.add_api_route(
            "/payment",
            self.payment,
            methods=["POST"],
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            name="payment_webhook",
        )
        self.router = router
        logger.info(
            {
                "event": "api_route_webhooks_initialized",
                "registered_route_count": 1,
                "signature_header": self._signature_header,
            }
        )

    async def payment(self, request: Request) -> Response:
        """POST ``/webhooks/payment`` -> verify and apply one payment event."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling payment webhook",
            event="api_route_webhooks_payment_start",
        )

        signature = single_header(
            request.scope,
            self._signature_header,
            required=True,
        )
        assert signature is not None
        signature = require_api_text(
            signature,
            field=self._signature_header,
            error_type=APIValidationError,
            component=_COMPONENT,
            operation="payment_webhook",
            max_length=4096,
        )

        try:
            payload = await request.body()
        except Exception as exc:
            raise APIValidationError(
                "Payment webhook request body could not be read.",
                component=_COMPONENT,
                operation="payment_webhook",
                field="body",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        if not payload:
            raise APIValidationError(
                "Payment webhook body must not be empty.",
                component=_COMPONENT,
                operation="payment_webhook",
                field="body",
            )

        result = self._handle_payment.execute(payload, signature=signature)
        logger.info(
            {
                "event": "api_route_webhooks_payment_completed",
                "event_id": result.event.event_id,
                "order_id": result.order.order_id,
                "payment_status": result.event.status.value, # type: ignore
                "order_state": result.order.state.value,
                "state_changed": result.state_changed,
            }
        )
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteWebhooks"]