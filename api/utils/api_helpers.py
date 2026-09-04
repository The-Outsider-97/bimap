"""
Shared HTTP/ASGI helpers for BIMAP's API layer.

The repository currently defines ``api/`` as the outer Level-6 HTTP boundary,
but it does not commit to FastAPI, Starlette, Django, Flask, or another concrete
web framework.  These helpers therefore use the minimal ASGI calling convention
instead of introducing an unverified framework dependency.

The module owns only cross-cutting HTTP mechanics needed by BIMAP middleware:
structured method-start diagnostics, ASGI header access/mutation, request state,
content-length validation, safe JSON/problem responses, and small configuration
validators.  It deliberately does not implement authentication, authorization,
CORS policy, persistence, payment logic, rate-limit storage, or application
business rules.

Dependency direction
--------------------
``api_helpers.py`` may import ``api_errors.py``.  ``api_errors.py`` must never
import this module.  API middleware may depend on both.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any, Awaitable, Callable, TypeVar, cast

from .api_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Helpers")
printer = PrettyPrinter()

ASGIScope = MutableMapping[str, Any]
ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]

E = TypeVar("E", bound=APIError)

_HEADER_TOKEN_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BIMAP_STATE_KEY = "bimap"


def announce_api_action(
    target_printer: PrettyPrinter,
    target_logger: Any,
    *,
    component: str,
    action: str,
    event: str,
    context: Mapping[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Emit one consistent, content-safe API method-start diagnostic."""
    safe_context = sanitize_api_context(context)
    target_printer.status("API", action, level)
    payload: dict[str, Any] = {
        "event": event,
        "component": component,
        "action": action,
    }
    if safe_context:
        payload["context"] = safe_context
    target_logger.debug(payload)


def lower_error_context(error: BaseException) -> dict[str, str]:
    """Return only safe lower-layer error identity for API translation."""
    context = {"lower_error_type": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.strip():
        context["lower_error_code"] = code.strip()
    return context


def require_api_text(
    value: Any,
    *,
    field: str,
    error_type: type[E] = APIValidationError,  # type: ignore[assignment]
    component: str = "api_helpers",
    operation: str = "require_text",
    max_length: int | None = None,
) -> str:
    """Validate required API-layer text without changing its semantics."""
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
            raise APIConfigurationError(
                "max_length must be a positive integer when supplied.",
                component="api_helpers",
                operation="require_text",
                field="max_length",
            )
        if len(normalized) > max_length:
            raise error_type(
                "Value exceeds the configured length bound.",
                component=component,
                operation=operation,
                field=field,
                context={"max_length": max_length},
            )
    return normalized


def require_non_negative_int(
    value: Any,
    *,
    field: str,
    error_type: type[E] = APIValidationError,  # type: ignore[assignment]
    component: str = "api_helpers",
    operation: str = "require_non_negative_int",
) -> int:
    """Require an integer >= 0 while explicitly rejecting booleans."""
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


def require_positive_int(
    value: Any,
    *,
    field: str,
    error_type: type[E] = APIValidationError,  # type: ignore[assignment]
    component: str = "api_helpers",
    operation: str = "require_positive_int",
) -> int:
    """Require an integer > 0 while explicitly rejecting booleans."""
    normalized = require_non_negative_int(
        value,
        field=field,
        error_type=error_type,
        component=component,
        operation=operation,
    )
    if normalized == 0:
        raise error_type(
            "Value must be greater than zero.",
            component=component,
            operation=operation,
            field=field,
        )
    return normalized


def require_header_name(name: Any, *, field: str = "header_name") -> str:
    """Validate an HTTP field-name token and return lowercase text."""
    normalized = require_api_text(
        name,
        field=field,
        error_type=APIConfigurationError,
        component="api_helpers",
        operation="require_header_name",
        max_length=128,
    )
    if not _HEADER_TOKEN_RE.fullmatch(normalized):
        raise APIConfigurationError(
            "HTTP header name contains unsupported characters.",
            component="api_helpers",
            operation="require_header_name",
            field=field,
        )
    try:
        normalized.encode("ascii")
    except UnicodeEncodeError as exc:
        raise APIConfigurationError(
            "HTTP header name must be ASCII.",
            component="api_helpers",
            operation="require_header_name",
            field=field,
            cause=exc,
        ) from exc
    return normalized.lower()


def require_header_value(
    value: Any,
    *,
    field: str = "header_value",
    max_length: int = 4096,
) -> str:
    """Validate a response header value against injection/control characters."""
    normalized = require_api_text(
        value,
        field=field,
        error_type=APIConfigurationError,
        component="api_helpers",
        operation="require_header_value",
        max_length=max_length,
    )
    if "\r" in normalized or "\n" in normalized or "\x00" in normalized:
        raise APIConfigurationError(
            "HTTP header value contains an unsafe control character.",
            component="api_helpers",
            operation="require_header_value",
            field=field,
        )
    try:
        normalized.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise APIConfigurationError(
            "HTTP header value must be Latin-1 encodable for ASGI transport.",
            component="api_helpers",
            operation="require_header_value",
            field=field,
            cause=exc,
        ) from exc
    return normalized


def _raw_headers(scope: Mapping[str, Any]) -> tuple[tuple[bytes, bytes], ...]:
    """Return validated raw ASGI request headers without decoding content."""
    raw = scope.get("headers", ())
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes, bytearray, Mapping)):
        raise APIProtocolError(
            "ASGI request headers have an invalid container type.",
            component="api_helpers",
            operation="raw_headers",
            field="scope.headers",
            context={"received_type": type(raw).__name__},
        )
    try:
        items = tuple(raw)
    except TypeError as exc:
        raise APIProtocolError(
            "ASGI request headers are not iterable.",
            component="api_helpers",
            operation="raw_headers",
            field="scope.headers",
            cause=exc,
        ) from exc

    normalized: list[tuple[bytes, bytes]] = []
    for index, item in enumerate(items):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise APIProtocolError(
                "ASGI request header entry must be a two-item pair.",
                component="api_helpers",
                operation="raw_headers",
                field=f"scope.headers[{index}]",
            )
        name, value = item # type: ignore
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            raise APIProtocolError(
                "ASGI request header names and values must be bytes.",
                component="api_helpers",
                operation="raw_headers",
                field=f"scope.headers[{index}]",
                context={
                    "name_type": type(name).__name__,
                    "value_type": type(value).__name__,
                },
            )
        normalized.append((name.lower(), value))
    return tuple(normalized)


