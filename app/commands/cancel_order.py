"""
Cancel-order application command for BIMAP.

Cancellation is an application use case, but lifecycle legality is not owned by
this module.  ``OrderService.cancel_order`` delegates the actual transition to
the canonical ``OrderTransitions`` authority and persists the resulting
aggregate with optimistic concurrency.

This command therefore does not infer whether cancellation is commercially
appropriate, initiate payment-provider refunds, delete uploads, or bypass the
order-state graph.  The current domain graph models ``CANCELLED`` as a
pre-payment termination; paid/post-payment termination is represented
separately as ``REFUNDED`` and requires explicit higher-level commercial policy.

The caller-supplied idempotency key is preserved unchanged so the domain/service
layers can provide their existing replay semantics.
"""

from __future__ import annotations

from ..services.order_service import OrderService
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Cancel Order Command")
printer = PrettyPrinter()

_COMPONENT = "cancel_order_command"


class CancelOrder:
    """Execute the canonical pre-payment order-cancellation use case."""

    __slots__ = ("_service",)

    def __init__(self, service: OrderService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing cancel-order command",
            event="cancel_order_command_init_start",
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
                "event": "cancel_order_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        actor: str | None = None,
    ) -> Order:
        """
        Request the canonical ``CANCELLED`` transition for one order.

        ``reason`` and ``actor`` are passed to the authoritative lifecycle event
        machinery but are intentionally omitted from command diagnostics.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing cancel-order command",
            event="cancel_order_command_execute_start",
            context={"order_id": order_id},
        )

        try:
            result = self._service.cancel_order(
                order_id,
                idempotency_key=idempotency_key,
                reason=reason,
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

        if not isinstance(result, Order):
            raise AppIntegrityError(
                "Cancel-order service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        if result.state is not OrderState.CANCELLED:
            raise AppIntegrityError(
                "Cancel-order command completed without the canonical cancelled state.",
                component=_COMPONENT,
                operation="execute",
                field="result.state",
                context={
                    "order_id": result.order_id,
                    "returned_state": result.state.value,
                },
            )

        logger.info(
            {
                "event": "cancel_order_command_completed",
                "order_id": result.order_id,
                "state": result.state.value,
                "version": result.version,
            }
        )
        return result


__all__ = ["CancelOrder"]