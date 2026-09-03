"""
Structured worker-layer error hierarchy for BIMAP.

``workers/`` is an outer execution-adapter layer. It converts failures from
application services/commands and runtime dependencies into a stable worker
vocabulary for ``workers/runner.py``, queue adapters, process supervisors,
metrics, and operational alerting.

The worker layer must not redefine application/domain business errors. Lower
``AppError`` instances remain the authoritative application failure; worker
errors wrap them only to add execution-context semantics.

Operational rules
-----------------
* Exception construction has no logging side effect. Call ``announce()`` only at
  the handling boundary that owns operator-facing reporting.
* ``code`` and ``retryable`` are metadata only. This module never performs
  retries, backoff, queue acknowledgement, or dead-letter routing.
* Context reuses BIMAP's application redaction policy. Raw uploads/report bytes,
  credentials, storage paths, signatures, tokens, signed URLs, and provider
  payloads must never be copied into worker diagnostics.
* Lower exception messages are not copied into machine-readable diagnostics.
* Normal no-ops, such as a retention check before expiry, are not errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...app.utils.app_errors import sanitize_app_context
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Worker Errors")
printer = PrettyPrinter()


class WorkerError(Exception):
    """Base class for BIMAP worker-layer failures."""

    code = "BIMAP.WORKER.ERROR"
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        operation: str | None = None,
        field: str | None = None,
        job_type: str | None = None,
        job_id: str | None = None,
        retryable: bool | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        self.message = normalized_message
        self.component = str(component).strip() if component is not None else None
        self.operation = str(operation).strip() if operation is not None else None
        self.field = str(field).strip() if field is not None else None
        self.job_type = str(job_type).strip() if job_type is not None else None
        self.job_id = str(job_id).strip() if job_id is not None else None
        self.retryable = (
            bool(self.default_retryable) if retryable is None else bool(retryable)
        )
        self.context = sanitize_app_context(context)
        self.cause = cause

        qualifiers: list[str] = []
        if self.component:
            qualifiers.append(f"component={self.component}")
        if self.operation:
            qualifiers.append(f"operation={self.operation}")
        if self.field:
            qualifiers.append(f"field={self.field}")
        if self.job_type:
            qualifiers.append(f"job_type={self.job_type}")

        rendered = normalized_message
        if qualifiers:
            rendered = f"{rendered} [{', '.join(qualifiers)}]"
        super().__init__(rendered)

    def announce(self, *, label: str = "WORKER", level: str = "error") -> None:
        """Explicitly emit one operator-facing status for a handled failure."""
        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "worker_error_announced",
                "code": self.code,
                "type": self.__class__.__name__,
                "component": self.component,
                "operation": self.operation,
                "field": self.field,
                "job_type": self.job_type,
                "job_id": self.job_id,
                "retryable": self.retryable,
            }
        )

    def to_dict(
        self,
        *,
        include_context: bool = True,
        include_cause_type: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic, content-safe failure representation."""
        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.component:
            payload["component"] = self.component
        if self.operation:
            payload["operation"] = self.operation
        if self.field:
            payload["field"] = self.field
        if self.job_type:
            payload["job_type"] = self.job_type
        if self.job_id:
            payload["job_id"] = self.job_id
        if include_context and self.context:
            payload["context"] = dict(self.context)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__
        return payload


class WorkerConfigurationError(WorkerError):
    code = "BIMAP.WORKER.CONFIGURATION"


class WorkerValidationError(WorkerError):
    code = "BIMAP.WORKER.VALIDATION"


class WorkerIntegrityError(WorkerError):
    code = "BIMAP.WORKER.INTEGRITY"


class WorkerExecutionError(WorkerError):
    code = "BIMAP.WORKER.EXECUTION"


class WorkerDependencyError(WorkerExecutionError):
    code = "BIMAP.WORKER.DEPENDENCY"


class WorkerDependencyUnavailableError(WorkerDependencyError):
    code = "BIMAP.WORKER.DEPENDENCY.UNAVAILABLE"
    default_retryable = True


class WorkerDependencyTimeoutError(WorkerDependencyError):
    code = "BIMAP.WORKER.DEPENDENCY.TIMEOUT"
    default_retryable = True


class WorkerAuditError(WorkerExecutionError):
    code = "BIMAP.WORKER.JOB.AUDIT"


class WorkerReportError(WorkerExecutionError):
    code = "BIMAP.WORKER.JOB.REPORT"


class WorkerRetentionError(WorkerExecutionError):
    code = "BIMAP.WORKER.JOB.RETENTION"


class WorkerDeletionError(WorkerExecutionError):
    code = "BIMAP.WORKER.JOB.DELETION"


__all__ = [
    "WorkerError",
    "WorkerConfigurationError",
    "WorkerValidationError",
    "WorkerIntegrityError",
    "WorkerExecutionError",
    "WorkerDependencyError",
    "WorkerDependencyUnavailableError",
    "WorkerDependencyTimeoutError",
    "WorkerAuditError",
    "WorkerReportError",
    "WorkerRetentionError",
    "WorkerDeletionError",
]


if __name__ == "__main__":
    print("\n=== Running Worker Errors Self-Test ===\n")
    printer.status("TEST", "Worker errors module initialized", "info")

    error = WorkerReportError(
        "Report worker failed.",
        component="report_job",
        operation="execute",
        job_type="report",
        job_id="REP-001",
        retryable=True,
        context={
            "order_id": "ORD-001",
            "signed_url": "https://example.invalid/private",
            "filename": "private-project.pdf",
        },
        cause=RuntimeError("provider detail must not leak"),
    )
    payload = error.to_dict()
    assert payload["retryable"] is True
    assert payload["context"]["signed_url"] == "<redacted>"
    assert payload["context"]["filename"] == "<redacted>"
    assert payload["cause_type"] == "RuntimeError"
    printer.status("PASS", "Worker error structure/redaction", "success")

    print("\n=== Test ran successfully ===\n")