"""
HTTP request/response security middleware for BIMAP's ASGI API boundary.

This module intentionally owns only HTTP-transport hardening.  It does not
implement user authentication, authorization, payment verification, malware
scanning, upload validation, CORS policy, or SLAI Safety/Privacy logic; those
concerns have separate owners in BIMAP/SLAI.

The current repository does not establish a concrete web framework or final
production host/CSP/HSTS configuration.  The middleware therefore uses pure
ASGI and injects deployment-specific policy through :class:`SecurityPolicy`.
It provides a conservative protocol baseline while keeping policies that may
break deployment topology (host allowlists, HTTPS enforcement, HSTS, CSP,
Permissions-Policy) explicit rather than guessed.

Security properties
-------------------
* duplicate/malformed Host values are rejected before downstream routing;
* an optional exact hostname allowlist protects against Host-header confusion;
* optional HTTPS enforcement uses the ASGI ``scheme`` value only and never
  trusts X-Forwarded-* headers implicitly;
* response header values are validated against CR/LF/NUL injection;
* X-Content-Type-Options defaults to ``nosniff`` because it is API-safe and does
  not require knowledge of frontend script/style sources;
* CSP, HSTS and Permissions-Policy are opt-in deployment policy;
* security context stored in request state contains transport metadata only.
"""

from __future__ import annotations

import ipaddress

from dataclasses import dataclass
from typing import Any

from ..utils.api_errors import *
from ..utils.api_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Security Middleware")
printer = PrettyPrinter()

_COMPONENT = "api_security"


