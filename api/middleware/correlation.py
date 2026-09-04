"""
ASGI correlation/request-ID middleware for BIMAP.

The middleware creates one server-owned request identifier per HTTP request and
propagates one validated correlation identifier across the request/response
boundary.  Correlation metadata is observability metadata only: it does not
represent authentication, authorization, idempotency, order identity, or audit
job identity.

Security/consistency decisions
------------------------------
* ``request_id`` is always generated locally; untrusted clients cannot choose it.
* an inbound correlation ID may be accepted, but it must satisfy a deliberately
  narrow visible-ASCII syntax before it is stored, logged, or reflected;
* duplicate correlation headers are rejected as ambiguous;
* malformed inbound correlation IDs are rejected by default.  Deployments may
  set ``reject_invalid_inbound=False`` to replace malformed values instead;
* the generated identifier factory is injectable so deterministic tests do not
  monkey-patch UUID generation;
* IDs are stored in BIMAP's namespaced ASGI request state and emitted as response
  headers without changing application payloads.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from ..utils.api_errors import *
from ..utils.api_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Correlation Middleware")
printer = PrettyPrinter()

_COMPONENT = "api_correlation"
_DEFAULT_CORRELATION_HEADER = "x-correlation-id"
_DEFAULT_REQUEST_HEADER = "x-request-id"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _default_id_factory() -> str:
    return uuid4().hex


def _normalize_id(
    value: str,
    *,
    field: str,
    max_length: int,
    error_type: type[APICorrelationError] = APICorrelationError,
) -> str:
    """Validate one correlation/request identifier before storage or reflection."""
    normalized = require_api_text(
        value,
        field=field,
        error_type=error_type,
        component=_COMPONENT,
        operation="normalize_id",
        max_length=max_length,
    )
    try:
        normalized.encode("ascii")
    except UnicodeEncodeError as exc:
        raise error_type(
            "Correlation metadata must contain only ASCII characters.",
            component=_COMPONENT,
            operation="normalize_id",
            field=field,
            cause=exc,
        ) from exc
    if not _ID_RE.fullmatch(normalized):
        raise error_type(
            "Correlation metadata contains unsupported characters.",
            component=_COMPONENT,
            operation="normalize_id",
            field=field,
        )
    return normalized


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Immutable request observability identifiers installed by middleware."""

    request_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating correlation context",
            event="api_correlation_context_validate_start",
        )
        object.__setattr__(
            self,
            "request_id",
            _normalize_id(self.request_id, field="request_id", max_length=128),
        )
        object.__setattr__(
            self,
            "correlation_id",
            _normalize_id(self.correlation_id, field="correlation_id", max_length=128),
        )

    def to_dict(self) -> dict[str, str]:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing correlation context",
            event="api_correlation_context_to_dict_start",
        )
        return {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
        }


