"""
Safe exception-to-HTTP mapping middleware for BIMAP.

The API boundary is the single owner of HTTP status semantics.  Lower BIMAP
layers expose stable exception classes/codes but intentionally do not know HTTP.
This middleware translates those failures without parsing exception strings and
without copying lower-layer technical messages, contexts, provider payloads, or
nested exception text into client responses.

Mapping principles
------------------
* API-native errors preserve their explicitly selected HTTP semantics.
* client/input validation failures map to 400/422 without exposing internals.
* missing canonical resources map to 404 only when the lower error explicitly
  means "not found"; absence is never guessed from arbitrary messages.
* optimistic-concurrency/domain-state conflicts map to 409.
* dependency unavailability/timeouts map to 503/504.
* integrity, serialization, configuration, reporting and unexpected failures
  remain 500 unless a more specific stable class proves otherwise.
* SLAI runtime failures marked retryable map to 503; internal SLAI policy or
  mapping failures are not misrepresented as user authorization failures.
* once an HTTP response has started, the middleware cannot safely replace it
  with a problem response; the original exception is re-raised to the server.

Responses use the API error layer's safe RFC-9457-style problem document and
``application/problem+json``.  Error details are intentionally generic.
"""

from __future__ import annotations

from typing import Any

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.utils.app_errors import *
from ...audit_engine.utils.engine_errors import *
from ...contracts.utils.contracts_errors import *
from ...domain.utils.domain_errors import *
from ...reporting.utils.reporting_errors import ReportingError
from ...slai.utils.slai_errors import SLAIIntegrationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Error Mapping Middleware")
printer = PrettyPrinter()

_COMPONENT = "api_error_mapping"