def header_values(scope: Mapping[str, Any], name: str) -> tuple[str, ...]:
    """Return all occurrences of one request header using ASGI Latin-1 decoding."""
    target = require_header_name(name).encode("ascii")
    values: list[str] = []
    for raw_name, raw_value in _raw_headers(scope):
        if raw_name == target:
            try:
                value = raw_value.decode("latin-1")
            except UnicodeDecodeError as exc:  # defensive; Latin-1 normally cannot fail
                raise APIInvalidHeaderError(
                    "HTTP header value cannot be decoded.",
                    component="api_helpers",
                    operation="header_values",
                    field=name,
                    cause=exc,
                ) from exc
            if "\r" in value or "\n" in value or "\x00" in value:
                raise APIInvalidHeaderError(
                    "HTTP header contains an unsafe control character.",
                    component="api_helpers",
                    operation="header_values",
                    field=name,
                )
            values.append(value)
    return tuple(values)


def single_header(
    scope: Mapping[str, Any],
    name: str,
    *,
    required: bool = False,
) -> str | None:
    """Return exactly one request-header value, rejecting ambiguity."""
    values = header_values(scope, name)
    if not values:
        if required:
            raise APIInvalidHeaderError(
                "Required HTTP header is missing.",
                component="api_helpers",
                operation="single_header",
                field=name,
            )
        return None
    if len(values) != 1:
        raise APIInvalidHeaderError(
            "HTTP header must occur at most once.",
            component="api_helpers",
            operation="single_header",
            field=name,
            context={"occurrences": len(values)},
        )
    return values[0]


def parse_content_length(scope: Mapping[str, Any]) -> int | None:
    """Parse one unambiguous non-negative Content-Length value."""
    value = single_header(scope, "content-length")
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or not stripped.isascii() or not stripped.isdecimal():
        raise APIInvalidHeaderError(
            "Content-Length must be one non-negative decimal integer.",
            component="api_helpers",
            operation="parse_content_length",
            field="content-length",
        )
    try:
        return int(stripped)
    except ValueError as exc:  # defensive for unusual Python integer constraints
        raise APIInvalidHeaderError(
            "Content-Length could not be parsed.",
            component="api_helpers",
            operation="parse_content_length",
            field="content-length",
            cause=exc,
        ) from exc


