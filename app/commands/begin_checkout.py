"""
Begin-checkout application command for BIMAP.

``OrderService.begin_checkout`` already owns the complete supported checkout
orchestration:

1. load the authoritative order;
2. require a selected configured tier;
3. require that the tier is priced;
4. transition ``UPLOAD_VALIDATED -> PAYMENT_PENDING`` through
   ``OrderTransitions``;
5. persist that transition with optimistic concurrency; and
6. invoke the provider-neutral ``Payment`` port using the same idempotency key.

This command is consequently a thin Level-5 use-case boundary.  It does not
calculate prices, select currencies, mutate orders directly, expose provider
SDK objects, or duplicate payment validation.

If provider checkout creation fails after the payment-pending transition was
persisted, callers may retry with the same idempotency key; the service/domain
layers own that replay behavior.
"""

from __future__ import annotations

from ..ports.payment import PaymentCheckout
from ..services.order_service import OrderService
from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Begin Checkout Command")
printer = PrettyPrinter()

_COMPONENT = "begin_checkout_command"


class BeginCheckout:
    """Enter payment-pending state and create one provider-neutral checkout."""

    __slots__ = ("_service",)

    def __init__(self, service: OrderService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing begin-checkout command",
            event="begin_checkout_command_init_start",
        )

        if not isinstance(service, OrderService):
            raise AppConfigurationError(
                "service must be an OrderService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )

        self._service = service
        logger.debug(
            {
                "event": "begin_checkout_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        actor: str | None = None,
    ) -> PaymentCheckout:
        """
        Begin checkout for one upload-validated order.

        Provider checkout identifiers and customer-action URLs are deliberately
        not emitted in command diagnostics.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing begin-checkout command",
            event="begin_checkout_command_execute_start",
            context={"order_id": order_id},
        )

        try:
            result = self._service.begin_checkout(
                order_id,
                idempotency_key=idempotency_key,
                actor=actor,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "OrderService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={
                    "order_id": order_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if not isinstance(result, PaymentCheckout):
            raise AppIntegrityError(
                "Begin-checkout service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        logger.info(
            {
                "event": "begin_checkout_command_completed",
                "order_id": result.order_id,
                "provider_name": result.provider_name,
                "currency": result.currency,
                "has_customer_action": result.customer_action_url is not None,
                "has_expiry": result.expires_at is not None,
            }
        )
        return result


__all__ = ["BeginCheckout"]