class CorrelationMiddleware:
    """Assign and propagate validated request/correlation IDs for HTTP traffic."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        correlation_header: str = _DEFAULT_CORRELATION_HEADER,
        request_header: str = _DEFAULT_REQUEST_HEADER,
        max_id_length: int = 128,
        reject_invalid_inbound: bool = True,
        id_factory: Callable[[], str] = _default_id_factory,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing correlation middleware",
            event="api_correlation_init_start",
        )
        if not callable(app):
            raise APIConfigurationError(
                "Correlation middleware requires a callable ASGI application.",
                component=_COMPONENT,
                operation="initialize",
                field="app",
                context={"received_type": type(app).__name__},
            )
        if isinstance(max_id_length, bool) or not isinstance(max_id_length, int) or max_id_length <= 0:
            raise APIConfigurationError(
                "max_id_length must be a positive integer.",
                component=_COMPONENT,
                operation="initialize",
                field="max_id_length",
            )
        if not isinstance(reject_invalid_inbound, bool):
            raise APIConfigurationError(
                "reject_invalid_inbound must be boolean.",
                component=_COMPONENT,
                operation="initialize",
                field="reject_invalid_inbound",
            )
        if not callable(id_factory):
            raise APIConfigurationError(
                "id_factory must be callable.",
                component=_COMPONENT,
                operation="initialize",
                field="id_factory",
            )

        self.app = app
        self.correlation_header = require_header_name(correlation_header)
        self.request_header = require_header_name(request_header)
        if self.correlation_header == self.request_header:
            raise APIConfigurationError(
                "correlation_header and request_header must be different.",
                component=_COMPONENT,
                operation="initialize",
                field="request_header",
            )
        self.max_id_length = max_id_length
        self.reject_invalid_inbound = reject_invalid_inbound
        self.id_factory = id_factory

        logger.info(
            {
                "event": "api_correlation_initialized",
                "correlation_header": self.correlation_header,
                "request_header": self.request_header,
                "max_id_length": self.max_id_length,
                "reject_invalid_inbound": self.reject_invalid_inbound,
            }
        )

    def _new_id(self, *, field: str) -> str:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action=f"Generating {field}",
            event="api_correlation_generate_id_start",
            context={"field": field},
        )
        try:
            raw = self.id_factory()
        except Exception as exc:
            raise APIConfigurationError(
                "Correlation identifier factory failed.",
                component=_COMPONENT,
                operation="generate_id",
                field=field,
                context={"factory_type": type(self.id_factory).__name__},
                cause=exc,
            ) from exc
        if not isinstance(raw, str):
            raise APIConfigurationError(
                "Correlation identifier factory must return text.",
                component=_COMPONENT,
                operation="generate_id",
                field=field,
                context={"received_type": type(raw).__name__},
            )
        try:
            return _normalize_id(
                raw,
                field=field,
                max_length=self.max_id_length,
            )
        except APICorrelationError as exc:
            raise APIConfigurationError(
                "Correlation identifier factory returned an invalid identifier.",
                component=_COMPONENT,
                operation="generate_id",
                field=field,
                cause=exc,
            ) from exc

    def _resolve_inbound_correlation(self, scope: ASGIScope) -> str | None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving inbound correlation ID",
            event="api_correlation_inbound_resolve_start",
        )
        values = header_values(scope, self.correlation_header)
        if not values:
            return None
        if len(values) != 1:
            raise APIInvalidHeaderError(
                "Correlation header must occur at most once.",
                component=_COMPONENT,
                operation="resolve_inbound_correlation",
                field=self.correlation_header,
                context={"occurrences": len(values)},
            )
        try:
            return _normalize_id(
                values[0],
                field=self.correlation_header,
                max_length=self.max_id_length,
            )
        except APICorrelationError:
            if self.reject_invalid_inbound:
                raise
            logger.warning(
                {
                    "event": "api_correlation_invalid_inbound_replaced",
                    "header": self.correlation_header,
                }
            )
            return None

    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Applying correlation middleware",
            event="api_correlation_call_start",
            context={"scope_type": scope.get("type")},
        )
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._new_id(field="request_id")
        # Store the server-owned request ID before parsing client correlation
        # metadata so the outer error mapper can still identify a rejected
        # malformed/duplicate correlation request.
        bimap_state(scope)["correlation"] = {
            "request_id": request_id,
            "correlation_id": None,
        }
        inbound = self._resolve_inbound_correlation(scope)
        correlation_id = inbound or self._new_id(field="correlation_id")
        context = CorrelationContext(
            request_id=request_id,
            correlation_id=correlation_id,
        )
        bimap_state(scope)["correlation"] = context

        async def send_with_ids(message: ASGIMessage) -> None:
            if message.get("type") == "http.response.start":
                set_response_header(message, self.correlation_header, context.correlation_id)
                set_response_header(message, self.request_header, context.request_id)
            await send(message)

        logger.debug(
            {
                "event": "api_correlation_bound",
                "request_id": context.request_id,
                "correlation_id": context.correlation_id,
            }
        )
        await self.app(scope, receive, send_with_ids)


# Backward-compatible alias for the original scaffold name.
Middleware = CorrelationMiddleware


__all__ = [
    "CorrelationContext",
    "CorrelationMiddleware",
    "Middleware",
]


if __name__ == "__main__":
    import asyncio

    print("\n=== Running Correlation Middleware Self-Test ===\n")
    printer.status("TEST", "Correlation middleware module initialized", "info")

    ids = iter(("request-1", "correlation-1"))

    async def _app(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = CorrelationMiddleware(_app, id_factory=lambda: next(ids))
    sent: list[ASGIMessage] = []

    async def _receive() -> ASGIMessage:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message: ASGIMessage) -> None:
        sent.append(dict(message))

    scope: ASGIScope = {"type": "http", "headers": []}
    asyncio.run(middleware(scope, _receive, _send))
    headers = dict(sent[0]["headers"])
    assert headers[b"x-request-id"] == b"request-1"
    assert headers[b"x-correlation-id"] == b"correlation-1"
    assert scope["state"]["bimap"]["correlation"].request_id == "request-1"
    printer.status("PASS", "Correlation propagation", "success")

    print("\n=== Test ran successfully ===\n")