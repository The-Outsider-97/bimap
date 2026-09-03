"""
Stable execution boundary for BIMAP worker operations.

``workers/`` is an outer execution-adapter layer.  Individual job modules own
thin, typed adapters around application commands/services; ``WorkerEngine``
adds one common process-level execution boundary around those adapters without
reimplementing any application, audit, governance, reporting, retention, or
deletion semantics.

The engine is deliberately transport-neutral.  It does not receive broker
messages, acknowledge queue deliveries, schedule jobs, sleep, retry, apply
backoff, dead-letter work, or deserialize arbitrary provider envelopes.  Those
concerns belong to a concrete worker-process / queue adapter composed above this
package.  The current BIMAP queue port exposes submission only, so consumer-side
broker semantics are intentionally not fabricated here.

Execution policy
----------------
* one stable worker job-type vocabulary is used for the currently implemented
  worker jobs: audit, report, retention, and deletion;
* callbacks are executed exactly once per ``execute()`` call;
* failures are normalized into ``WorkerError`` values and returned as an
  immutable ``WorkerExecutionResult``;
* retryability is preserved as metadata only; no automatic retry occurs;
* elapsed time uses ``time.perf_counter_ns()`` so duration measurement is
  monotonic and independent from wall-clock changes;
* result payloads are never serialized by the engine.  Operational summaries
  expose only result type and safe error metadata.
"""

from __future__ import annotations

import math
import time

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar, cast

from .utils.workers_errors import *
from .utils.workers_helpers import *
from ..app.utils.app_errors import AppError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Worker Engine")
printer = PrettyPrinter()

_COMPONENT = "worker_engine"
T = TypeVar("T")


