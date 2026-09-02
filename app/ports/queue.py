"""
Provider-neutral asynchronous job queue port for BIMAP.

``contracts.audit_job.AuditJob`` is the authoritative work envelope. This module
does not redefine audit-job fields, serialize arbitrary Python object graphs, or
embed broker-specific delivery models. It validates one canonical AuditJob,
submits it through a concrete adapter, and binds the receipt to the same job and
idempotency identity.

The AuditJob contract assigns submission idempotency, retry policy,
cancellation, and persistence to the queue/application layer. This port exposes
the submission primitive only. Broker-level destructive cancellation is not
assumed because production brokers differ materially once a message is leased
or delivered; cooperative cancellation can remain an application/worker concern.

No automatic retry occurs here. Timeout/unavailability exceptions carry
``retryable=True`` so an owning service or worker can apply an idempotent policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.audit_job import AuditJob
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Queue Port")
printer = PrettyPrinter()

_COMPONENT = "queue"


@dataclass(frozen=True, slots=True)
class QueueReceipt:
    """
    Provider-neutral acknowledgement of one accepted AuditJob.

    ``queue_reference`` is an opaque provider/adapter correlation value. It does
    not replace the authoritative ``job_id``.
    """

    job_id: str
    queue_reference: str
    idempotency_key: str

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating queue submission receipt",
            event="queue_receipt_validate_start",
            context={"job_id": self.job_id},
        )
        object.__setattr__(
            self,
            "job_id",
            require_app_text(
                self.job_id,
                field="job_id",
                error_type=QueueValidationError,
                component=_COMPONENT,
                operation="validate_receipt",
            ),
        )
        object.__setattr__(
            self,
            "queue_reference",
            require_app_text(
                self.queue_reference,
                field="queue_reference",
                error_type=QueueValidationError,
                component=_COMPONENT,
                operation="validate_receipt",
                max_length=1024,
            ),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            require_app_text(
                self.idempotency_key,
                field="idempotency_key",
                error_type=QueueValidationError,
                component=_COMPONENT,
                operation="validate_receipt",
                max_length=512,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        """Return deterministic queue acknowledgement metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing queue submission receipt",
            event="queue_receipt_to_dict_start",
            context={"job_id": self.job_id},
        )
        return {
            "job_id": self.job_id,
            "queue_reference": self.queue_reference,
            "idempotency_key": self.idempotency_key,
        }


class Queue(ABC):
    """Abstract application dependency for idempotent AuditJob submission."""

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing queue port",
            event="queue_init_start",
        )
        logger.debug(
            {
                "event": "queue_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _enqueue(
        self,
        job: AuditJob,
        *,
        idempotency_key: str,
    ) -> QueueReceipt:
        """Submit one validated AuditJob through the concrete queue adapter."""
        raise NotImplementedError

    def enqueue(
        self,
        job: AuditJob,
        *,
        idempotency_key: str | None = None,
    ) -> QueueReceipt:
        """
        Submit one canonical AuditJob and return a bound receipt.

        If no separate idempotency key is supplied, the stable ``job_id`` is
        used. Retries of the same immutable work envelope therefore preserve one
        deduplication identity without inventing a second key.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Enqueuing audit job",
            event="queue_enqueue_start",
            context={"job_id": getattr(job, "job_id", None)},
        )
        if not isinstance(job, AuditJob):
            raise QueueValidationError(
                "enqueue() requires an AuditJob contract.",
                component=_COMPONENT,
                operation="enqueue",
                field="job",
                context={"received_type": type(job).__name__},
            )

        key = require_app_text(
            job.job_id if idempotency_key is None else idempotency_key,
            field="idempotency_key",
            error_type=QueueValidationError,
            component=_COMPONENT,
            operation="enqueue",
            max_length=512,
        )

        try:
            result = self._enqueue(job, idempotency_key=key)
        except QueueError:
            raise
        except TimeoutError as exc:
            raise QueueTimeoutError(
                "Queue submission timed out.",
                component=_COMPONENT,
                operation="enqueue",
                context={"job_id": job.job_id},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise QueueUnavailableError(
                "Queue backend is unavailable.",
                component=_COMPONENT,
                operation="enqueue",
                context={"job_id": job.job_id},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise QueueOperationError(
                "Queue adapter failed while submitting an audit job.",
                component=_COMPONENT,
                operation="enqueue",
                context={
                    "job_id": job.job_id,
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(result, QueueReceipt):
            raise QueueValidationError(
                "Queue adapter returned an unsupported receipt type.",
                component=_COMPONENT,
                operation="enqueue",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.job_id != job.job_id:
            raise QueueIntegrityError(
                "Queue receipt belongs to a different audit job.",
                component=_COMPONENT,
                operation="enqueue",
                field="result.job_id",
                context={
                    "submitted_job_id": job.job_id,
                    "returned_job_id": result.job_id,
                },
            )
        if result.idempotency_key != key:
            raise QueueIntegrityError(
                "Queue receipt does not preserve the submission idempotency key.",
                component=_COMPONENT,
                operation="enqueue",
                field="result.idempotency_key",
                context={"job_id": job.job_id},
            )

        product_code = getattr(job.product_code, "value", str(job.product_code))
        logger.info(
            {
                "event": "audit_job_enqueued",
                "job_id": job.job_id,
                "order_id": job.order_id,
                "product_code": product_code,
            }
        )
        return result


__all__ = [
    "QueueReceipt",
    "Queue",
]