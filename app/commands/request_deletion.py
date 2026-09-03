"""
Request-deletion application command for BIMAP.

The current BIMAP fulfilment boundary does not define an asynchronous
``DeletionRequest`` record, a persistence API for pending deletion requests, or
a force-delete policy that bypasses configured retention.  It does define one
authoritative deletion-capable application operation:
``FulfilmentService.expire_delivery_if_due``.

That operation:

* applies only to delivered/expired orders;
* requires an explicit configured retention expiry;
* deletes only caller-identified storage object IDs;
* never derives hidden storage keys from report IDs or filenames;
* transitions a due delivered order to ``EXPIRED``; and
* deliberately retains immutable report-manifest control metadata because the
  repository port exposes no hard-delete operation for it.

This command therefore implements a synchronous deletion request against that
existing retention-governed capability.  If retention is not yet due, no storage
objects are deleted and the command raises ``AppValidationError`` rather than
pretending that a durable future deletion request was recorded.

A future asynchronous/right-to-erasure workflow should introduce an explicit
request contract and persistence/worker semantics rather than overloading this
module with invented state.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..services.fulfilment_service import FulfilmentService
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Request Deletion Command")
printer = PrettyPrinter()

_COMPONENT = "request_deletion_command"


class RequestDeletion:
    """Execute one retention-governed deletion request synchronously."""

    __slots__ = ("_service",)

    def __init__(self, service: FulfilmentService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing request-deletion command",
            event="request_deletion_command_init_start",
        )

        if not isinstance(service, FulfilmentService):
            raise AppConfigurationError(
                "service must be a FulfilmentService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )

        self._service = service
        logger.debug(
            {
                "event": "request_deletion_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        *,
        object_ids: Iterable[str],
        idempotency_key: str,
        actor: str | None = None,
    ) -> Order:
        """
        Delete explicitly identified retained objects when retention is due.

        The iterable is forwarded directly to ``FulfilmentService`` so object-ID
        normalization, de-duplication, storage deletion, and lifecycle handling
        remain centralized there.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing request-deletion command",
            event="request_deletion_command_execute_start",
            context={"order_id": order_id},
        )

        try:
            result = self._service.expire_delivery_if_due(
                order_id,
                object_ids=object_ids,
                idempotency_key=idempotency_key,
                actor=actor,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "FulfilmentService failed outside the BIMAP application-error contract.",
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
                "Request-deletion service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        if result.state is not OrderState.EXPIRED:
            raise AppValidationError(
                "Deletion request was not executed because the configured retention expiry is not yet due.",
                component=_COMPONENT,
                operation="execute",
                field="order.retention_expires_at",
                context={
                    "order_id": result.order_id,
                    "current_state": result.state.value,
                    "has_retention_expiry": result.retention_expires_at is not None,
                },
            )

        logger.info(
            {
                "event": "request_deletion_command_completed",
                "order_id": result.order_id,
                "state": result.state.value,
                "version": result.version,
            }
        )
        return result


__all__ = ["RequestDeletion"]