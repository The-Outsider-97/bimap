"""
Handle-payment application command for BIMAP.

The command is the application entry point for one authenticated payment
provider notification.  ``OrderService.handle_payment_event`` already performs
all supported authoritative work:

* delegates raw provider authentication/normalization to the ``Payment`` port;
* binds the verified event to the authoritative order and selected tier;
* validates amount/currency against configured product pricing;
* maps provider-neutral payment status to the canonical order lifecycle; and
* persists any state transition using the domain transition authority and
  optimistic concurrency.

This module intentionally does not enqueue an audit.  Audit queueing is a
separate command because it requires a fully constructed, reference-oriented
``AuditJob`` containing approved evidence/manifest references.  Coupling payment
handling directly to queue submission would either fabricate those references or
duplicate ``EnqueueAudit``.

Thus a successful payment may leave the order in ``PAID``; the explicit
``EnqueueAudit`` command is the next application action when an authoritative
AuditJob is available.
"""

from __future__ import annotations

from ..services.order_service import *
from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Handle Payment Command")
printer = PrettyPrinter()

_COMPONENT = "handle_payment_command"


class HandlePayment:
    """Verify and apply one provider payment event through ``OrderService``."""

    __slots__ = ("_service",)

    def __init__(self, service: OrderService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing handle-payment command",
            event="handle_payment_command_init_start",
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
                "event": "handle_payment_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(self, payload: bytes | bytearray | memoryview, *, signature: str) -> PaymentHandlingResult:
        """
        Verify one payment-provider notification and apply its lifecycle effect.

        Neither the raw provider payload nor signature is included in command
        diagnostics.  Provider-specific verification remains owned by the
        concrete ``Payment`` adapter behind ``OrderService``.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing handle-payment command",
            event="handle_payment_command_execute_start",
        )

        try:
            result = self._service.handle_payment_event(
                payload,
                signature=signature,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "OrderService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if not isinstance(result, PaymentHandlingResult):
            raise AppIntegrityError(
                "Handle-payment service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        logger.info(
            {
                "event": "handle_payment_command_completed",
                "event_id": result.event.event_id,
                "order_id": result.order.order_id,
                "payment_status": result.event.status,
                "order_state": result.order.state.value,
                "state_changed": result.state_changed,
            }
        )
        return result


__all__ = ["HandlePayment"]