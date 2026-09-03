"""
Scheduled BIMAP retention-worker entry.

Retention periods are not defined here. The worker receives explicit storage
object identities and delegates the due-time check, object deletion, and
``DELIVERED -> EXPIRED`` transition to
``FulfilmentService.expire_delivery_if_due``.

A retention check before configured expiry is a successful no-op, not a failure.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.services.fulfilment_service import FulfilmentService
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Job Retention")
printer = PrettyPrinter()

_COMPONENT = "worker_retention"


class JobRetention:
    """Evaluate and execute one already-configured retention deadline."""

    __slots__ = ("_service",)

    def __init__(self, service: FulfilmentService) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing retention worker job",
            event="worker_retention_init_start",
        )
        if not isinstance(service, FulfilmentService):
            raise WorkerConfigurationError(
                "service must be a FulfilmentService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )
        self._service = service
        logger.debug(
            {
                "event": "worker_retention_initialized",
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
        """Run one retention check and return the authoritative resulting order."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing retention worker job",
            event="worker_retention_execute_start",
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
            lambda: self._service.expire_delivery_if_due(
                target_order_id,
                object_ids=targets,
                idempotency_key=idempotency_key,
                actor=actor,
            ),
            component=_COMPONENT,
            operation="execute",
            message="FulfilmentService failed while evaluating delivery retention.",
            context={"order_id": target_order_id, "target_object_count": len(targets)},
            error_type=WorkerRetentionError,
        )
        validated = require_worker_result(
            result,
            Order,
            component=_COMPONENT,
            operation="execute",
            message="FulfilmentService returned an unsupported retention result.",
        )

        requested_order_id = target_order_id
        if validated.order_id != requested_order_id:
            raise WorkerIntegrityError(
                "Retention worker result belongs to a different order.",
                component=_COMPONENT,
                operation="execute",
                field="result.order_id",
                job_type="retention",
                context={
                    "requested_order_id": requested_order_id,
                    "returned_order_id": validated.order_id,
                },
            )

        expired = validated.state is OrderState.EXPIRED
        logger.info(
            {
                "event": "worker_retention_expired" if expired else "worker_retention_not_due",
                "order_id": validated.order_id,
                "state": validated.state.value,
                "version": validated.version,
                "target_object_count": len(targets),
            }
        )
        return validated


__all__ = ["JobRetention"]