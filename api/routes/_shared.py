"""
Shared FastAPI route-boundary helpers for BIMAP.

This module is deliberately narrower than ``api.utils.api_helpers``:

* ``api.utils.api_helpers`` owns framework-neutral HTTP/ASGI mechanics used by
  middleware and other API infrastructure;
* ``api.routes._shared`` owns FastAPI-specific request parsing, authorization
  hooks, route response construction, and small route projection helpers.

Keeping these concerns separate prevents route modules from duplicating parsing,
authorization, idempotency, and response code while avoiding a dependency from
the generic API helpers back into FastAPI.

Dependency direction
--------------------
api.routes.*
    -> api.routes._shared
    -> api.utils.*
    -> contracts/domain

The module does not instantiate repositories, storage clients, payment clients,
SLAI agents, or application services.  Runtime dependencies are supplied by the
composition root and injected into route groups.
"""

from __future__ import annotations

import inspect
import json

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any, TypeAlias

from fastapi import Request, Response, status

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...contracts.order import OrderContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.orders.models import Order
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Shared")
printer = PrettyPrinter()

_COMPONENT = "api_routes_shared"
_JSON_MEDIA_TYPES = frozenset({"application/json"})
_IDEMPOTENCY_HEADER = "idempotency-key"


RouteAuthorizer: TypeAlias = Callable[
    [Request, str, str | None],
    str | None | Awaitable[str | None],
]

OrderReportIdResolver: TypeAlias = Callable[
    [Request, str],
    Iterable[str] | Awaitable[Iterable[str]],
]