def request_header_metrics(scope: Mapping[str, Any]) -> tuple[int, int]:
    """Return ``(count, raw_name_plus_value_bytes)`` for request headers."""
    headers = _raw_headers(scope)
    return len(headers), sum(len(name) + len(value) for name, value in headers)


def bimap_state(scope: ASGIScope) -> MutableMapping[str, Any]:
    """Return BIMAP's namespaced per-request ASGI state mapping."""
    state = scope.get("state")
    if state is None:
        state = {}
        scope["state"] = state
    if not isinstance(state, MutableMapping):
        raise APIProtocolError(
            "ASGI scope state must be a mutable mapping when present.",
            component="api_helpers",
            operation="bimap_state",
            field="scope.state",
            context={"received_type": type(state).__name__},
        )
    nested = state.get(_BIMAP_STATE_KEY)
    if nested is None:
        nested = {}
        state[_BIMAP_STATE_KEY] = nested
    if not isinstance(nested, MutableMapping):
        raise APIProtocolError(
            "BIMAP ASGI request state namespace is not mutable.",
            component="api_helpers",
            operation="bimap_state",
            field=f"scope.state.{_BIMAP_STATE_KEY}",
            context={"received_type": type(nested).__name__},
        )
    return cast(MutableMapping[str, Any], nested)


def _correlation_state_value(scope: Mapping[str, Any], key: str) -> str | None:
    """Read one correlation-state value without requiring a concrete context type."""
    state = scope.get("state")
    if not isinstance(state, Mapping):
        return None
    nested = state.get(_BIMAP_STATE_KEY)
    if not isinstance(nested, Mapping):
        return None
    correlation = nested.get("correlation")
    if isinstance(correlation, Mapping):
        value = correlation.get(key)
    else:
        value = getattr(correlation, key, None)
    return value if isinstance(value, str) and value else None


def get_correlation_id(scope: Mapping[str, Any]) -> str | None:
    """Read the correlation identifier previously installed by middleware."""
    return _correlation_state_value(scope, "correlation_id")


def get_request_id(scope: Mapping[str, Any]) -> str | None:
    """Read the server-owned request identifier installed by middleware."""
    return _correlation_state_value(scope, "request_id")


def peer_client_ip(scope: Mapping[str, Any]) -> str | None:
    """Return the ASGI peer address without trusting forwarded headers."""
    client = scope.get("client")
    if not isinstance(client, (tuple, list)) or not client:
        return None
    host = client[0]
    return str(host) if host is not None else None


def set_response_header(
    message: ASGIMessage,
    name: str,
    value: str,
) -> None:
    """Replace one response header on an ``http.response.start`` message."""
    if message.get("type") != "http.response.start":
        raise APIProtocolError(
            "Response headers can only be changed on http.response.start.",
            component="api_helpers",
            operation="set_response_header",
            field="message.type",
            context={"message_type": message.get("type")},
        )
    normalized_name = require_header_name(name)
    normalized_value = require_header_value(value)
    target = normalized_name.encode("ascii")
    encoded_value = normalized_value.encode("latin-1")

    raw_headers = message.get("headers", [])
    if raw_headers is None:
        raw_headers = []
    if not isinstance(raw_headers, Sequence) or isinstance(
        raw_headers, (str, bytes, bytearray)
    ):
        raise APIProtocolError(
            "ASGI response headers must be a sequence.",
            component="api_helpers",
            operation="set_response_header",
            field="message.headers",
        )
    retained: list[tuple[bytes, bytes]] = []
    for item in raw_headers:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise APIProtocolError(
                "ASGI response header entry is malformed.",
                component="api_helpers",
                operation="set_response_header",
                field="message.headers",
            )
        raw_name, raw_value = item
        if not isinstance(raw_name, bytes) or not isinstance(raw_value, bytes):
            raise APIProtocolError(
                "ASGI response header names and values must be bytes.",
                component="api_helpers",
                operation="set_response_header",
                field="message.headers",
            )
        if raw_name.lower() != target:
            retained.append((raw_name, raw_value))
    retained.append((target, encoded_value))
    message["headers"] = retained


