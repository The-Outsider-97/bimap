"""
Enqueue-audit application command for BIMAP.

``AuditService.enqueue_audit`` intentionally refuses to mutate order lifecycle
state: its contract requires the authoritative order to already be ``QUEUED``.
``OrderService`` is the application owner of lifecycle transitions.

This command is therefore the correct Level-5 coordination point between the two
sibling services:

1. load/transition the AuditJob's order to the canonical ``QUEUED`` state;
2. submit the immutable ``AuditJob`` through ``AuditService`` and the configured
   queue port.

The same caller-supplied idempotency key is used for both application actions.
If the order transition succeeds but broker submission fails, retrying the
command with the same immutable job/key replays the existing transition and
resubmits idempotently.  This does not claim atomicity: the current architecture
has no transactional outbox spanning repository and queue.

The command never constructs evidence references, derives storage keys, creates
an AuditJob from raw uploads, or runs the audit synchronously.
"""

from __future__ import annotations

from ..ports.queue import QueueReceipt
from ..services.audit_service import AuditService
from ..services.order_service import OrderService
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.audit_job import AuditJob
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Enqueue Audit Command")
printer = PrettyPrinter()

_COMPONENT = "enqueue_audit_command"


class EnqueueAudit:
    """Transition an eligible order to ``QUEUED`` and submit its AuditJob."""

    __slots__ = ("_order_service", "_audit_service")

    def __init__(self, order_service: OrderService, audit_service: AuditService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing enqueue-audit command",
            event="enqueue_audit_command_init_start",
        )

        if not isinstance(order_service, OrderService):
            raise AppConfigurationError(
                "order_service must be an OrderService.",
                component=_COMPONENT,
                operation="initialize",
                field="order_service",
                context={"received_type": type(order_service).__name__},
            )
        if not isinstance(audit_service, AuditService):
            raise AppConfigurationError(
                "audit_service must be an AuditService.",
                component=_COMPONENT,
                operation="initialize",
                field="audit_service",
                context={"received_type": type(audit_service).__name__},
            )

        self._order_service = order_service
        self._audit_service = audit_service

        logger.debug(
            {
                "event": "enqueue_audit_command_initialized",
                "order_service_type": type(order_service).__name__,
                "audit_service_type": type(audit_service).__name__,
            }
        )

    def execute(
        self,
        job: AuditJob,
        *,
        idempotency_key: str,
        actor: str | None = None,
    ) -> QueueReceipt:
        """
        Queue one canonical AuditJob after establishing ``QUEUED`` order state.

        ``AuditService`` performs the authoritative job/order product and version
        binding before queue submission.  This command therefore does not
        duplicate those checks.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing enqueue-audit command",
            event="enqueue_audit_command_execute_start",
            context={"job_id": getattr(job, "job_id", None)},
        )

        if not isinstance(job, AuditJob):
            raise UnsupportedAppInputError(
                "enqueue-audit command requires an AuditJob contract.",
                component=_COMPONENT,
                operation="execute",
                field="job",
                context={"received_type": type(job).__name__},
            )

        try:
            order = self._order_service.get_order(job.order_id)

            # A previous successful attempt may already have established QUEUED
            # state before a queue outage.  In that case do not manufacture a
            # second transition; AuditService will still validate job binding.
            if order.state is not OrderState.QUEUED:
                order = self._order_service.transition(
                    job.order_id,
                    OrderState.QUEUED,
                    idempotency_key=idempotency_key,
                    actor=actor,
                )

            if not isinstance(order, Order) or order.state is not OrderState.QUEUED:
                raise AppIntegrityError(
                    "OrderService did not establish the canonical queued state.",
                    component=_COMPONENT,
                    operation="execute",
                    field="order.state",
                    context={
                        "job_id": job.job_id,
                        "order_id": job.order_id,
                        "returned_type": type(order).__name__,
                        "returned_state": getattr(
                            getattr(order, "state", None),
                            "value",
                            None,
                        ),
                    },
                )

            receipt = self._audit_service.enqueue_audit(
                job,
                idempotency_key=idempotency_key,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Enqueue-audit coordination failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={
                    "job_id": job.job_id,
                    "order_id": job.order_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if not isinstance(receipt, QueueReceipt):
            raise AppIntegrityError(
                "AuditService returned an unsupported queue receipt type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(receipt).__name__},
            )

        logger.info(
            {
                "event": "enqueue_audit_command_completed",
                "job_id": job.job_id,
                "order_id": job.order_id,
                "queue_idempotency_preserved": receipt.idempotency_key == idempotency_key,
            }
        )
        return receipt


__all__ = ["EnqueueAudit"]