class ErrorMapping:
    """Map stable BIMAP exception families to safe HTTP problem responses."""

    def __init__(self, app: ASGIApp) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing API error-mapping middleware",
            event="api_error_mapping_init_start",
        )
        if not callable(app):
            from ..utils.api_errors import APIConfigurationError

            raise APIConfigurationError(
                "ErrorMapping requires a callable ASGI application.",
                component=_COMPONENT,
                operation="initialize",
                field="app",
                context={"received_type": type(app).__name__},
            )
        self.app = app
        logger.info({"event": "api_error_mapping_initialized"})

    @classmethod
    def map_exception(cls, error: BaseException) -> APIError:
        """Translate one known BIMAP/lower-runtime failure into an ``APIError``."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Mapping exception to HTTP error",
            event="api_error_mapping_translate_start",
            context={"error_type": type(error).__name__},
        )

        if isinstance(error, APIError):
            return error

        # Application layer: only explicitly stable semantics are made public.
        if isinstance(error, RepositoryConflictError):
            return APIConflictError(
                "Repository optimistic-concurrency precondition failed.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, StorageNotFoundError):
            return APINotFoundError(
                "Requested storage-backed resource was not found.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(
            error,
            (
                AppPortTimeoutError,
                MalwareTimeoutError,
                PaymentTimeoutError,
                QueueTimeoutError,
                RepositoryTimeoutError,
                StorageTimeoutError,
            ),
        ):
            return APIGatewayTimeoutError(
                "Application dependency timed out.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(
            error,
            (
                AppPortUnavailableError,
                MalwareUnavailableError,
                PaymentUnavailableError,
                QueueUnavailableError,
                RepositoryUnavailableError,
                StorageUnavailableError,
            ),
        ):
            return APIServiceUnavailableError(
                "Application dependency is unavailable.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, AppValidationError):
            return APIValidationError(
                "Application request validation failed.",
                component=_COMPONENT,
                operation="map_exception",
                field=getattr(error, "field", None),
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, AppError):
            if bool(getattr(error, "retryable", False)):
                return APIServiceUnavailableError(
                    "A required application dependency is temporarily unavailable.",
                    component=_COMPONENT,
                    operation="map_exception",
                    context=lower_error_context(error),
                    cause=error,
                )
            return APIInternalError(
                "Unhandled application-layer failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )

        # Domain semantics: validation is bad input; state/invariant clashes are
        # conflicts; only an explicit NotFound type becomes HTTP 404.
        if isinstance(error, EvidenceNotFoundError):
            return APINotFoundError(
                "Required domain resource was not found.",
                component=_COMPONENT,
                operation="map_exception",
                field=getattr(error, "field", None),
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, DomainValidationError):
            return APIValidationError(
                "Domain validation rejected request data.",
                component=_COMPONENT,
                operation="map_exception",
                field=getattr(error, "field", None),
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, DomainInvariantError):
            return APIConflictError(
                "Requested operation conflicts with canonical domain state.",
                component=_COMPONENT,
                operation="map_exception",
                field=getattr(error, "field", None),
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, DomainError):
            return APIInternalError(
                "Unhandled domain failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )

        # External contract parsing/version errors are client-facing validation
        # failures. Contract integrity/serialization/registry definition errors
        # remain internal by falling through to the ContractError branch.
        if isinstance(
            error,
            (
                ContractValidationError,
                ContractDeserializationError,
                ContractVersionError,
                ContractSchemaValidationError,
            ),
        ):
            return APIValidationError(
                "External contract validation failed.",
                component=_COMPONENT,
                operation="map_exception",
                field=getattr(error, "field", None),
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, ContractError):
            return APIInternalError(
                "Contract-layer failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )

        # Engine validation can be reached by endpoints that accept analytical
        # input directly; it is represented as semantically unprocessable input.
        # Internal engine failures remain 500.
        if isinstance(error, IngestionDeserializationError):
            return APIValidationError(
                "Audit ingestion payload could not be decoded.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, EngineValidationError):
            return APIUnprocessableError(
                "Audit input failed engine-level validation.",
                component=_COMPONENT,
                operation="map_exception",
                field=getattr(error, "field", None),
                context=lower_error_context(error),
                cause=error,
            )
        if isinstance(error, EngineError):
            return APIInternalError(
                "Audit-engine failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )

        # Reporting is output construction and should not be reclassified as a
        # client error merely because some reporting subclasses use 'validation'.
        if isinstance(error, ReportingError):
            return APIInternalError(
                "Reporting failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )

        # SLAI integration policy is internal system policy, not HTTP user auth.
        if isinstance(error, SLAIIntegrationError):
            if bool(getattr(error, "retryable", False)):
                return APIServiceUnavailableError(
                    "SLAI runtime is temporarily unavailable.",
                    component=_COMPONENT,
                    operation="map_exception",
                    context=lower_error_context(error),
                    cause=error,
                )
            return APIInternalError(
                "SLAI integration failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context=lower_error_context(error),
                cause=error,
            )

        # Conservative standard-library infrastructure fallbacks.  These do not
        # inspect exception messages and therefore cannot disclose provider text.
        if isinstance(error, TimeoutError):
            return APIGatewayTimeoutError(
                "Unhandled dependency timeout reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context={"lower_error_type": type(error).__name__},
                cause=error,
            )
        if isinstance(error, ConnectionError):
            return APIServiceUnavailableError(
                "Unhandled dependency connection failure reached the API boundary.",
                component=_COMPONENT,
                operation="map_exception",
                context={"lower_error_type": type(error).__name__},
                cause=error,
            )

        return APIInternalError(
            "Unexpected exception reached the API boundary.",
            component=_COMPONENT,
            operation="map_exception",
            context={"lower_error_type": type(error).__name__},
            cause=error,
        )

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
            action="Applying API error-mapping middleware",
            event="api_error_mapping_call_start",
            context={"scope_type": scope.get("type")},
        )
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_tracked(message: ASGIMessage) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                if response_started:
                    raise APIInternalError(
                        "ASGI application attempted to start the HTTP response twice.",
                        component=_COMPONENT,
                        operation="send_tracked",
                    )
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_tracked)
        except Exception as exc:
            correlation_id = get_correlation_id(scope)
            request_id = get_request_id(scope)

            if response_started:
                logger.error(
                    {
                        "event": "api_error_after_response_started",
                        "error_type": type(exc).__name__,
                        "error_code": getattr(exc, "code", None),
                        "correlation_id": correlation_id,
                        "request_id": request_id,
                    }
                )
                raise

            mapped = self.map_exception(exc)
            logger.error(
                {
                    "event": "api_exception_mapped",
                    "source_type": type(exc).__name__,
                    "source_code": getattr(exc, "code", None),
                    "mapped_code": mapped.code,
                    "status_code": mapped.status_code,
                    "retryable": mapped.retryable,
                    "correlation_id": correlation_id,
                    "request_id": request_id,
                }
            )
            printer.status(
                "API",
                f"HTTP request failed with {mapped.status_code} ({mapped.code})",
                "error",
            )
            await send_problem_response(
                send,
                error=mapped,
                correlation_id=correlation_id,
                request_id=request_id,
                suppress_body=str(scope.get("method", "")).upper() == "HEAD",
            )


ErrorMappingMiddleware = ErrorMapping


__all__ = [
    "ErrorMapping",
    "ErrorMappingMiddleware",
]


if __name__ == "__main__":
    import asyncio

    print("\n=== Running API Error Mapping Self-Test ===\n")
    printer.status("TEST", "API error mapping middleware initialized", "info")

    mapped = ErrorMapping.map_exception(
        DomainInvariantError("Transition is not permitted.", field="target_state")
    )
    assert isinstance(mapped, APIConflictError)
    assert mapped.status_code == 409
    assert "Transition is not permitted" not in str(mapped.to_public_dict())

    async def _app(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        raise AppValidationError("private validation detail", field="order_id")

    middleware = ErrorMapping(_app)
    sent: list[ASGIMessage] = []

    async def _receive() -> ASGIMessage:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message: ASGIMessage) -> None:
        sent.append(dict(message))

    asyncio.run(
        middleware(
            {"type": "http", "method": "GET", "headers": []},
            _receive,
            _send,
        )
    )
    assert sent[0]["status"] == 400
    assert b"private validation detail" not in sent[1]["body"]
    printer.status("PASS", "Safe exception-to-HTTP mapping", "success")

    print("\n=== Test ran successfully ===\n")