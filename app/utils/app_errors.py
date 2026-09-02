"""
Structured application/port error hierarchy for BIMAP.

``app/ports`` is BIMAP's dependency-inversion boundary.  Port interfaces may be
implemented by local, cloud, provider, or test adapters, but the application
layer must observe one stable BIMAP-owned failure vocabulary rather than SDK-
specific exceptions.

This module deliberately sits at the bottom of the application-port dependency
graph.  It imports no domain model, contract, concrete adapter, API, worker,
audit-engine, reporting, or SLAI implementation.

Operational policy
------------------
* Exception construction has no logging side effects.  A handling boundary may
  call :meth:`AppError.announce` once when operator-facing status is useful.
* ``code`` and ``retryable`` are stable machine-readable properties.  They are
  metadata, not an automatic retry policy; application services/workers remain
  responsible for deciding whether and when retries are safe.
* Diagnostic context is bounded and redacted.  Raw uploads, file contents,
  credentials, signed URLs, storage paths, tokens, and provider payloads must
  not be copied into logs through exception metadata.
* Concrete adapter exceptions may be retained through ``cause`` for exception
  chaining.  ``to_dict()`` exposes only the cause type.
* A positive malware finding is not an exception.  It is a valid scan result and
  must be represented by the malware port's verdict model.  Exceptions are
  reserved for invalid input, malformed adapter output, or scanner operation
  failures.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Application Errors")
printer = PrettyPrinter()

_REDACTED = "<redacted>"
_MAX_CONTEXT_DEPTH = 3
_MAX_CONTEXT_ITEMS = 32
_MAX_CONTEXT_STRING = 256
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "body",
    "content",
    "cookie",
    "credential",
    "file_bytes",
    "filepath",
    "filename",
    "object_key",
    "password",
    "path",
    "payload",
    "presigned",
    "raw",
    "secret",
    "session",
    "signed_url",
    "storage_key",
    "stream",
    "token",
    "uri",
    "url",
)


def _is_sensitive_key(key: str) -> bool:
    """Return whether an application diagnostic key should be redacted."""
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded representation suitable for application diagnostics."""
    if depth >= _MAX_CONTEXT_DEPTH:
        return f"<{type(value).__name__}>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        if len(value) <= _MAX_CONTEXT_STRING:
            return value
        return f"{value[:_MAX_CONTEXT_STRING]}…"

    if isinstance(value, Mapping):
        rendered: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTEXT_ITEMS:
                rendered["__truncated__"] = True
                break
            text_key = str(key)
            rendered[text_key] = (
                _REDACTED
                if _is_sensitive_key(text_key)
                else _safe_context_value(item, depth=depth + 1)
            )
        return rendered

    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = list(value)
        rendered_sequence = [
            _safe_context_value(item, depth=depth + 1)
            for item in sequence[:_MAX_CONTEXT_ITEMS]
        ]
        if len(sequence) > _MAX_CONTEXT_ITEMS:
            rendered_sequence.append("<truncated>")
        return rendered_sequence

    return f"<{type(value).__name__}>"


def sanitize_app_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize diagnostic context without exposing upload/provider content."""
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise TypeError(
            "Application error context must be a mapping or None, "
            f"got {type(context).__name__}."
        )

    safe: dict[str, Any] = {}
    for key, value in context.items():
        text_key = str(key)
        safe[text_key] = (
            _REDACTED
            if _is_sensitive_key(text_key)
            else _safe_context_value(value)
        )
    return safe


class AppError(Exception):
    """Base exception for BIMAP application-layer and port-boundary failures."""

    code = "BIMAP.APP.ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        operation: str | None = None,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        self.message = normalized_message
        self.component = str(component).strip() if component is not None else None
        self.operation = str(operation).strip() if operation is not None else None
        self.field = str(field).strip() if field is not None else None
        self.context = sanitize_app_context(context)
        self.cause = cause

        qualifiers: list[str] = []
        if self.component:
            qualifiers.append(f"component={self.component}")
        if self.operation:
            qualifiers.append(f"operation={self.operation}")
        if self.field:
            qualifiers.append(f"field={self.field}")

        rendered = normalized_message
        if qualifiers:
            rendered = f"{rendered} [{', '.join(qualifiers)}]"
        super().__init__(rendered)

    def announce(
        self,
        *,
        label: str = "APPLICATION",
        level: str = "error",
    ) -> None:
        """Explicitly emit one operator-facing status for a handled failure."""
        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "application_error_announced",
                "code": self.code,
                "type": self.__class__.__name__,
                "component": self.component,
                "operation": self.operation,
                "field": self.field,
                "retryable": bool(self.retryable),
            }
        )

    def to_dict(
        self,
        *,
        include_context: bool = True,
        include_cause_type: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic, logging-safe error representation."""
        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.component:
            payload["component"] = self.component
        if self.operation:
            payload["operation"] = self.operation
        if self.field:
            payload["field"] = self.field
        if include_context and self.context:
            payload["context"] = dict(self.context)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__
        return payload


