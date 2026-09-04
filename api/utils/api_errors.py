"""
Structured HTTP/API error hierarchy for the R3D BIM Audit Platform (BIMAP).

The API layer is an outer architectural boundary.  Errors created here are
therefore allowed to describe HTTP-facing failure semantics, but they must not
leak raw BIM evidence, credentials, authorization material, uploaded file
content, provider payloads, filesystem paths, signed URLs, cookies, or nested
exception messages to clients or logs.

Design rules
------------
* Exception construction has no logging side effect.  A handling boundary may
  call :meth:`APIError.announce` exactly once when operator-facing status is
  useful.
* ``code`` and ``status_code`` are stable machine-readable properties.
* ``public_message`` is deliberately separate from the technical ``message``.
  Lower-layer exception messages are never copied to clients automatically.
* Diagnostic context is bounded and redacted before it is retained.
* ``cause`` is kept for exception chaining, while ``to_dict()`` exposes only
  its type.
* Header values attached to an API error are response metadata only.  They are
  validated again by ``api_helpers`` before emission.
* Request-limit and security outcomes are represented by explicit subclasses;
  they are not encoded by string matching exception messages.

Dependency direction
--------------------
``api_errors.py`` sits at the bottom of ``bimap.api`` and must not import API
middleware, routes, application services, domain models, concrete adapters, or
SLAI runtime objects.  This keeps the API error vocabulary reusable and avoids
circular imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Errors")
printer = PrettyPrinter()

_REDACTED = "<redacted>"
_MAX_CONTEXT_DEPTH = 3
_MAX_CONTEXT_ITEMS = 32
_MAX_CONTEXT_STRING = 256
_MAX_HEADER_VALUE = 1024

_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "body",
    "cookie",
    "credential",
    "document",
    "evidence_content",
    "evidence_value",
    "file_bytes",
    "filename",
    "filepath",
    "object_key",
    "password",
    "path",
    "payload",
    "payment_reference",
    "presigned",
    "raw",
    "secret",
    "session",
    "signature",
    "signed_url",
    "storage_key",
    "stream",
    "token",
    "uri",
    "url",
)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a diagnostic key should be redacted by default."""
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded representation suitable for API diagnostics."""
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


def sanitize_api_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize diagnostic context without retaining sensitive request data."""
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise TypeError(
            "API error context must be a mapping or None, "
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


def _normalize_error_headers(
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    """Retain only syntactically safe, bounded response-header metadata."""
    if headers is None:
        return {}
    if not isinstance(headers, Mapping):
        raise TypeError(
            "API error headers must be a mapping or None, "
            f"got {type(headers).__name__}."
        )

    result: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip()
        value = str(raw_value).strip()
        if not name:
            raise ValueError("API error header name must not be empty.")
        if not value:
            raise ValueError("API error header value must not be empty.")
        if any(ch in name for ch in "\r\n:"):
            raise ValueError("API error header name contains an unsafe character.")
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ValueError("API error header value contains an unsafe character.")
        if len(value) > _MAX_HEADER_VALUE:
            raise ValueError("API error header value exceeds the supported bound.")
        result[name] = value
    return result


class APIError(Exception):
    """Base exception for BIMAP HTTP/API boundary failures."""

    code = "BIMAP.API.ERROR"
    status_code = 500
    public_message = "The request could not be completed."
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        public_message: str | None = None,
        component: str | None = None,
        operation: str | None = None,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        normalized_public = (
            str(public_message).strip()
            if public_message is not None
            else str(self.public_message).strip()
        )
        if not normalized_public:
            normalized_public = "The request could not be completed."

        self.message = normalized_message
        self.client_message = normalized_public
        self.component = str(component).strip() if component is not None else None
        self.operation = str(operation).strip() if operation is not None else None
        self.field = str(field).strip() if field is not None else None
        self.context = sanitize_api_context(context)
        self.headers = _normalize_error_headers(headers)
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
        label: str = "API",
        level: str = "error",
    ) -> None:
        """Explicitly emit one operator-facing status for a handled API error."""
        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "api_error_announced",
                "code": self.code,
                "type": self.__class__.__name__,
                "status_code": self.status_code,
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
        """Return a deterministic, logging-safe technical representation."""
        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "public_message": self.client_message,
            "status_code": int(self.status_code),
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
        if self.headers:
            payload["headers"] = dict(self.headers)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__
        return payload

    def to_public_dict(
        self,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a safe RFC-9457-style problem document.

        ``about:blank`` is used deliberately because BIMAP does not yet publish
        a stable external problem-type URI registry.  ``code`` remains the
        stable BIMAP machine-readable discriminator.
        """
        payload: dict[str, Any] = {
            "type": "about:blank",
            "title": self.client_message,
            "status": int(self.status_code),
            "detail": self.client_message,
            "code": self.code,
        }
        if correlation_id:
            payload["correlation_id"] = correlation_id
        return payload


class APIConfigurationError(APIError):
    code = "BIMAP.API.CONFIGURATION"
    status_code = 500
    public_message = "The service is not configured to complete this request."


class APIInternalError(APIError):
    code = "BIMAP.API.INTERNAL"
    status_code = 500
    public_message = "An internal service error occurred."


class APIValidationError(APIError):
    code = "BIMAP.API.VALIDATION"
    status_code = 400
    public_message = "The request is invalid."


class APIProtocolError(APIValidationError):
    code = "BIMAP.API.PROTOCOL"
    public_message = "The HTTP request is malformed or ambiguous."


class APIRequestError(APIValidationError):
    code = "BIMAP.API.REQUEST"


class APIUnauthorizedError(APIError):
    code = "BIMAP.API.UNAUTHORIZED"
    status_code = 401
    public_message = "Authentication is required."


class APIForbiddenError(APIError):
    code = "BIMAP.API.FORBIDDEN"
    status_code = 403
    public_message = "The request is not permitted."


class APINotFoundError(APIError):
    code = "BIMAP.API.NOT_FOUND"
    status_code = 404
    public_message = "The requested resource was not found."


class APIConflictError(APIError):
    code = "BIMAP.API.CONFLICT"
    status_code = 409
    public_message = "The request conflicts with the current resource state."


class APIRequestTooLargeError(APIRequestError):
    code = "BIMAP.API.REQUEST.TOO_LARGE"
    status_code = 413
    public_message = "The request body exceeds the configured limit."


class APIUnsupportedMediaTypeError(APIRequestError):
    code = "BIMAP.API.REQUEST.UNSUPPORTED_MEDIA_TYPE"
    status_code = 415
    public_message = "The request media type is not supported."


class APIUnprocessableError(APIRequestError):
    code = "BIMAP.API.REQUEST.UNPROCESSABLE"
    status_code = 422
    public_message = "The request could not be processed in its current form."




class APIRequestHeadersTooLargeError(APIRequestError):
    code = "BIMAP.API.REQUEST.HEADERS_TOO_LARGE"
    status_code = 431
    public_message = "The request headers exceed the configured limit."


class APIRateLimitError(APIError):
    code = "BIMAP.API.RATE_LIMIT"
    status_code = 429
    public_message = "Too many requests were received."
    retryable = True

    def __init__(
        self,
        message: str = "Request rate limit exceeded.",
        *,
        retry_after_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        if retry_after_seconds is not None:
            if isinstance(retry_after_seconds, bool) or not isinstance(
                retry_after_seconds, int
            ):
                raise TypeError("retry_after_seconds must be an integer or None.")
            if retry_after_seconds < 0:
                raise ValueError("retry_after_seconds must not be negative.")

        headers = dict(kwargs.pop("headers", {}) or {})
        if retry_after_seconds is not None:
            headers["Retry-After"] = str(retry_after_seconds)

        context = dict(kwargs.pop("context", {}) or {})
        if retry_after_seconds is not None:
            context["retry_after_seconds"] = retry_after_seconds

        self.retry_after_seconds = retry_after_seconds
        super().__init__(message, headers=headers, context=context, **kwargs)


class APICorrelationError(APIRequestError):
    code = "BIMAP.API.CORRELATION"
    public_message = "The request correlation metadata is invalid."


class APISecurityError(APIError):
    code = "BIMAP.API.SECURITY"
    status_code = 400
    public_message = "The request failed HTTP security validation."


class APIInvalidHeaderError(APISecurityError):
    code = "BIMAP.API.SECURITY.HEADER"
    public_message = "The request contains an invalid or ambiguous HTTP header."


class APIHostRejectedError(APISecurityError):
    code = "BIMAP.API.SECURITY.HOST"
    public_message = "The request host is not accepted by this service."


class APIInsecureTransportError(APISecurityError):
    code = "BIMAP.API.SECURITY.TRANSPORT"
    status_code = 403
    public_message = "Secure transport is required for this request."


class APIServiceUnavailableError(APIError):
    code = "BIMAP.API.SERVICE_UNAVAILABLE"
    status_code = 503
    public_message = "A required service is temporarily unavailable."
    retryable = True


class APIGatewayTimeoutError(APIError):
    code = "BIMAP.API.GATEWAY_TIMEOUT"
    status_code = 504
    public_message = "A required service did not respond in time."
    retryable = True


__all__ = [
    "sanitize_api_context",
    "APIError",
    "APIConfigurationError",
    "APIInternalError",
    "APIValidationError",
    "APIProtocolError",
    "APIRequestError",
    "APIUnauthorizedError",
    "APIForbiddenError",
    "APINotFoundError",
    "APIConflictError",
    "APIRequestTooLargeError",
    "APIUnsupportedMediaTypeError",
    "APIUnprocessableError",
    "APIRequestHeadersTooLargeError",
    "APIRateLimitError",
    "APICorrelationError",
    "APISecurityError",
    "APIInvalidHeaderError",
    "APIHostRejectedError",
    "APIInsecureTransportError",
    "APIServiceUnavailableError",
    "APIGatewayTimeoutError",
]


if __name__ == "__main__":
    print("\n=== Running API Errors Self-Test ===\n")
    printer.status("TEST", "API errors module initialized", "info")

    error = APIRateLimitError(
        retry_after_seconds=30,
        component="request_limits",
        operation="check_rate_limit",
        context={
            "correlation_id": "corr-1",
            "authorization": "Bearer must-not-leak",
            "payload": "must-not-leak",
        },
        cause=RuntimeError("private provider detail"),
    )
    technical = error.to_dict()
    public = error.to_public_dict(correlation_id="corr-1")

    assert technical["context"]["authorization"] == _REDACTED
    assert technical["context"]["payload"] == _REDACTED
    assert technical["headers"]["Retry-After"] == "30"
    assert technical["cause_type"] == "RuntimeError"
    assert public["status"] == 429
    assert "must-not-leak" not in str(technical)
    assert "private provider detail" not in str(technical)
    printer.status("PASS", "API error redaction and public projection", "success")

    print("\n=== Test ran successfully ===\n")