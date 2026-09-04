"""
Private shared mechanics for BIMAP FastAPI route modules.

This module exists to keep route files thin and non-redundant.  It owns only
HTTP-presentation mechanics that are specific to route handlers: strict JSON
object decoding, closed request-object validation, idempotency-header handling,
trusted authorization-hook invocation, deterministic JSON responses, and
projection of canonical ``Order`` values through the existing versioned
``OrderContract``.

It does not implement authentication/authorization policy, product policy,
upload validation policy, payment verification, persistence, or application
business rules.  Those responsibilities remain with injected dependencies and
lower BIMAP layers.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeAlias

from fastapi import Request, Response

from ..utils.api_errors import (
    APIConfigurationError,
    APIError,
    APIInternalError,
    APIUnsupportedMediaTypeError,
    APIValidationError,
)
from ..utils.api_helpers import (
    announce_api_action,
    json_bytes,
    lower_error_context,
    require_api_text,
    require_header_name,
    single_header,
)
from ...contracts.order import OrderContract
from ...contracts.utils.contracts_errors import ContractError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Routes Shared")
printer = PrettyPrinter()

_COMPONENT = "api_routes_shared"

RouteAuthorizer: TypeAlias = Callable[
    [Request, str, str | None],
    str | None | Awaitable[str | None],
]


def require_route_authorizer(value: Any, *, field: str = "authorizer") -> RouteAuthorizer:
    """Require a callable authorization hook without defining its policy."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating route authorizer",
        event="api_routes_authorizer_validate_start",
    )
    if not callable(value):
        raise APIConfigurationError(
            "Protected BIMAP routes require a callable authorization hook.",
            component=_COMPONENT,
            operation="require_route_authorizer",
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


async def authorize_request(
    authorizer: RouteAuthorizer,
    request: Request,
    *,
    operation: str,
    resource_id: str | None = None,
) -> str | None:
    """Invoke one injected authorization hook and return its trusted actor label.

    The hook owns identity and authorization semantics.  It should raise
    ``APIUnauthorizedError`` or ``APIForbiddenError`` when access is not allowed.
    Returning a string optionally supplies a trusted actor identifier for
    append-only order lifecycle events.  Returning ``None`` is permitted when a
    deployment intentionally does not persist actor identity.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Authorizing API route request",
        event="api_routes_authorize_start",
        context={"operation": operation, "resource_id": resource_id},
    )
    try:
        result = authorizer(request, operation, resource_id)
        if inspect.isawaitable(result):
            result = await result
    except APIError:
        raise
    except Exception as exc:
        raise APIInternalError(
            "API authorization hook failed outside the BIMAP API error contract.",
            component=_COMPONENT,
            operation="authorize_request",
            context={"route_operation": operation, **lower_error_context(exc)},
            cause=exc,
        ) from exc

    if result is None:
        return None
    return require_api_text(
        result,
        field="actor",
        error_type=APIConfigurationError,
        component=_COMPONENT,
        operation="authorize_request",
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON numeric constant: {value}")


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _require_json_media_type(request: Request) -> None:
    """Require an unambiguous JSON media type for a non-empty JSON request."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating JSON request media type",
        event="api_routes_media_type_validate_start",
    )
    content_type = single_header(request.scope, "content-type")
    if content_type is None:
        raise APIUnsupportedMediaTypeError(
            "JSON request body requires a Content-Type header.",
            component=_COMPONENT,
            operation="require_json_media_type",
            field="content-type",
        )

    media_type = content_type.partition(";")[0].strip().casefold()
    if media_type != "application/json" and not (
        media_type.startswith("application/") and media_type.endswith("+json")
    ):
        raise APIUnsupportedMediaTypeError(
            "Request body must use application/json or an application/*+json media type.",
            component=_COMPONENT,
            operation="require_json_media_type",
            field="content-type",
            context={"media_type": media_type},
        )