def require_route_authorizer(authorizer: RouteAuthorizer) -> RouteAuthorizer:
    """Validate the required route-authorization hook.

    The hook owns authentication/tenant authorization semantics.  It should
    raise ``APIUnauthorizedError``/``APIForbiddenError`` (or another explicit
    ``APIError``) when access is denied.  Returning ``None`` is permitted for
    operations whose underlying domain model allows an anonymous/system actor.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating route authorizer",
        event="api_routes_shared_authorizer_validate_start",
    )
    if not callable(authorizer):
        raise APIConfigurationError(
            "Route authorizer must be callable.",
            component=_COMPONENT,
            operation="require_route_authorizer",
            field="authorizer",
            context={"received_type": type(authorizer).__name__},
        )
    return authorizer


async def authorize_request(
    authorizer: RouteAuthorizer,
    request: Request,
    *,
    operation: str,
    resource_id: str | None,
) -> str | None:
    """Run the injected authorization hook and normalize its actor result."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Authorizing API route request",
        event="api_routes_shared_authorize_start",
        context={
            "operation": operation,
            "has_resource_id": resource_id is not None,
        },
    )
    hook = require_route_authorizer(authorizer)
    if not isinstance(request, Request):
        raise APIConfigurationError(
            "authorize_request() requires a FastAPI Request.",
            component=_COMPONENT,
            operation="authorize_request",
            field="request",
            context={"received_type": type(request).__name__},
        )

    normalized_operation = require_api_text(
        operation,
        field="operation",
        error_type=APIConfigurationError,
        component=_COMPONENT,
        operation="authorize_request",
        max_length=128,
    )
    normalized_resource = (
        None
        if resource_id is None
        else require_api_text(
            resource_id,
            field="resource_id",
            component=_COMPONENT,
            operation="authorize_request",
            max_length=512,
        )
    )

    try:
        result = hook(request, normalized_operation, normalized_resource)
        if inspect.isawaitable(result):
            result = await result
    except APIError:
        raise
    except Exception as exc:
        raise APIInternalError(
            "Route authorizer failed outside the BIMAP API error contract.",
            component=_COMPONENT,
            operation="authorize_request",
            context={
                "operation_name": normalized_operation,
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc

    if result is None:
        return None
    return require_api_text(
        result,
        field="actor",
        error_type=APIInternalError,
        component=_COMPONENT,
        operation="authorize_request",
        max_length=512,
    )


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON object hook that rejects duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise APIValidationError(
                "JSON object contains a duplicate member name.",
                component=_COMPONENT,
                operation="read_json_object",
                field=key,
            )
        result[key] = value
    return result


def _request_media_type(request: Request) -> str | None:
    """Return normalized request media type without parameters."""
    value = request.headers.get("content-type")
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type or None


def _is_json_media_type(media_type: str | None) -> bool:
    if media_type is None:
        return False
    return media_type in _JSON_MEDIA_TYPES or media_type.endswith("+json")


async def read_json_object(
    request: Request,
    *,
    required: bool = True,
) -> dict[str, Any]:
    """Read one JSON object request body with strict duplicate-key handling."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Reading JSON route request body",
        event="api_routes_shared_json_read_start",
        context={"required": required},
    )
    if not isinstance(required, bool):
        raise APIConfigurationError(
            "required must be boolean.",
            component=_COMPONENT,
            operation="read_json_object",
            field="required",
        )
    if not isinstance(request, Request):
        raise APIConfigurationError(
            "read_json_object() requires a FastAPI Request.",
            component=_COMPONENT,
            operation="read_json_object",
            field="request",
            context={"received_type": type(request).__name__},
        )

    try:
        body = await request.body()
    except Exception as exc:
        raise APIValidationError(
            "Request body could not be read.",
            component=_COMPONENT,
            operation="read_json_object",
            field="body",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc

    if not body or not body.strip():
        if required:
            raise APIValidationError(
                "A JSON request body is required.",
                component=_COMPONENT,
                operation="read_json_object",
                field="body",
            )
        return {}

    media_type = _request_media_type(request)
    if not _is_json_media_type(media_type):
        raise APIUnsupportedMediaTypeError(
            "Route requires an application/json compatible request body.",
            component=_COMPONENT,
            operation="read_json_object",
            field="content-type",
            context={"media_type": media_type},
        )

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise APIValidationError(
            "JSON request body must be UTF-8 encoded.",
            component=_COMPONENT,
            operation="read_json_object",
            field="body",
            cause=exc,
        ) from exc

    try:
        payload = json.loads(text, object_pairs_hook=_json_object_no_duplicates)
    except APIError:
        raise
    except json.JSONDecodeError as exc:
        raise APIValidationError(
            "Request body is not valid JSON.",
            component=_COMPONENT,
            operation="read_json_object",
            field="body",
            context={
                "line": exc.lineno,
                "column": exc.colno,
            },
            cause=exc,
        ) from exc

    if not isinstance(payload, dict):
        raise APIValidationError(
            "Request JSON must be an object.",
            component=_COMPONENT,
            operation="read_json_object",
            field="body",
            context={"received_type": type(payload).__name__},
        )
    return payload


def validate_object_fields(
    payload: Mapping[str, Any],
    *,
    required: Iterable[str] = (),
    optional: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate an exact JSON-object field set.

    Unknown members are rejected rather than ignored.  This avoids silent API
    drift and protects callers from believing an unsupported option was applied.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating JSON route object fields",
        event="api_routes_shared_fields_validate_start",
    )
    if not isinstance(payload, Mapping):
        raise APIValidationError(
            "Request payload must be an object.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field="payload",
            context={"received_type": type(payload).__name__},
        )

    required_fields = normalize_route_texts(
        required,
        field="required_fields",
        allow_empty=True,
        error_type=APIConfigurationError,
    )
    optional_fields = normalize_route_texts(
        optional,
        field="optional_fields",
        allow_empty=True,
        error_type=APIConfigurationError,
    )

    overlap = set(required_fields).intersection(optional_fields)
    if overlap:
        raise APIConfigurationError(
            "A route field cannot be both required and optional.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field="field_definition",
            context={"overlap": tuple(sorted(overlap))},
        )

    keys: list[str] = []
    for raw_key in payload:
        if not isinstance(raw_key, str):
            raise APIValidationError(
                "JSON object member names must be strings.",
                component=_COMPONENT,
                operation="validate_object_fields",
                field="payload",
                context={"received_key_type": type(raw_key).__name__},
            )
        keys.append(raw_key)

    key_set = set(keys)
    required_set = set(required_fields)
    allowed_set = required_set.union(optional_fields)
    missing = tuple(field for field in required_fields if field not in key_set)
    unexpected = tuple(sorted(key_set - allowed_set))

    if missing:
        raise APIValidationError(
            "Request is missing one or more required fields.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field="payload",
            context={"missing_fields": missing},
        )
    if unexpected:
        raise APIValidationError(
            "Request contains unsupported fields.",
            component=_COMPONENT,
            operation="validate_object_fields",
            field="payload",
            context={"unexpected_fields": unexpected},
        )
    return dict(payload)


def optional_route_text(
    value: Any,
    *,
    field: str,
    max_length: int | None = None,
) -> str | None:
    """Normalize optional route text using the API error vocabulary."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating optional route field: {field}",
        event="api_routes_shared_optional_text_start",
    )
    if value is None:
        return None
    return require_api_text(
        value,
        field=field,
        component=_COMPONENT,
        operation="optional_route_text",
        max_length=max_length,
    )


def require_idempotency_key(
    request: Request,
    *,
    header_name: str = _IDEMPOTENCY_HEADER,
) -> str:
    """Read one mandatory idempotency key from the request headers."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Reading route idempotency key",
        event="api_routes_shared_idempotency_start",
    )
    name = require_header_name(header_name, field="header_name")
    raw = single_header(request.scope, name, required=True)
    assert raw is not None
    return require_api_text(
        raw,
        field=name,
        error_type=APIValidationError,
        component=_COMPONENT,
        operation="require_idempotency_key",
        max_length=512,
    )


def normalize_route_texts(
    values: Iterable[str],
    *,
    field: str,
    allow_empty: bool,
    error_type: type[APIError] = APIValidationError,
) -> tuple[str, ...]:
    """Materialize, validate, and stable-deduplicate a text iterable."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Normalizing route text collection: {field}",
        event="api_routes_shared_texts_normalize_start",
    )
    if not isinstance(allow_empty, bool):
        raise APIConfigurationError(
            "allow_empty must be boolean.",
            component=_COMPONENT,
            operation="normalize_route_texts",
            field="allow_empty",
        )
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise error_type(
            "Expected an iterable of individual text values.",
            component=_COMPONENT,
            operation="normalize_route_texts",
            field=field,
            context={"received_type": type(values).__name__},
        )
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise error_type(
            "Expected an iterable value.",
            component=_COMPONENT,
            operation="normalize_route_texts",
            field=field,
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(iterator):
        normalized = require_api_text(
            item,
            field=f"{field}[{index}]",
            error_type=error_type,
            component=_COMPONENT,
            operation="normalize_route_texts",
            max_length=1024,
        )
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    if not result and not allow_empty:
        raise error_type(
            "At least one value is required.",
            component=_COMPONENT,
            operation="normalize_route_texts",
            field=field,
        )
    return tuple(result)


def require_order_report_id_resolver(
    resolver: OrderReportIdResolver,
) -> OrderReportIdResolver:
    """Validate the trusted order-to-report-ID resolver hook."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating order report-ID resolver",
        event="api_routes_shared_report_resolver_validate_start",
    )
    if not callable(resolver):
        raise APIConfigurationError(
            "Report-ID resolver must be callable.",
            component=_COMPONENT,
            operation="require_order_report_id_resolver",
            field="resolver",
            context={"received_type": type(resolver).__name__},
        )
    return resolver


async def resolve_order_report_ids(
    resolver: OrderReportIdResolver,
    request: Request,
    order_id: str,
) -> tuple[str, ...]:
    """Resolve explicit report IDs for one already-authorized order.

    The current ``Repository``/``ListReports`` surfaces intentionally expose
    point reads only.  This hook is therefore a composition seam for an
    authorized read model, not an invented ``Repository.list_reports`` method.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Resolving report identifiers for order",
        event="api_routes_shared_report_ids_resolve_start",
        context={"order_id": order_id},
    )
    hook = require_order_report_id_resolver(resolver)
    target = require_api_text(
        order_id,
        field="order_id",
        component=_COMPONENT,
        operation="resolve_order_report_ids",
    )

    try:
        result = hook(request, target)
        if inspect.isawaitable(result):
            result = await result
    except APIError:
        raise
    except Exception as exc:
        raise APIInternalError(
            "Report-ID resolver failed outside the BIMAP API error contract.",
            component=_COMPONENT,
            operation="resolve_order_report_ids",
            context={
                "order_id": target,
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc

    return normalize_route_texts(
        result,
        field="report_ids",
        allow_empty=True,
        error_type=APIInternalError,
    )


def order_to_public_dict(order: Order) -> dict[str, Any]:
    """Project a canonical order through the stable external OrderContract."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Projecting order for API response",
        event="api_routes_shared_order_project_start",
        context={"order_id": getattr(order, "order_id", None)},
    )
    if not isinstance(order, Order):
        raise APIInternalError(
            "Order projection received an unsupported object type.",
            component=_COMPONENT,
            operation="order_to_public_dict",
            field="order",
            context={"received_type": type(order).__name__},
        )
    try:
        return OrderContract.from_domain(order).to_dict()
    except ContractError as exc:
        raise APIInternalError(
            "Canonical order could not be projected through the order contract.",
            component=_COMPONENT,
            operation="order_to_public_dict",
            context={
                "order_id": getattr(order, "order_id", None),
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc


def json_response(
    payload: Any,
    *,
    status_code: int = status.HTTP_200_OK,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """Build one deterministic JSON FastAPI response."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Building JSON route response",
        event="api_routes_shared_json_response_start",
        context={"status_code": status_code},
    )
    normalized_status = require_positive_int(
        status_code,
        field="status_code",
        error_type=APIConfigurationError,
        component=_COMPONENT,
        operation="json_response",
    )
    if normalized_status < 100 or normalized_status > 599:
        raise APIConfigurationError(
            "HTTP status code must be between 100 and 599.",
            component=_COMPONENT,
            operation="json_response",
            field="status_code",
            context={"status_code": normalized_status},
        )

    response_headers: dict[str, str] = {}
    if headers is not None:
        if not isinstance(headers, Mapping):
            raise APIConfigurationError(
                "Response headers must be a mapping.",
                component=_COMPONENT,
                operation="json_response",
                field="headers",
                context={"received_type": type(headers).__name__},
            )
        for raw_name, raw_value in headers.items():
            name = require_header_name(raw_name)
            value = require_header_value(raw_value)
            response_headers[name] = value

    body = json_bytes(payload)
    return Response(
        content=body,
        status_code=normalized_status,
        headers=response_headers,
        media_type="application/json",
    )


__all__ = [
    "RouteAuthorizer",
    "OrderReportIdResolver",
    "require_route_authorizer",
    "authorize_request",
    "read_json_object",
    "validate_object_fields",
    "optional_route_text",
    "require_idempotency_key",
    "normalize_route_texts",
    "require_order_report_id_resolver",
    "resolve_order_report_ids",
    "order_to_public_dict",
    "json_response",
]