"""
BIMAP deletion-worker execution adapter.

The current application model intentionally has no durable ``DeletionRequest``
aggregate, pending-deletion repository API, legal-hold model, or force-delete
operation. Those concepts must not be fabricated in this outer worker layer.

``app.commands.request_deletion.RequestDeletion`` is the authoritative currently
supported deletion use case. This worker executes that already-authorized
command; it does not create or persist the request itself.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.commands.request_deletion import RequestDeletion
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Job Deletion")
printer = PrettyPrinter()

_COMPONENT = "worker_deletion"


class JobDeletion:
    """Execute one already-authorized retention-governed deletion command."""

    __slots__ = ("_command",)

    def __init__(self, command: RequestDeletion) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing deletion worker job",
            event="worker_deletion_init_start",
        )
        if not isinstance(command, RequestDeletion):
            raise WorkerConfigurationError(
                "command must be a RequestDeletion application command.",
                component=_COMPONENT,
                operation="initialize",
                field="command",
                context={"received_type": type(command).__name__},
            )
        self._command = command
        logger.debug(
            {
                "event": "worker_deletion_initialized",
                "command_type": type(command).__name__,
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
        """Execute deletion only through the current authoritative command."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing deletion worker job",
            event="worker_deletion_execute_start",
            context={"order_id": order_id},
        )

        target_order_id = require_worker_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="execute",
        )
        targets = materialize_worker_iterable(
            object_ids,
            field="object_ids",
            component=_COMPONENT,
            operation="execute",
            accepted_type=str,
            allow_empty=True,
        )

        result = run_worker_dependency(
            lambda: self._command.execute(
                target_order_id,
                object_ids=targets,
                idempotency_key=idempotency_key,
                actor=actor,
            ),
            component=_COMPONENT,
            operation="execute",
            message="RequestDeletion failed while executing a deletion job.",
            context={"order_id": target_order_id, "target_object_count": len(targets)},
            error_type=WorkerDeletionError,
        )
        validated = require_worker_result(
            result,
            Order,
            component=_COMPONENT,
            operation="execute",
            message="RequestDeletion returned an unsupported deletion result.",
        )

        requested_order_id = target_order_id
        if validated.order_id != requested_order_id:
            raise WorkerIntegrityError(
                "Deletion worker result belongs to a different order.",
                component=_COMPONENT,
                operation="execute",
                field="result.order_id",
                job_type="deletion",
                context={
                    "requested_order_id": requested_order_id,
                    "returned_order_id": validated.order_id,
                },
            )
        if validated.state is not OrderState.EXPIRED:
            raise WorkerIntegrityError(
                "Successful deletion command did not return an expired order.",
                component=_COMPONENT,
                operation="execute",
                field="result.state",
                job_type="deletion",
                context={
                    "order_id": validated.order_id,
                    "returned_state": validated.state.value,
                },
            )

        logger.info(
            {
                "event": "worker_deletion_completed",
                "order_id": validated.order_id,
                "state": validated.state.value,
                "version": validated.version,
                "target_object_count": len(targets),
            }
        )
        return validated


__all__ = ["JobDeletion"]