def json_bytes(value: Any) -> bytes:
    """Encode deterministic compact UTF-8 JSON for API-generated responses."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise APIConfigurationError(
            "API-generated response value is not JSON serializable.",
            component="api_helpers",
            operation="json_bytes",
            context={"value_type": type(value).__name__},
            cause=exc,
        ) from exc


async def send_json_response(
    send: ASGISend,
    *,
    status_code: int,
    payload: Any,
    headers: Mapping[str, str] | None = None,
    media_type: str = "application/json",
    suppress_body: bool = False,
) -> None:
    """Send a complete ASGI JSON response without a framework dependency."""
    normalized_status = require_positive_int(
        status_code,
        field="status_code",
        error_type=APIConfigurationError,
        component="api_helpers",
        operation="send_json_response",
    )
    if normalized_status < 100 or normalized_status > 599:
        raise APIConfigurationError(
            "HTTP status code must be between 100 and 599.",
            component="api_helpers",
            operation="send_json_response",
            field="status_code",
            context={"status_code": normalized_status},
        )

    body = b"" if suppress_body else json_bytes(payload)
    response_headers: list[tuple[bytes, bytes]] = [
        (b"content-type", f"{media_type}; charset=utf-8".encode("latin-1")),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if headers:
        for raw_name, raw_value in headers.items():
            name = require_header_name(raw_name)
            value = require_header_value(raw_value)
            name_bytes = name.encode("ascii")
            response_headers = [
                pair for pair in response_headers if pair[0].lower() != name_bytes
            ]
            response_headers.append((name_bytes, value.encode("latin-1")))

    await send(
        {
            "type": "http.response.start",
            "status": normalized_status,
            "headers": response_headers,
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def send_problem_response(
    send: ASGISend,
    *,
    error: APIError,
    correlation_id: str | None = None,
    request_id: str | None = None,
    suppress_body: bool = False,
) -> None:
    """Send one safe API problem response for a mapped ``APIError``."""
    if not isinstance(error, APIError):
        raise APIConfigurationError(
            "send_problem_response() requires an APIError.",
            component="api_helpers",
            operation="send_problem_response",
            field="error",
            context={"received_type": type(error).__name__},
        )
    headers = {
        "Cache-Control": "no-store",
        **dict(error.headers),
    }
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    if request_id:
        headers["X-Request-ID"] = request_id
    await send_json_response(
        send,
        status_code=error.status_code,
        payload=error.to_public_dict(correlation_id=correlation_id),
        headers=headers,
        media_type="application/problem+json",
        suppress_body=suppress_body,
    )


__all__ = [
    "ASGIScope",
    "ASGIMessage",
    "ASGIReceive",
    "ASGISend",
    "ASGIApp",
    "announce_api_action",
    "lower_error_context",
    "require_api_text",
    "require_non_negative_int",
    "require_positive_int",
    "require_header_name",
    "require_header_value",
    "header_values",
    "single_header",
    "parse_content_length",
    "request_header_metrics",
    "bimap_state",
    "get_correlation_id",
    "get_request_id",
    "peer_client_ip",
    "set_response_header",
    "json_bytes",
    "send_json_response",
    "send_problem_response",
]


if __name__ == "__main__":
    import asyncio

    print("\n=== Running API Helpers Self-Test ===\n")
    printer.status("TEST", "API helpers module initialized", "info")

    scope: ASGIScope = {
        "type": "http",
        "headers": [(b"content-length", b"12"), (b"x-test", b"value")],
        "client": ("127.0.0.1", 50000),
    }
    assert parse_content_length(scope) == 12
    assert request_header_metrics(scope) == (2, 27)
    assert peer_client_ip(scope) == "127.0.0.1"
    bimap_state(scope)["test"] = True
    assert scope["state"]["bimap"]["test"] is True

    sent: list[dict[str, Any]] = []

    async def _send(message: ASGIMessage) -> None:
        sent.append(dict(message))

    asyncio.run(
        send_json_response(
            _send,
            status_code=200,
            payload={"ok": True},
            headers={"X-Test": "1"},
        )
    )
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b'{"ok":true}'
    printer.status("PASS", "API helper HTTP mechanics", "success")

    print("\n=== Test ran successfully ===\n")