# ---------------------------------------------------------------------------
# Cross-application primitives
# ---------------------------------------------------------------------------


class AppConfigurationError(AppError):
    """Raised when an application component is configured inconsistently."""

    code = "BIMAP.APP.CONFIGURATION"


class AppValidationError(AppError):
    """Raised when application input violates a boundary precondition."""

    code = "BIMAP.APP.VALIDATION"


class AppIntegrityError(AppError):
    """Raised when accepted application state is internally contradictory."""

    code = "BIMAP.APP.INTEGRITY"


class AppSerializationError(AppError):
    """Raised when application metadata cannot be represented deterministically."""

    code = "BIMAP.APP.SERIALIZATION"


class UnsupportedAppInputError(AppValidationError):
    """Raised when a port receives an unsupported object type."""

    code = "BIMAP.APP.INPUT.UNSUPPORTED"


# ---------------------------------------------------------------------------
# Generic port boundary
# ---------------------------------------------------------------------------


class AppPortError(AppError):
    """Base class for failures observed through an application port."""

    code = "BIMAP.APP.PORT"


class AppPortOperationError(AppPortError):
    """Raised when a concrete adapter cannot complete a requested operation."""

    code = "BIMAP.APP.PORT.OPERATION"


class AppPortUnavailableError(AppPortOperationError):
    """Raised when a required external/local adapter dependency is unavailable."""

    code = "BIMAP.APP.PORT.UNAVAILABLE"
    retryable = True


class AppPortTimeoutError(AppPortOperationError):
    """Raised when a port operation exceeds the adapter's configured time budget."""

    code = "BIMAP.APP.PORT.TIMEOUT"
    retryable = True


# ---------------------------------------------------------------------------
# Clock port
# ---------------------------------------------------------------------------


class ClockError(AppPortError):
    """Base class for clock-port failures."""

    code = "BIMAP.APP.PORT.CLOCK"


class ClockValidationError(ClockError):
    """Raised when a clock value or temporal argument is invalid."""

    code = "BIMAP.APP.PORT.CLOCK.VALIDATION"


class ClockReadError(ClockError):
    """Raised when a clock implementation cannot provide a valid current time."""

    code = "BIMAP.APP.PORT.CLOCK.READ"


# ---------------------------------------------------------------------------
# Malware-scanning port
# ---------------------------------------------------------------------------


class MalwareError(AppPortError):
    """Base class for malware-port failures."""

    code = "BIMAP.APP.PORT.MALWARE"


class MalwareValidationError(MalwareError):
    """Raised for invalid malware-scan inputs or malformed scanner output."""

    code = "BIMAP.APP.PORT.MALWARE.VALIDATION"


class MalwareScanError(MalwareError):
    """Raised when a malware scanner fails to complete an operation."""

    code = "BIMAP.APP.PORT.MALWARE.SCAN"


class MalwareUnavailableError(MalwareScanError):
    """Raised when the configured scanner service/engine is unavailable."""

    code = "BIMAP.APP.PORT.MALWARE.UNAVAILABLE"
    retryable = True


class MalwareTimeoutError(MalwareScanError):
    """Raised when malware scanning exceeds the adapter's configured time budget."""

    code = "BIMAP.APP.PORT.MALWARE.TIMEOUT"
    retryable = True


__all__ = [
    "sanitize_app_context",
    "AppError",
    "AppConfigurationError",
    "AppValidationError",
    "AppIntegrityError",
    "AppSerializationError",
    "UnsupportedAppInputError",
    "AppPortError",
    "AppPortOperationError",
    "AppPortUnavailableError",
    "AppPortTimeoutError",
    "ClockError",
    "ClockValidationError",
    "ClockReadError",
    "MalwareError",
    "MalwareValidationError",
    "MalwareScanError",
    "MalwareUnavailableError",
    "MalwareTimeoutError",
]


if __name__ == "__main__":
    print("\n=== Running Application Errors Self-Test ===\n")
    printer.status("TEST", "Application errors module initialized", "info")

    error = MalwareScanError(
        "Scanner failure.",
        component="malware",
        operation="scan",
        context={
            "object_id": "OBJ-1",
            "filename": "sensitive-project.rvt",
            "payload": b"not-for-logs",
        },
        cause=RuntimeError("provider detail"),
    )
    payload = error.to_dict()
    assert payload["code"] == "BIMAP.APP.PORT.MALWARE.SCAN"
    assert payload["context"]["filename"] == _REDACTED
    assert payload["context"]["payload"] == _REDACTED
    assert payload["cause_type"] == "RuntimeError"
    printer.status("PASS", "Application error redaction and structure", "success")

    print("\n=== Test ran successfully ===\n")
