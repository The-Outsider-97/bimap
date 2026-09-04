"""
ASGI request-size and rate-limit enforcement for BIMAP.

The middleware enforces only transport-level request limits.  Product-specific
file counts, upload sizes, evidence quantities, and commercial scope remain in
the canonical product/application layers and must not be duplicated here.

No numeric limits are hard-coded because BIMAP's current configuration files do
not yet establish authoritative production values.  Limits are injected through
:class:`RequestLimitPolicy`.  Distributed rate-limit state is likewise not
implemented in-process; deployments inject a :class:`RateLimiter` adapter so
multi-process/multi-host consistency can be provided by the infrastructure that
actually owns it.

Body-size enforcement is performed twice:
1. an early Content-Length check when a single valid value is present; and
2. streaming byte accounting over ASGI ``http.request`` messages, which remains
   authoritative when Content-Length is absent or inaccurate.
"""

from __future__ import annotations

import inspect

from dataclasses import dataclass
from typing import Any, Awaitable, Protocol

from ..utils.api_errors import *
from ..utils.api_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Request Limits Middleware")
printer = PrettyPrinter()

_COMPONENT = "api_request_limits"


@dataclass(frozen=True, slots=True)
class RequestLimitPolicy:
    """Configured transport-level bounds for one BIMAP API deployment.

    ``None`` disables the corresponding bound.  A value of zero is valid for
    ``max_body_bytes`` and can be used for endpoints/deployments that must not
    accept request bodies.  Header limits are positive because an HTTP request
    necessarily contains protocol metadata.
    """

    max_body_bytes: int | None = None
    max_header_count: int | None = None
    max_header_bytes: int | None = None

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating request-limit policy",
            event="api_request_limits_policy_validate_start",
        )
        if self.max_body_bytes is not None:
            object.__setattr__(
                self,
                "max_body_bytes",
                require_non_negative_int(
                    self.max_body_bytes,
                    field="max_body_bytes",
                    error_type=APIConfigurationError,
                    component=_COMPONENT,
                    operation="validate_policy",
                ),
            )
        if self.max_header_count is not None:
            object.__setattr__(
                self,
                "max_header_count",
                require_positive_int(
                    self.max_header_count,
                    field="max_header_count",
                    error_type=APIConfigurationError,
                    component=_COMPONENT,
                    operation="validate_policy",
                ),
            )
        if self.max_header_bytes is not None:
            object.__setattr__(
                self,
                "max_header_bytes",
                require_positive_int(
                    self.max_header_bytes,
                    field="max_header_bytes",
                    error_type=APIConfigurationError,
                    component=_COMPONENT,
                    operation="validate_policy",
                ),
            )


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Provider-neutral result returned by an injected rate-limit adapter."""

    allowed: bool
    retry_after_seconds: int | None = None
    limit: int | None = None
    remaining: int | None = None

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating rate-limit decision",
            event="api_request_limits_rate_decision_validate_start",
        )
        if not isinstance(self.allowed, bool):
            raise APIConfigurationError(
                "RateLimitDecision.allowed must be boolean.",
                component=_COMPONENT,
                operation="validate_rate_decision",
                field="allowed",
                context={"received_type": type(self.allowed).__name__},
            )

        for field_name in ("retry_after_seconds", "limit", "remaining"):
            value = getattr(self, field_name)
            if value is not None:
                normalized = require_non_negative_int(
                    value,
                    field=field_name,
                    error_type=APIConfigurationError,
                    component=_COMPONENT,
                    operation="validate_rate_decision",
                )
                object.__setattr__(self, field_name, normalized)

        if self.limit is not None and self.remaining is not None:
            if self.remaining > self.limit:
                raise APIConfigurationError(
                    "Rate-limit remaining count cannot exceed the configured limit.",
                    component=_COMPONENT,
                    operation="validate_rate_decision",
                    field="remaining",
                    context={"limit": self.limit, "remaining": self.remaining},
                )


class RateLimiter(Protocol):
    """Infrastructure-owned asynchronous rate-limit decision boundary."""

    def check(self, scope: ASGIScope) -> Awaitable[RateLimitDecision]:
        """Return the current decision for this request without mutating ASGI data."""
        ...


class RequestLimits:
    """Enforce configured request-header/body limits and injected rate limits."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: RequestLimitPolicy,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing request-limits middleware",
            event="api_request_limits_init_start",
        )
        if not callable(app):
            raise APIConfigurationError(
                "RequestLimits requires a callable ASGI application.",
                component=_COMPONENT,
                operation="initialize",
                field="app",
                context={"received_type": type(app).__name__},
            )
        if not isinstance(policy, RequestLimitPolicy):
            raise APIConfigurationError(
                "policy must be a RequestLimitPolicy.",
                component=_COMPONENT,
                operation="initialize",
                field="policy",
                context={"received_type": type(policy).__name__},
            )
        if rate_limiter is not None and not callable(getattr(rate_limiter, "check", None)):
            raise APIConfigurationError(
                "rate_limiter must provide an asynchronous check(scope) method.",
                component=_COMPONENT,
                operation="initialize",
                field="rate_limiter",
                context={"received_type": type(rate_limiter).__name__},
            )

        self.app = app
        self.policy = policy
        self.rate_limiter = rate_limiter
        logger.info(
            {
                "event": "api_request_limits_initialized",
                "max_body_bytes": policy.max_body_bytes,
                "max_header_count": policy.max_header_count,
                "max_header_bytes": policy.max_header_bytes,
                "rate_limiter_configured": rate_limiter is not None,
            }
        )

    def _enforce_headers(self, scope: ASGIScope) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Enforcing request-header limits",
            event="api_request_limits_headers_start",
        )
        count, raw_bytes = request_header_metrics(scope)
        if self.policy.max_header_count is not None and count > self.policy.max_header_count:
            raise APIRequestHeadersTooLargeError(
                "Request contains more headers than the configured API limit.",
                component=_COMPONENT,
                operation="enforce_headers",
                field="headers",
                context={
                    "header_count": count,
                    "max_header_count": self.policy.max_header_count,
                },
            )
        if self.policy.max_header_bytes is not None and raw_bytes > self.policy.max_header_bytes:
            raise APIRequestHeadersTooLargeError(
                "Request headers exceed the configured raw-byte limit.",
                component=_COMPONENT,
                operation="enforce_headers",
                field="headers",
                context={
                    "header_bytes": raw_bytes,
                    "max_header_bytes": self.policy.max_header_bytes,
                },
            )

    def _enforce_declared_body_size(self, scope: ASGIScope) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Enforcing declared request-body limit",
            event="api_request_limits_content_length_start",
        )
        if self.policy.max_body_bytes is None:
            return
        declared = parse_content_length(scope)
        if declared is not None and declared > self.policy.max_body_bytes:
            raise APIRequestTooLargeError(
                "Declared request body exceeds the configured limit.",
                component=_COMPONENT,
                operation="enforce_declared_body_size",
                field="content-length",
                context={
                    "declared_bytes": declared,
                    "max_body_bytes": self.policy.max_body_bytes,
                },
            )

    async def _enforce_rate_limit(self, scope: ASGIScope) -> RateLimitDecision | None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Evaluating request rate limit",
            event="api_request_limits_rate_start",
            context={"correlation_id": get_correlation_id(scope)},
        )
        if self.rate_limiter is None:
            return None
        try:
            pending = self.rate_limiter.check(scope)
        except TimeoutError as exc:
            raise APIGatewayTimeoutError(
                "Rate-limit dependency timed out.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise APIServiceUnavailableError(
                "Rate-limit dependency is unavailable.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise APIInternalError(
                "Rate-limit adapter failed before returning a decision.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
                cause=exc,
            ) from exc
        if not inspect.isawaitable(pending):
            raise APIConfigurationError(
                "Rate-limit adapter check(scope) must return an awaitable.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
            )
        try:
            decision = await pending
        except TimeoutError as exc:
            raise APIGatewayTimeoutError(
                "Rate-limit dependency timed out.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise APIServiceUnavailableError(
                "Rate-limit dependency is unavailable.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise APIInternalError(
                "Rate-limit adapter failed while evaluating the request.",
                component=_COMPONENT,
                operation="check_rate_limit",
                context={"adapter_type": type(self.rate_limiter).__name__},
                cause=exc,
            ) from exc
        if not isinstance(decision, RateLimitDecision):
            raise APIConfigurationError(
                "Rate-limit adapter returned an unsupported decision type.",
                component=_COMPONENT,
                operation="check_rate_limit",
                field="result",
                context={"received_type": type(decision).__name__},
            )
        if not decision.allowed:
            raise APIRateLimitError(
                retry_after_seconds=decision.retry_after_seconds,
                component=_COMPONENT,
                operation="check_rate_limit",
                context={
                    "limit": decision.limit,
                    "remaining": decision.remaining,
                    "correlation_id": get_correlation_id(scope),
                },
            )
        return decision

    def _limited_receive(self, receive: ASGIReceive) -> ASGIReceive:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Wrapping request-body receiver",
            event="api_request_limits_receive_wrap_start",
        )
        if self.policy.max_body_bytes is None:
            return receive

        max_bytes = self.policy.max_body_bytes
        consumed = 0

        async def receive_limited() -> ASGIMessage:
            nonlocal consumed
            message = await receive()
            if message.get("type") != "http.request":
                return message

            body = message.get("body", b"")
            if body is None:
                body = b""
            if not isinstance(body, (bytes, bytearray, memoryview)):
                raise APIProtocolError(
                    "ASGI http.request body must be bytes-like.",
                    component=_COMPONENT,
                    operation="receive_body",
                    field="message.body",
                    context={"received_type": type(body).__name__},
                )

            consumed += len(body)
            if consumed > max_bytes:
                raise APIRequestTooLargeError(
                    "Streaming request body exceeded the configured limit.",
                    component=_COMPONENT,
                    operation="receive_body",
                    field="body",
                    context={
                        "received_bytes": consumed,
                        "max_body_bytes": max_bytes,
                    },
                )
            return message

        return receive_limited

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
            action="Applying request-limits middleware",
            event="api_request_limits_call_start",
            context={"scope_type": scope.get("type")},
        )
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        self._enforce_headers(scope)
        self._enforce_declared_body_size(scope)
        decision = await self._enforce_rate_limit(scope)
        limited_receive = self._limited_receive(receive)

        if decision is not None:
            logger.debug(
                {
                    "event": "api_request_rate_allowed",
                    "limit": decision.limit,
                    "remaining": decision.remaining,
                    "correlation_id": get_correlation_id(scope),
                }
            )
        await self.app(scope, limited_receive, send)