class WorkerJobType(str, Enum):
    """Stable worker-level identities for the currently implemented job kinds."""

    AUDIT = "audit"
    REPORT = "report"
    RETENTION = "retention"
    DELETION = "deletion"

    @classmethod
    def parse(cls, value: "WorkerJobType | str") -> "WorkerJobType":
        """Normalize a supported worker job-type value."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Parsing worker job type",
            event="worker_engine_job_type_parse_start",
            context={"received_type": type(value).__name__},
        )
        if isinstance(value, cls):
            return value

        normalized = require_worker_text(
            value,
            field="job_type",
            component=_COMPONENT,
            operation="parse_job_type",
        ).casefold()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise WorkerValidationError(
                "Unsupported BIMAP worker job type.",
                component=_COMPONENT,
                operation="parse_job_type",
                field="job_type",
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc

    def __str__(self) -> str:
        return self.value


def _normalize_duration_ms(value: Any, *, operation: str) -> float:
    """Validate an observed non-negative finite elapsed duration."""
    announce_worker_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating worker execution duration",
        event="worker_engine_duration_validate_start",
        context={"operation": operation},
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkerValidationError(
            "Worker execution duration must be numeric.",
            component=_COMPONENT,
            operation=operation,
            field="duration_ms",
            context={"received_type": type(value).__name__},
        )
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise WorkerValidationError(
            "Worker execution duration must be finite and non-negative.",
            component=_COMPONENT,
            operation=operation,
            field="duration_ms",
            context={"received": normalized},
        )
    return normalized


@dataclass(frozen=True, slots=True)
class WorkerExecutionResult(Generic[T]):
    """
    Immutable process-level result of exactly one worker callback invocation.

    ``result`` is retained in-memory for the process owner but is intentionally
    excluded from :meth:`to_dict`.  Worker outputs can contain domain objects,
    report metadata, or other typed application results that should not be
    serialized by an outer execution helper without an explicit contract.
    """

    job_type: WorkerJobType | str
    operation: str
    succeeded: bool
    duration_ms: float
    job_id: str | None = None
    result: T | None = None
    error: WorkerError | None = None

    def __post_init__(self) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating worker execution result",
            event="worker_engine_result_validate_start",
            context={
                "job_type": str(self.job_type),
                "operation": self.operation,
                "succeeded": self.succeeded,
            },
        )

        job_type = WorkerJobType.parse(self.job_type)
        operation = require_worker_text(
            self.operation,
            field="operation",
            component=_COMPONENT,
            operation="validate_result",
            max_length=128,
        )
        job_id = optional_worker_text(
            self.job_id,
            field="job_id",
            component=_COMPONENT,
            operation="validate_result",
            max_length=512,
        )
        duration_ms = _normalize_duration_ms(
            self.duration_ms,
            operation="validate_result",
        )

        if not isinstance(self.succeeded, bool):
            raise WorkerValidationError(
                "succeeded must be boolean.",
                component=_COMPONENT,
                operation="validate_result",
                field="succeeded",
                context={"received_type": type(self.succeeded).__name__},
            )

        if self.succeeded:
            if self.error is not None:
                raise WorkerIntegrityError(
                    "Successful worker execution result cannot contain an error.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="error",
                    job_type=job_type.value,
                    job_id=job_id,
                )
        else:
            if not isinstance(self.error, WorkerError):
                raise WorkerIntegrityError(
                    "Failed worker execution result must contain a WorkerError.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="error",
                    job_type=job_type.value,
                    job_id=job_id,
                    context={"received_type": type(self.error).__name__},
                )
            if self.result is not None:
                raise WorkerIntegrityError(
                    "Failed worker execution result cannot expose a successful result payload.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="result",
                    job_type=job_type.value,
                    job_id=job_id,
                    context={"received_type": type(self.result).__name__},
                )

        object.__setattr__(self, "job_type", job_type)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "duration_ms", duration_ms)

    @property
    def failed(self) -> bool:
        """Return whether the callback failed."""
        return not self.succeeded

    @property
    def retryable(self) -> bool:
        """Return retryability metadata from the normalized failure, if any."""
        return False if self.error is None else worker_failure_retryable(self.error)

    @property
    def error_code(self) -> str | None:
        """Return a stable worker failure code when execution failed."""
        return None if self.error is None else worker_failure_code(self.error)

    @property
    def result_type(self) -> str | None:
        """Return the runtime result type without exposing the result payload."""
        return None if self.result is None else type(self.result).__name__

    def unwrap(self) -> T:
        """Return the callback result or re-raise the normalized worker failure."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Unwrapping worker execution result",
            event="worker_engine_result_unwrap_start",
            context={
                "job_type": WorkerJobType.parse(self.job_type).value,
                "job_id": self.job_id,
            },
        )
        if self.error is not None:
            raise self.error
        return cast(T, self.result)

    def to_dict(self, *, include_error_context: bool = False) -> dict[str, Any]:
        """Return content-safe operational execution metadata."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing worker execution metadata",
            event="worker_engine_result_to_dict_start",
            context={
                "job_type": WorkerJobType.parse(self.job_type).value,
                "job_id": self.job_id,
            },
        )
        payload: dict[str, Any] = {
            "job_type": WorkerJobType.parse(self.job_type).value,
            "operation": self.operation,
            "job_id": self.job_id,
            "succeeded": self.succeeded,
            "duration_ms": self.duration_ms,
            "result_type": self.result_type,
            "retryable": self.retryable,
            "error_code": self.error_code,
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict(
                include_context=include_error_context,
                include_cause_type=True,
            )
        else:
            payload["error"] = None
        return payload


class WorkerEngine:
    """Execute one worker callback through the shared BIMAP process boundary."""

    __slots__ = ()

    def __init__(self) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing worker execution engine",
            event="worker_engine_init_start",
        )
        logger.info({"event": "worker_engine_initialized"})

    @staticmethod
    def _normalize_failure(
        exc: Exception,
        *,
        job_type: WorkerJobType,
        operation: str,
        job_id: str | None,
        context: Mapping[str, Any] | None,
    ) -> WorkerError:
        """Translate an escaping non-worker exception into worker vocabulary."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Normalizing worker execution failure",
            event="worker_engine_failure_normalize_start",
            context={
                "job_type": job_type.value,
                "operation": operation,
                "job_id": job_id,
            },
        )

        common_context = dict(context or {})
        common_context.update(lower_worker_error_context(exc))

        if isinstance(exc, AppError):
            return WorkerDependencyError(
                "Application dependency failed during worker execution.",
                component=_COMPONENT,
                operation=operation,
                job_type=job_type.value,
                job_id=job_id,
                retryable=worker_failure_retryable(exc),
                context=common_context,
                cause=exc,
            )
        if isinstance(exc, TimeoutError):
            return WorkerDependencyTimeoutError(
                "Worker dependency timed out.",
                component=_COMPONENT,
                operation=operation,
                job_type=job_type.value,
                job_id=job_id,
                context=common_context,
                cause=exc,
            )
        if isinstance(exc, ConnectionError):
            return WorkerDependencyUnavailableError(
                "Worker dependency is unavailable.",
                component=_COMPONENT,
                operation=operation,
                job_type=job_type.value,
                job_id=job_id,
                context=common_context,
                cause=exc,
            )
        return WorkerExecutionError(
            "Unexpected exception escaped the worker job boundary.",
            component=_COMPONENT,
            operation=operation,
            job_type=job_type.value,
            job_id=job_id,
            retryable=False,
            context=common_context,
            cause=exc,
        )

    def execute(
        self,
        job_type: WorkerJobType | str,
        operation: str,
        callback: Callable[[], T],
        *,
        job_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> WorkerExecutionResult[T]:
        """Execute ``callback`` exactly once and return a normalized outcome."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing worker callback",
            event="worker_engine_execute_start",
            context={
                "job_type": str(job_type),
                "operation": operation,
                "job_id": job_id,
            },
        )

        normalized_type = WorkerJobType.parse(job_type)
        normalized_operation = require_worker_text(
            operation,
            field="operation",
            component=_COMPONENT,
            operation="execute",
            max_length=128,
        )
        normalized_job_id = optional_worker_text(
            job_id,
            field="job_id",
            component=_COMPONENT,
            operation="execute",
            max_length=512,
        )
        if not callable(callback):
            raise WorkerValidationError(
                "callback must be callable.",
                component=_COMPONENT,
                operation="execute",
                field="callback",
                job_type=normalized_type.value,
                job_id=normalized_job_id,
                context={"received_type": type(callback).__name__},
            )
        if context is not None and not isinstance(context, Mapping):
            raise WorkerValidationError(
                "context must be a mapping or None.",
                component=_COMPONENT,
                operation="execute",
                field="context",
                job_type=normalized_type.value,
                job_id=normalized_job_id,
                context={"received_type": type(context).__name__},
            )

        started_ns = time.perf_counter_ns()
        result: T | None = None
        error: WorkerError | None = None

        try:
            result = callback()
        except WorkerError as exc:
            error = exc
        except Exception as exc:
            error = self._normalize_failure(
                exc,
                job_type=normalized_type,
                operation=normalized_operation,
                job_id=normalized_job_id,
                context=context,
            )

        duration_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0

        if error is not None:
            # The engine is the boundary that converts failure into an outcome,
            # so this is the single appropriate operator-facing announcement.
            error.announce(label="WORKER", level="error")
            logger.warning(
                {
                    "event": "worker_execution_failed",
                    "job_type": normalized_type.value,
                    "operation": normalized_operation,
                    "job_id": normalized_job_id,
                    "duration_ms": duration_ms,
                    "error_code": worker_failure_code(error),
                    "retryable": worker_failure_retryable(error),
                }
            )
            return WorkerExecutionResult(
                job_type=normalized_type,
                operation=normalized_operation,
                job_id=normalized_job_id,
                succeeded=False,
                duration_ms=duration_ms,
                error=error,
            )

        logger.info(
            {
                "event": "worker_execution_completed",
                "job_type": normalized_type.value,
                "operation": normalized_operation,
                "job_id": normalized_job_id,
                "duration_ms": duration_ms,
                "result_type": None if result is None else type(result).__name__,
            }
        )
        return WorkerExecutionResult(
            job_type=normalized_type,
            operation=normalized_operation,
            job_id=normalized_job_id,
            succeeded=True,
            duration_ms=duration_ms,
            result=result,
        )

    def execute_or_raise(
        self,
        job_type: WorkerJobType | str,
        operation: str,
        callback: Callable[[], T],
        *,
        job_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> T:
        """Execute once and return the result, re-raising normalized failure."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing worker callback with raise-on-failure semantics",
            event="worker_engine_execute_or_raise_start",
            context={"job_type": str(job_type), "job_id": job_id},
        )
        return self.execute(
            job_type,
            operation,
            callback,
            job_id=job_id,
            context=context,
        ).unwrap()


__all__ = [
    "WorkerJobType",
    "WorkerExecutionResult",
    "WorkerEngine",
]


if __name__ == "__main__":
    print("\n=== Running Worker Engine Self-Test ===\n")
    printer.status("TEST", "Worker engine module initialized", "info")

    engine = WorkerEngine()
    success = engine.execute(
        WorkerJobType.AUDIT,
        "self_test",
        lambda: "ok",
        job_id="JOB-SELF-TEST",
    )
    assert success.succeeded is True
    assert success.unwrap() == "ok"
    assert success.retryable is False

    failure = engine.execute(
        WorkerJobType.REPORT,
        "self_test",
        lambda: (_ for _ in ()).throw(TimeoutError("timeout")),
        job_id="REPORT-SELF-TEST",
    )
    assert failure.failed is True
    assert failure.retryable is True
    assert failure.error_code == WorkerDependencyTimeoutError.code

    printer.status("PASS", "Worker engine execution normalization", "success")
    print("\n=== Test ran successfully ===\n")