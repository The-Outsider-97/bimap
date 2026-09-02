"""
Shared helpers for BIMAP application ports.

The helpers in this module provide only cross-port boundary mechanics:
method-start diagnostics, safe error translation, text/numeric validation,
UTC normalization, canonical JSON delegation, and binary-stream validation.
They intentionally do not implement business rules, retry loops, payment/storage
semantics, upload policy, malware policy, worker scheduling, or concrete SDK
behavior.

Dependency direction
--------------------
``app_helpers.py`` may import the lower-level BIMAP domain/contracts helpers and
``app_errors.py``.  Concrete ports/adapters may consume these helpers.

``app_errors.py`` must never import ``app_helpers.py``.

Time handling delegates to the canonical domain UTC helpers rather than creating
a second timestamp policy.  JSON handling likewise delegates to the contract
serialization helpers.  This preserves one normalization policy across BIMAP.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import Enum
from io import TextIOBase
from typing import Any, BinaryIO, TypeVar, cast

from ...contracts.utils.contracts_errors import ContractError
from ...contracts.utils.contracts_helpers import *
from ...domain.utils.domain_errors import DomainError
from ...domain.utils.domain_helpers import *
from .app_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Application Helpers")
printer = PrettyPrinter()

E = TypeVar("E", bound=AppError)


def announce_app_action(
    target_printer: PrettyPrinter,
    target_logger: Any,
    *,
    component: str,
    action: str,
    event: str,
    context: Mapping[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Emit one consistent, content-safe method-start diagnostic."""
    safe_context = sanitize_app_context(context)
    target_printer.status("APPLICATION", action, level)

    payload: dict[str, Any] = {
        "event": event,
        "component": component,
        "action": action,
    }
    if safe_context:
        payload["context"] = safe_context
    target_logger.debug(payload)