async def read_json_object(
    request: Request,
    *,
    required: bool = True,
    field: str = "body",
) -> dict[str, Any]:
    """Read one strict UTF-8 JSON object from a request.

    Duplicate object keys and NaN/Infinity-style constants are rejected rather
    than silently normalized.  Request-size enforcement remains the
    responsibility of ``RequestLimits`` middleware; this helper intentionally
    does not invent a second byte limit.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Reading JSON route request body",
        event="api_routes_json_body_read_start",
    )
    try:
        body = await request.body()
    except Exception as exc:
        raise APIValidationError(
            "HTTP request body could not be read.",
            component=_COMPONENT,
            operation="read_json_object",
            field=field,
            context=lower_error_context(exc),
            cause=exc,
        ) from exc

    if not body:
        if required:
            raise APIValidationError(
                "A JSON request body is required.",
                component=_COMPONENT,
                operation="read_json_object",
                field=field,
            )
        return {}

    _require_json_media_type(request)
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise APIValidationError(
            "JSON request body must be valid UTF-8.",
            component=_COMPONENT,
            operation="read_json_object",
            field=field,
            cause=exc,
        ) from exc

    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise APIValidationError(
            "Request body is not valid strict JSON.",
            component=_COMPONENT,
            operation="read_json_object",
            field=field,
            context=lower_error_context(exc),
            cause=exc,
        ) from exc

    if not isinstance(decoded, dict):
        raise APIValidationError(
            "JSON request body root must be an object.",
            component=_COMPONENT,
            operation="read_json_object",
            field=field,
            context={"received_type": type(decoded).__name__},
        )
    return decoded


def validate_object_fields(
    payload: Mapping[str, Any],
    *,
    required: Sequence[str] = (),
    optional: Sequence[str] = (),
    field: str = "body",
) -> dict[str, Any]:
    """Validate a closed API request object and return a shallow copy."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating API request object fields",
        event="api_routes_fields_validate_start",
    )
    if not isinstance(payload, Mapping):
        raise APIValidationError(
            "Request payload must be an object.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field=field,
            context={"received_type": type(payload).__name__},
        )

    required_names = tuple(str(name) for name in required)
    optional_names = tuple(str(name) for name in optional)
    allowed = set(required_names) | set(optional_names)
    keys = {str(key) for key in payload.keys()}
    missing = tuple(name for name in required_names if name not in keys)
    unexpected = tuple(sorted(keys - allowed))

    if missing:
        raise APIValidationError(
            "Request object is missing required fields.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field=field,
            context={"missing_fields": missing},
        )
    if unexpected:
        raise APIValidationError(
            "Request object contains unsupported fields.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field=field,
            context={"unexpected_fields": unexpected},
        )
    return dict(payload)


def optional_route_text(
    value: Any,
    *,
    field: str,
) -> str | None:
    """Normalize optional route text while preserving ``None``."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing optional route text",
        event="api_routes_optional_text_start",
        context={"field": field},
    )
    if value is None:
        return None
    return require_api_text(
        value,
        field=field,
        error_type=APIValidationError,
        component=_COMPONENT,
        operation="optional_route_text",
    )


def require_idempotency_key(
    request: Request,
    *,
    header_name: str = "Idempotency-Key",
) -> str:
    """Read one required idempotency key using the application's 512-char bound."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Reading idempotency key",
        event="api_routes_idempotency_read_start",
    )
    name = require_header_name(header_name, field="idempotency_header")
    value = single_header(request.scope, name, required=True)
    assert value is not None
    return require_api_text(
        value,
        field=name,
        error_type=APIValidationError,
        component=_COMPONENT,
        operation="require_idempotency_key",
        max_length=512,
    )


def order_to_public_dict(order: Any) -> dict[str, Any]:
    """Project a canonical Order through the existing external OrderContract."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Projecting order for API response",
        event="api_routes_order_project_start",
        context={"order_id": getattr(order, "order_id", None)},
    )
    try:
        return OrderContract.from_domain(order).to_dict()
    except ContractError as exc:
        raise APIInternalError(
            "Authoritative order could not be projected to the external API contract.",
            component=_COMPONENT,
            operation="order_to_public_dict",
            field="order",
            context={
                "order_id": getattr(order, "order_id", None),
                "received_type": type(order).__name__,
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc


def json_response(
    payload: Any,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Build a deterministic compact JSON FastAPI/Starlette response."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Building route JSON response",
        event="api_routes_json_response_start",
        context={"status_code": status_code},
    )
    response = Response(
        content=json_bytes(payload),
        status_code=status_code,
        media_type="application/json",
    )
    if headers:
        for name, value in headers.items():
            response.headers[name] = value
    return response


__all__ = [
    "RouteAuthorizer",
    "require_route_authorizer",
    "authorize_request",
    "read_json_object",
    "validate_object_fields",
    "optional_route_text",
    "require_idempotency_key",
    "order_to_public_dict",
    "json_response",
]