__all__ = [
    "RequestLimitPolicy",
    "RateLimitDecision",
    "RateLimiter",
    "RequestLimits",
]


if __name__ == "__main__":
    import asyncio

    print("\n=== Running Request Limits Middleware Self-Test ===\n")
    printer.status("TEST", "Request limits middleware initialized", "info")

    received_total: list[int] = []

    async def _app(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        message = await receive()
        received_total.append(len(message.get("body", b"")))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = RequestLimits(
        _app,
        policy=RequestLimitPolicy(
            max_body_bytes=8,
            max_header_count=8,
            max_header_bytes=256,
        ),
    )

    messages = iter(
        ({"type": "http.request", "body": b"1234", "more_body": False},)
    )

    async def _receive() -> ASGIMessage:
        return next(messages)

    async def _send(message: ASGIMessage) -> None:
        return None

    scope: ASGIScope = {
        "type": "http",
        "headers": [(b"content-length", b"4")],
    }
    asyncio.run(middleware(scope, _receive, _send))
    assert received_total == [4]

    too_large_scope: ASGIScope = {
        "type": "http",
        "headers": [(b"content-length", b"9")],
    }
    try:
        asyncio.run(middleware(too_large_scope, _receive, _send))
    except APIRequestTooLargeError:
        pass
    else:
        raise AssertionError("Expected APIRequestTooLargeError")

    printer.status("PASS", "Request size enforcement", "success")
    print("\n=== Test ran successfully ===\n")