def lower_error_context(error: BaseException) -> dict[str, str]:
    """Return only safe lower-layer error identity for exception translation."""
    context = {"lower_error_type": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.strip():
        context["lower_error_code"] = code.strip()
    return context


def require_app_text(
    value: Any,
    *,
    field: str,
    error_type: type[E] = AppValidationError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "require_text",
    max_length: int | None = None,
) -> str:
    """Validate required application-boundary text without changing semantics."""
    if not isinstance(value, str):
        raise error_type(
            "Value must be text.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )

    normalized = value.strip()
    if not normalized:
        raise error_type(
            "Value must not be empty.",
            component=component,
            operation=operation,
            field=field,
        )

    if max_length is not None:
        if isinstance(max_length, bool) or not isinstance(max_length, int) or max_length <= 0:
            raise AppValidationError(
                "max_length must be a positive integer when supplied.",
                component="app_helpers",
                operation="require_text",
                field="max_length",
            )
        if len(normalized) > max_length:
            raise error_type(
                "Value exceeds the allowed length.",
                component=component,
                operation=operation,
                field=field,
                context={"max_length": max_length},
            )

    return normalized


def optional_app_text(
    value: Any,
    *,
    field: str,
    error_type: type[E] = AppValidationError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "optional_text",
    max_length: int | None = None,
) -> str | None:
    """Normalize optional text while preserving ``None``."""
    if value is None:
        return None
    return require_app_text(
        value,
        field=field,
        error_type=error_type,
        component=component,
        operation=operation,
        max_length=max_length,
    )


def require_non_negative_int(
    value: Any,
    *,
    field: str,
    error_type: type[E] = AppValidationError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "require_non_negative_int",
) -> int:
    """Require an integer >= 0 while rejecting booleans explicitly."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise error_type(
            "Value must be a non-negative integer.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    if value < 0:
        raise error_type(
            "Value must not be negative.",
            component=component,
            operation=operation,
            field=field,
            context={"received": value},
        )
    return value


def require_non_negative_timedelta(
    value: Any,
    *,
    field: str,
    error_type: type[E] = AppValidationError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "require_non_negative_timedelta",
) -> timedelta:
    """Require a non-negative ``timedelta`` for deadlines/retention arithmetic."""
    if not isinstance(value, timedelta):
        raise error_type(
            "Value must be a timedelta.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    if value < timedelta(0):
        raise error_type(
            "Duration must not be negative.",
            component=component,
            operation=operation,
            field=field,
        )
    return value


def ensure_app_utc_datetime(
    value: datetime | str,
    *,
    field: str,
    error_type: type[E] = AppValidationError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "normalize_datetime",
) -> datetime:
    """Normalize an aware datetime/ISO-8601 value to UTC via domain policy."""
    try:
        return ensure_utc_datetime(value, field=field)
    except DomainError as exc:
        raise error_type(
            "Timestamp must be a valid timezone-aware datetime.",
            component=component,
            operation=operation,
            field=field,
            context=lower_error_context(exc),
            cause=exc,
        ) from exc


def format_app_utc_datetime(
    value: datetime,
    *,
    field: str = "datetime",
    error_type: type[E] = AppValidationError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "format_datetime",
) -> str:
    """Format an aware datetime as canonical ISO-8601 UTC ending in ``Z``."""
    normalized = ensure_app_utc_datetime(
        value,
        field=field,
        error_type=error_type,
        component=component,
        operation=operation,
    )
    return format_utc_datetime(normalized)


def require_binary_stream(
    value: Any,
    *,
    field: str = "stream",
    error_type: type[E] = UnsupportedAppInputError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "require_binary_stream",
) -> BinaryIO:
    """Require a readable, open stream-like object without consuming it.

    The helper intentionally does not call ``read()`` for type probing because
    that could alter stream position or trigger I/O before the concrete adapter
    owns the operation.  Text/scalar payloads are rejected explicitly; adapters
    remain responsible for actual read failures from otherwise valid streams.
    """
    if isinstance(value, (str, bytes, bytearray, memoryview, TextIOBase)):
        raise error_type(
            "Expected an open binary stream, not a scalar text/bytes value.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )

    read = getattr(value, "read", None)
    if not callable(read):
        raise error_type(
            "Value must provide a callable read() method.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )

    if bool(getattr(value, "closed", False)):
        raise error_type(
            "Binary stream must be open.",
            component=component,
            operation=operation,
            field=field,
        )

    return cast(BinaryIO, value)


def require_bytes_like(
    value: Any,
    *,
    field: str = "content",
    error_type: type[E] = UnsupportedAppInputError,  # type: ignore[assignment]
    component: str = "app_helpers",
    operation: str = "require_bytes_like",
) -> bytes | bytearray | memoryview:
    """Require an in-memory binary payload without silently encoding text."""
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise error_type(
            "Value must be bytes, bytearray, or memoryview.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


def canonical_app_json(value: Any, *, pretty: bool = False) -> str:
    """Serialize application metadata using BIMAP's canonical contract JSON rules."""
    try:
        return canonical_json_dumps(value, pretty=pretty)
    except ContractError as exc:
        raise AppSerializationError(
            "Application value cannot be encoded as canonical JSON.",
            component="app_helpers",
            operation="canonical_json",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc


def to_app_primitive(value: Any, *, field: str) -> Any:
    """Convert a supported value into deterministic JSON primitives."""
    if isinstance(value, Enum):
        value = value.value
    try:
        return to_json_primitive(value, field=field)
    except ContractError as exc:
        raise AppSerializationError(
            "Application value cannot be represented deterministically.",
            component="app_helpers",
            operation="to_primitive",
            field=field,
            context={
                "received_type": type(value).__name__,
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc


__all__ = [
    "announce_app_action",
    "lower_error_context",
    "require_app_text",
    "optional_app_text",
    "require_non_negative_int",
    "require_non_negative_timedelta",
    "ensure_app_utc_datetime",
    "format_app_utc_datetime",
    "require_binary_stream",
    "require_bytes_like",
    "canonical_app_json",
    "to_app_primitive",
]


if __name__ == "__main__":
    from datetime import timezone
    from io import BytesIO

    print("\n=== Running Application Helpers Self-Test ===\n")
    printer.status("TEST", "Application helpers module initialized", "info")

    timestamp = ensure_app_utc_datetime(
        "2026-09-02T20:00:00Z",
        field="timestamp",
    )
    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == timedelta(0)
    assert format_app_utc_datetime(timestamp).endswith("Z")
    assert require_binary_stream(BytesIO(b"test")).read(0) == b""
    assert require_non_negative_int(0, field="count") == 0
    assert canonical_app_json({"ok": True}) == '{"ok":true}'
    assert ensure_app_utc_datetime(
        datetime(2026, 9, 2, 20, tzinfo=timezone.utc),
        field="timestamp",
    ) == timestamp
    printer.status("PASS", "Application helper normalization", "success")

    print("\n=== Test ran successfully ===\n")