def _normalize_hostname(value: str, *, field: str) -> str:
    """Normalize a configured/request hostname without accepting URL syntax."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing HTTP hostname",
        event="api_security_hostname_normalize_start",
        context={"field": field},
    )
    if not isinstance(value, str):
        raise APIConfigurationError(
            "Hostname must be text.",
            component=_COMPONENT,
            operation="normalize_hostname",
            field=field,
            context={"received_type": type(value).__name__},
        )
    text = value.strip()
    if not text:
        raise APIConfigurationError(
            "Hostname must not be empty.",
            component=_COMPONENT,
            operation="normalize_hostname",
            field=field,
        )
    if any(ch in text for ch in "/\\?#@\r\n\x00") or any(ch.isspace() for ch in text):
        raise APIConfigurationError(
            "Hostname contains URL syntax, whitespace, or control characters.",
            component=_COMPONENT,
            operation="normalize_hostname",
            field=field,
        )

    # Bracketed IPv6 authority, optionally followed by a decimal port.
    if text.startswith("["):
        close = text.find("]")
        if close <= 1:
            raise APIConfigurationError(
                "Bracketed IPv6 hostname is malformed.",
                component=_COMPONENT,
                operation="normalize_hostname",
                field=field,
            )
        host = text[1:close]
        suffix = text[close + 1 :]
        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                raise APIConfigurationError(
                    "IPv6 host port is malformed.",
                    component=_COMPONENT,
                    operation="normalize_hostname",
                    field=field,
                )
            if int(suffix[1:]) > 65535:
                raise APIConfigurationError(
                    "IPv6 host port exceeds the valid TCP/UDP port range.",
                    component=_COMPONENT,
                    operation="normalize_hostname",
                    field=field,
                )
        try:
            return ipaddress.IPv6Address(host).compressed.lower()
        except ValueError as exc:
            raise APIConfigurationError(
                "Bracketed IPv6 hostname is invalid.",
                component=_COMPONENT,
                operation="normalize_hostname",
                field=field,
                cause=exc,
            ) from exc

    host = text
    if text.count(":") == 1:
        candidate, port = text.rsplit(":", 1)
        if port:
            if not port.isdigit():
                raise APIConfigurationError(
                    "Hostname port must be decimal when present.",
                    component=_COMPONENT,
                    operation="normalize_hostname",
                    field=field,
                )
            if int(port) > 65535:
                raise APIConfigurationError(
                    "Hostname port exceeds the valid TCP/UDP port range.",
                    component=_COMPONENT,
                    operation="normalize_hostname",
                    field=field,
                )
            host = candidate
    elif text.count(":") > 1:
        # Unbracketed IPv6 is accepted as a host, but never as host:port.
        try:
            return ipaddress.IPv6Address(text).compressed.lower()
        except ValueError as exc:
            raise APIConfigurationError(
                "Unbracketed hostname containing multiple colons is invalid.",
                component=_COMPONENT,
                operation="normalize_hostname",
                field=field,
                cause=exc,
            ) from exc

    host = host.rstrip(".").lower()
    if not host:
        raise APIConfigurationError(
            "Hostname is empty after normalization.",
            component=_COMPONENT,
            operation="normalize_hostname",
            field=field,
        )

    try:
        ipaddress.IPv4Address(host)
        return host
    except ValueError:
        pass

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise APIConfigurationError(
            "Hostname cannot be normalized to IDNA ASCII.",
            component=_COMPONENT,
            operation="normalize_hostname",
            field=field,
            cause=exc,
        ) from exc

    if len(ascii_host) > 253:
        raise APIConfigurationError(
            "Hostname exceeds the DNS length bound.",
            component=_COMPONENT,
            operation="normalize_hostname",
            field=field,
        )
    labels = ascii_host.split(".")
    for label in labels:
        if not label or len(label) > 63:
            raise APIConfigurationError(
                "Hostname contains an empty or overlong DNS label.",
                component=_COMPONENT,
                operation="normalize_hostname",
                field=field,
            )
        if label.startswith("-") or label.endswith("-"):
            raise APIConfigurationError(
                "Hostname DNS labels must not start or end with '-'.",
                component=_COMPONENT,
                operation="normalize_hostname",
                field=field,
            )
        if not all(ch.isalnum() or ch == "-" for ch in label):
            raise APIConfigurationError(
                "Hostname contains unsupported DNS label characters.",
                component=_COMPONENT,
                operation="normalize_hostname",
                field=field,
            )
    return ascii_host.lower()


def _request_host(scope: ASGIScope) -> str | None:
    """Resolve one unambiguous Host header and translate syntax errors safely."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Resolving request Host header",
        event="api_security_request_host_start",
    )
    raw = single_header(scope, "host")
    if raw is None:
        return None
    try:
        return _normalize_hostname(raw, field="host")
    except APIConfigurationError as exc:
        raise APIInvalidHeaderError(
            "Request Host header is malformed.",
            component=_COMPONENT,
            operation="resolve_request_host",
            field="host",
            cause=exc,
        ) from exc


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    """Deployment-owned HTTP security policy.

    ``allowed_hosts`` contains exact normalized hostnames, not URL origins or
    wildcard patterns.  An empty tuple means no host allowlist is enforced.
    ``require_https`` relies on the ASGI server/proxy integration to provide a
    trustworthy ``scope['scheme']`` value.
    """

    allowed_hosts: tuple[str, ...] = ()
    require_https: bool = False
    add_nosniff: bool = True
    referrer_policy: str | None = None
    frame_options: str | None = None
    content_security_policy: str | None = None
    permissions_policy: str | None = None
    hsts_max_age_seconds: int | None = None
    hsts_include_subdomains: bool = False
    hsts_preload: bool = False

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating API security policy",
            event="api_security_policy_validate_start",
        )
        if isinstance(self.allowed_hosts, (str, bytes, bytearray)):
            raise APIConfigurationError(
                "allowed_hosts must be a tuple/sequence of exact hostnames.",
                component=_COMPONENT,
                operation="validate_policy",
                field="allowed_hosts",
            )
        try:
            raw_hosts = tuple(self.allowed_hosts)
        except TypeError as exc:
            raise APIConfigurationError(
                "allowed_hosts must be iterable.",
                component=_COMPONENT,
                operation="validate_policy",
                field="allowed_hosts",
                cause=exc,
            ) from exc

        normalized_hosts: list[str] = []
        seen: set[str] = set()
        for index, host in enumerate(raw_hosts):
            normalized = _normalize_hostname(host, field=f"allowed_hosts[{index}]")
            if normalized in seen:
                raise APIConfigurationError(
                    "allowed_hosts contains a duplicate normalized hostname.",
                    component=_COMPONENT,
                    operation="validate_policy",
                    field="allowed_hosts",
                    context={"host": normalized},
                )
            seen.add(normalized)
            normalized_hosts.append(normalized)
        object.__setattr__(self, "allowed_hosts", tuple(normalized_hosts))

        for field_name in (
            "require_https",
            "add_nosniff",
            "hsts_include_subdomains",
            "hsts_preload",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise APIConfigurationError(
                    f"{field_name} must be boolean.",
                    component=_COMPONENT,
                    operation="validate_policy",
                    field=field_name,
                )

        for field_name in (
            "referrer_policy",
            "frame_options",
            "content_security_policy",
            "permissions_policy",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    require_header_value(value, field=field_name),
                )

        if self.hsts_max_age_seconds is not None:
            if isinstance(self.hsts_max_age_seconds, bool) or not isinstance(
                self.hsts_max_age_seconds, int
            ):
                raise APIConfigurationError(
                    "hsts_max_age_seconds must be a non-negative integer or None.",
                    component=_COMPONENT,
                    operation="validate_policy",
                    field="hsts_max_age_seconds",
                )
            if self.hsts_max_age_seconds < 0:
                raise APIConfigurationError(
                    "hsts_max_age_seconds must not be negative.",
                    component=_COMPONENT,
                    operation="validate_policy",
                    field="hsts_max_age_seconds",
                )
        elif self.hsts_include_subdomains or self.hsts_preload:
            raise APIConfigurationError(
                "HSTS subdomain/preload flags require hsts_max_age_seconds.",
                component=_COMPONENT,
                operation="validate_policy",
                field="hsts_max_age_seconds",
            )

    def response_headers(self, *, secure_transport: bool) -> dict[str, str]:
        """Return the exact configured response-security headers."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building response security headers",
            event="api_security_response_headers_start",
            context={"secure_transport": secure_transport},
        )
        headers: dict[str, str] = {}
        if self.add_nosniff:
            headers["X-Content-Type-Options"] = "nosniff"
        if self.referrer_policy is not None:
            headers["Referrer-Policy"] = self.referrer_policy
        if self.frame_options is not None:
            headers["X-Frame-Options"] = self.frame_options
        if self.content_security_policy is not None:
            headers["Content-Security-Policy"] = self.content_security_policy
        if self.permissions_policy is not None:
            headers["Permissions-Policy"] = self.permissions_policy
        if secure_transport and self.hsts_max_age_seconds is not None:
            value = f"max-age={self.hsts_max_age_seconds}"
            if self.hsts_include_subdomains:
                value += "; includeSubDomains"
            if self.hsts_preload:
                value += "; preload"
            headers["Strict-Transport-Security"] = value
        return headers


@dataclass(frozen=True, slots=True)
class SecurityContext:
    """Non-authentication transport metadata derived at the API boundary."""

    scheme: str
    host: str | None
    client_ip: str | None
    secure_transport: bool

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating API security context",
            event="api_security_context_validate_start",
        )
        if self.scheme not in {"http", "https"}:
            raise APIProtocolError(
                "HTTP ASGI scope has an unsupported scheme.",
                component=_COMPONENT,
                operation="validate_security_context",
                field="scheme",
                context={"scheme": self.scheme},
            )
        if self.secure_transport != (self.scheme == "https"):
            raise APIProtocolError(
                "Security context secure_transport is inconsistent with scheme.",
                component=_COMPONENT,
                operation="validate_security_context",
                field="secure_transport",
            )

    def to_dict(self) -> dict[str, Any]:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing API security context",
            event="api_security_context_to_dict_start",
        )
        return {
            "scheme": self.scheme,
            "host": self.host,
            "client_ip": self.client_ip,
            "secure_transport": self.secure_transport,
        }


class Security:
    """Validate transport metadata and apply configured response hardening."""

    def __init__(self, app: ASGIApp, *, policy: SecurityPolicy) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing API security middleware",
            event="api_security_init_start",
        )
        if not callable(app):
            raise APIConfigurationError(
                "Security middleware requires a callable ASGI application.",
                component=_COMPONENT,
                operation="initialize",
                field="app",
                context={"received_type": type(app).__name__},
            )
        if not isinstance(policy, SecurityPolicy):
            raise APIConfigurationError(
                "policy must be a SecurityPolicy.",
                component=_COMPONENT,
                operation="initialize",
                field="policy",
                context={"received_type": type(policy).__name__},
            )
        self.app = app
        self.policy = policy
        logger.info(
            {
                "event": "api_security_initialized",
                "host_allowlist_enabled": bool(policy.allowed_hosts),
                "require_https": policy.require_https,
                "hsts_enabled": policy.hsts_max_age_seconds is not None,
                "csp_configured": policy.content_security_policy is not None,
            }
        )

    def _build_context(self, scope: ASGIScope) -> SecurityContext:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building request security context",
            event="api_security_context_build_start",
        )
        raw_scheme = scope.get("scheme", "http")
        if not isinstance(raw_scheme, str):
            raise APIProtocolError(
                "ASGI HTTP scheme must be text.",
                component=_COMPONENT,
                operation="build_context",
                field="scope.scheme",
                context={"received_type": type(raw_scheme).__name__},
            )
        scheme = raw_scheme.strip().lower()
        host = _request_host(scope)
        context = SecurityContext(
            scheme=scheme,
            host=host,
            client_ip=peer_client_ip(scope),
            secure_transport=scheme == "https",
        )

        if self.policy.require_https and not context.secure_transport:
            raise APIInsecureTransportError(
                "HTTP request was rejected because HTTPS is required.",
                component=_COMPONENT,
                operation="build_context",
                field="scheme",
                context={"scheme": context.scheme},
            )
        if self.policy.allowed_hosts:
            if context.host is None:
                raise APIHostRejectedError(
                    "Request has no Host header while a host allowlist is configured.",
                    component=_COMPONENT,
                    operation="build_context",
                    field="host",
                )
            if context.host not in self.policy.allowed_hosts:
                raise APIHostRejectedError(
                    "Request Host is not present in the configured allowlist.",
                    component=_COMPONENT,
                    operation="build_context",
                    field="host",
                    context={"host": context.host},
                )
        return context

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
            action="Applying API security middleware",
            event="api_security_call_start",
            context={"scope_type": scope.get("type")},
        )
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        context = self._build_context(scope)
        bimap_state(scope)["security"] = context
        configured_headers = self.policy.response_headers(
            secure_transport=context.secure_transport
        )

        async def send_secured(message: ASGIMessage) -> None:
            if message.get("type") == "http.response.start":
                for name, value in configured_headers.items():
                    set_response_header(message, name, value)
            await send(message)

        logger.debug(
            {
                "event": "api_security_request_validated",
                "host": context.host,
                "scheme": context.scheme,
                "secure_transport": context.secure_transport,
            }
        )
        await self.app(scope, receive, send_secured)


SecurityMiddleware = Security


__all__ = [
    "SecurityPolicy",
    "SecurityContext",
    "Security",
    "SecurityMiddleware",
]


if __name__ == "__main__":
    import asyncio

    print("\n=== Running API Security Middleware Self-Test ===\n")
    printer.status("TEST", "API security middleware initialized", "info")

    async def _app(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = Security(
        _app,
        policy=SecurityPolicy(
            allowed_hosts=("api.example.test",),
            require_https=True,
            referrer_policy="no-referrer",
            hsts_max_age_seconds=300,
        ),
    )
    sent: list[ASGIMessage] = []

    async def _receive() -> ASGIMessage:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(message: ASGIMessage) -> None:
        sent.append(dict(message))

    scope: ASGIScope = {
        "type": "http",
        "scheme": "https",
        "headers": [(b"host", b"api.example.test:443")],
        "client": ("127.0.0.1", 50000),
    }
    asyncio.run(middleware(scope, _receive, _send))
    headers = dict(sent[0]["headers"])
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"strict-transport-security"] == b"max-age=300"
    assert scope["state"]["bimap"]["security"].host == "api.example.test"
    printer.status("PASS", "API security request/response hardening", "success")

    print("\n=== Test ran successfully ===\n")