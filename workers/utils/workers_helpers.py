"""
Shared execution helpers for the complete BIMAP ``workers/`` package.

The helpers centralize worker-boundary mechanics that would otherwise be
repeated by ``workers/runner.py``, ``workers/engine.py``, ``workers/reports.py``,
and individual jobs: method-start diagnostics, invocation normalization, lower
error identity extraction, dependency-error translation, result validation, and
retryability inspection.

They deliberately do not implement business state transitions, audit sequencing,
governance policy, report construction, retention periods, deletion/legal-hold
policy, storage naming, retry/backoff loops, or queue acknowledgement semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar, cast

from ...app.utils.app_errors import AppError, sanitize_app_context
from ...app.utils.app_helpers import optional_app_text, require_app_text
from .workers_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Worker Helpers")
printer = PrettyPrinter()

T = TypeVar("T")
E = TypeVar("E", bound=WorkerError)


def announce_worker_action(
    target_printer: PrettyPrinter,
    target_logger: Any,
    *,
    component: str,
    action: str,
    event: str,
    context: Mapping[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Emit one consistent, content-safe worker method-start diagnostic."""
    safe_context = sanitize_app_context(context)
    target_printer.status("WORKER", action, level)
    payload: dict[str, Any] = {
        "event": event,
        "component": component,
        "action": action,
    }
    if safe_context:
        payload["context"] = safe_context
    target_logger.debug(payload)


def lower_worker_error_context(error: BaseException) -> dict[str, Any]:
    """Return lower-layer identity only; never copy exception/provider messages."""
    context: dict[str, Any] = {"lower_error_type": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.strip():
        context["lower_error_code"] = code.strip()
    retryable = getattr(error, "retryable", None)
    if isinstance(retryable, bool):
        context["lower_retryable"] = retryable
    return context


def require_worker_text(
    value: Any,
    *,
    field: str,
    component: str,
    operation: str,
    max_length: int | None = None,
    error_type: type[E] = WorkerValidationError,  # type: ignore[assignment]
) -> str:
    """Normalize required text through the existing application text policy."""
    try:
        return require_app_text(value, field=field, max_length=max_length)
    except AppError as exc:
        raise error_type(
            "Worker invocation contains invalid required text.",
            component=component,
            operation=operation,
            field=field,
            context=lower_worker_error_context(exc),
            cause=exc,
        ) from exc


def optional_worker_text(
    value: Any,
    *,
    field: str,
    component: str,
    operation: str,
    max_length: int | None = None,
    error_type: type[E] = WorkerValidationError,  # type: ignore[assignment]
) -> str | None:
    """Normalize optional text through the existing application text policy."""
    try:
        return optional_app_text(value, field=field, max_length=max_length)
    except AppError as exc:
        raise error_type(
            "Worker invocation contains invalid optional text.",
            component=component,
            operation=operation,
            field=field,
            context=lower_worker_error_context(exc),
            cause=exc,
        ) from exc


def materialize_worker_iterable(
    value: Iterable[T],
    *,
    field: str,
    component: str,
    operation: str,
    accepted_type: type[Any] | tuple[type[Any], ...] | None = None,
    allow_empty: bool = True,
) -> tuple[T, ...]:
    """
    Materialize one adapter collection exactly once without altering its order.

    Scalar text/bytes and mappings are rejected because treating them as generic
    iterables is almost always a worker invocation bug. No sorting or
    deduplication occurs; domain/application services retain semantic ownership.
    """
    announce_worker_action(
        printer,
        logger,
        component=component,
        action=f"Materializing worker collection: {field}",
        event="worker_iterable_materialize_start",
        context={"field": field},
    )
    if isinstance(value, (str, bytes, bytearray, memoryview, Mapping)):
        raise WorkerValidationError(
            "Worker collection input must be an iterable of individual values.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    try:
        items = tuple(value)
    except TypeError as exc:
        raise WorkerValidationError(
            "Worker collection input must be iterable.",
            component=component,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
            cause=exc,
        ) from exc

    if not allow_empty and not items:
        raise WorkerValidationError(
            "Worker collection input must not be empty.",
            component=component,
            operation=operation,
            field=field,
        )
    if accepted_type is not None:
        for index, item in enumerate(items):
            if not isinstance(item, accepted_type):
                raise WorkerValidationError(
                    "Worker collection contains an unsupported value type.",
                    component=component,
                    operation=operation,
                    field=f"{field}[{index}]",
                    context={"received_type": type(item).__name__},
                )
    return items


def run_worker_dependency(
    callback: Callable[[], T],
    *,
    component: str,
    operation: str,
    message: str,
    context: Mapping[str, Any] | None = None,
    error_type: type[E] = WorkerDependencyError,  # type: ignore[assignment]
) -> T:
    """
    Execute one inner call and translate failure without performing a retry.

    ``AppError.retryable`` is preserved. Generic timeouts/connections are marked
    retryable; unexpected exceptions fail closed as non-retryable unless an
    outer runner explicitly decides otherwise.
    """
    safe_context = sanitize_app_context(context)
    try:
        return callback()
    except WorkerError:
        raise
    except AppError as exc:
        raise error_type(
            message,
            component=component,
            operation=operation,
            retryable=bool(getattr(exc, "retryable", False)),
            context={**safe_context, **lower_worker_error_context(exc)},
            cause=exc,
        ) from exc
    except TimeoutError as exc:
        if error_type is WorkerDependencyError:
            raise WorkerDependencyTimeoutError(
                message,
                component=component,
                operation=operation,
                context={**safe_context, **lower_worker_error_context(exc)},
                cause=exc,
            ) from exc
        raise error_type(
            message,
            component=component,
            operation=operation,
            retryable=True,
            context={**safe_context, **lower_worker_error_context(exc)},
            cause=exc,
        ) from exc
    except ConnectionError as exc:
        if error_type is WorkerDependencyError:
            raise WorkerDependencyUnavailableError(
                message,
                component=component,
                operation=operation,
                context={**safe_context, **lower_worker_error_context(exc)},
                cause=exc,
            ) from exc
        raise error_type(
            message,
            component=component,
            operation=operation,
            retryable=True,
            context={**safe_context, **lower_worker_error_context(exc)},
            cause=exc,
        ) from exc
    except Exception as exc:
        raise error_type(
            message,
            component=component,
            operation=operation,
            retryable=False,
            context={**safe_context, **lower_worker_error_context(exc)},
            cause=exc,
        ) from exc


def require_worker_result(
    value: Any,
    expected_type: type[T],
    *,
    component: str,
    operation: str,
    field: str = "result",
    message: str = "Worker dependency returned an unsupported result type.",
) -> T:
    """Fail closed when an inner use case violates its declared result type."""
    announce_worker_action(
        printer,
        logger,
        component=component,
        action="Validating worker dependency result",
        event="worker_result_validate_start",
        context={"expected_type": expected_type.__name__},
    )
    if not isinstance(value, expected_type):
        raise WorkerIntegrityError(
            message,
            component=component,
            operation=operation,
            field=field,
            context={
                "expected_type": expected_type.__name__,
                "received_type": type(value).__name__,
            },
        )
    return cast(T, value)


def worker_failure_retryable(error: BaseException) -> bool:
    """Inspect retryability metadata without defining retry policy."""
    return bool(getattr(error, "retryable", False))


def worker_failure_code(error: BaseException) -> str | None:
    """Return a stable machine-readable failure code when available."""
    code = getattr(error, "code", None)
    return code.strip() if isinstance(code, str) and code.strip() else None


__all__ = [
    "announce_worker_action",
    "lower_worker_error_context",
    "require_worker_text",
    "optional_worker_text",
    "materialize_worker_iterable",
    "run_worker_dependency",
    "require_worker_result",
    "worker_failure_retryable",
    "worker_failure_code",
]


if __name__ == "__main__":
    print("\n=== Running Worker Helpers Self-Test ===\n")
    printer.status("TEST", "Worker helpers module initialized", "info")
    assert require_worker_text(
        "  JOB-001  ",
        field="job_id",
        component="self_test",
        operation="normalize",
    ) == "JOB-001"
    assert materialize_worker_iterable(
        (1, 2, 3),
        field="items",
        component="self_test",
        operation="materialize",
        accepted_type=int,
    ) == (1, 2, 3)
    try:
        run_worker_dependency(
            lambda: (_ for _ in ()).throw(TimeoutError("timeout")),
            component="self_test",
            operation="dependency",
            message="Dependency timed out.",
        )
    except WorkerDependencyTimeoutError as exc:
        assert exc.retryable is True
    else:
        raise AssertionError("Timeout translation did not execute.")
    printer.status("PASS", "Worker helper normalization/translation", "success")
    print("\n=== Test ran successfully ===\n")