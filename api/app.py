"""
FastAPI application factory for the R3D BIM Audit Platform (BIMAP).

``create_app`` is the HTTP composition boundary immediately below
``bootstrap.py``.  It receives an already-built :class:`APIDependencies`
container, constructs the current route groups, mounts them under one validated
API prefix, and installs BIMAP's cross-cutting middleware.

The module deliberately does not import ``bootstrap.py`` and does not construct
repositories, payment/storage/queue adapters, SLAI agents, product catalogs, or
application services.  Those are composition-root responsibilities.

Middleware execution order (outer -> inner)
-------------------------------------------

    ErrorMapping
        -> CorrelationMiddleware
        -> Security
        -> RequestLimits
        -> FastAPI / routes

This ordering gives the error boundary visibility over middleware and route
failures while making request/correlation state available to downstream
security, limit, and application errors whenever correlation initialization has
succeeded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .dependencies import APIDependencies, install_api_dependencies
from .middleware.correlation import CorrelationMiddleware
from .middleware.error_mapping import ErrorMapping
from .middleware.request_limits import RateLimiter, RequestLimitPolicy, RequestLimits
from .middleware.security import Security, SecurityPolicy
from .routes.admin import RouteAdmin
from .routes.checkout import RouteCheckout
from .routes.deletion import RouteDeletion
from .routes.downloads import RouteDownloads
from .routes.health import RouteHealth
from .routes.orders import RouteOrders
from .routes.products import RouteProducts
from .routes.reports import RouteReports
from .routes.uploads import RouteUploads
from .routes.webhooks import RouteWebhooks
from .utils.api_errors import *
from .utils.api_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Application")
printer = PrettyPrinter()

_COMPONENT = "api_app"
_DEFAULT_API_PREFIX = "/api/v1"
_DEFAULT_TITLE = "R3D BIM Audit Platform API"


def _normalize_api_prefix(value: str) -> str:
    """Validate one path prefix used to mount every BIMAP public route group."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating BIMAP API prefix",
        event="api_app_prefix_validate_start",
    )
    prefix = require_api_text(
        value,
        field="api_prefix",
        error_type=APIConfigurationError,
        component=_COMPONENT,
        operation="validate_api_prefix",
        max_length=256,
    )
    if not prefix.startswith("/"):
        raise APIConfigurationError(
            "api_prefix must start with '/'.",
            component=_COMPONENT,
            operation="validate_api_prefix",
            field="api_prefix",
        )
    if prefix == "/":
        raise APIConfigurationError(
            "api_prefix must identify a non-root API namespace.",
            component=_COMPONENT,
            operation="validate_api_prefix",
            field="api_prefix",
        )
    if prefix.endswith("/"):
        prefix = prefix.rstrip("/")
    segments = prefix.split("/")[1:]
    if not segments or any(not segment for segment in segments):
        raise APIConfigurationError(
            "api_prefix contains an empty path segment.",
            component=_COMPONENT,
            operation="validate_api_prefix",
            field="api_prefix",
        )
    if any(segment in {".", ".."} for segment in segments):
        raise APIConfigurationError(
            "api_prefix contains a relative path segment.",
            component=_COMPONENT,
            operation="validate_api_prefix",
            field="api_prefix",
        )
    if any(
        any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in segment)
        or "?" in segment
        or "#" in segment
        or "\\" in segment
        for segment in segments
    ):
        raise APIConfigurationError(
            "api_prefix contains whitespace, control, or unsupported URL/path characters.",
            component=_COMPONENT,
            operation="validate_api_prefix",
            field="api_prefix",
        )
    return prefix


def _normalize_optional_path(value: str | None, *, field: str) -> str | None:
    """Validate an optional FastAPI metadata endpoint path."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating optional API path: {field}",
        event="api_app_optional_path_validate_start",
        context={"field": field, "configured": value is not None},
    )
    if value is None:
        return None
    path = require_api_text(
        value,
        field=field,
        error_type=APIConfigurationError,
        component=_COMPONENT,
        operation="validate_optional_path",
        max_length=256,
    )
    if not path.startswith("/") or path == "/" or path.endswith("/"):
        raise APIConfigurationError(
            f"{field} must be an absolute non-root path without a trailing '/'.",
            component=_COMPONENT,
            operation="validate_optional_path",
            field=field,
        )
    if (
        "?" in path
        or "#" in path
        or "\\" in path
        or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path)
    ):
        raise APIConfigurationError(
            f"{field} contains whitespace, control, or unsupported URL/path characters.",
            component=_COMPONENT,
            operation="validate_optional_path",
            field=field,
        )
    return path


@dataclass(frozen=True, slots=True)
class APISettings:
    """Explicit HTTP composition settings for one BIMAP API deployment.

    Request/body/header thresholds and HTTP security policy are injected as
    their existing middleware policy objects; no commercial/product or
    deployment-specific numeric threshold is duplicated here.

    OpenAPI and interactive documentation are disabled by default.  A deployment
    may explicitly expose them by configuring ``openapi_url`` and optionally
    ``docs_url``/``redoc_url``.
    """

    request_limits: RequestLimitPolicy
    security: SecurityPolicy
    api_prefix: str = _DEFAULT_API_PREFIX
    title: str = _DEFAULT_TITLE
    correlation_header: str = "x-correlation-id"
    request_id_header: str = "x-request-id"
    max_correlation_id_length: int = 128
    reject_invalid_correlation: bool = True
    openapi_url: str | None = None
    docs_url: str | None = None
    redoc_url: str | None = None

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating BIMAP API application settings",
            event="api_app_settings_validate_start",
        )
        if not isinstance(self.request_limits, RequestLimitPolicy):
            raise APIConfigurationError(
                "request_limits must be a RequestLimitPolicy.",
                component=_COMPONENT,
                operation="validate_settings",
                field="request_limits",
                context={"received_type": type(self.request_limits).__name__},
            )
        if not isinstance(self.security, SecurityPolicy):
            raise APIConfigurationError(
                "security must be a SecurityPolicy.",
                component=_COMPONENT,
                operation="validate_settings",
                field="security",
                context={"received_type": type(self.security).__name__},
            )

        object.__setattr__(self, "api_prefix", _normalize_api_prefix(self.api_prefix))
        object.__setattr__(
            self,
            "title",
            require_api_text(
                self.title,
                field="title",
                error_type=APIConfigurationError,
                component=_COMPONENT,
                operation="validate_settings",
                max_length=256,
            ),
        )

        object.__setattr__(
            self,
            "correlation_header",
            require_header_name(
                self.correlation_header,
                field="correlation_header",
            ),
        )
        object.__setattr__(
            self,
            "request_id_header",
            require_header_name(
                self.request_id_header,
                field="request_id_header",
            ),
        )
        if self.correlation_header == self.request_id_header:
            raise APIConfigurationError(
                "correlation_header and request_id_header must be different.",
                component=_COMPONENT,
                operation="validate_settings",
                field="request_id_header",
            )
        object.__setattr__(
            self,
            "max_correlation_id_length",
            require_positive_int(
                self.max_correlation_id_length,
                field="max_correlation_id_length",
                error_type=APIConfigurationError,
                component=_COMPONENT,
                operation="validate_settings",
            ),
        )
        if self.max_correlation_id_length > 128:
            raise APIConfigurationError(
                "max_correlation_id_length cannot exceed the canonical 128-character correlation-context bound.",
                component=_COMPONENT,
                operation="validate_settings",
                field="max_correlation_id_length",
                context={"maximum": 128},
            )
        if not isinstance(self.reject_invalid_correlation, bool):
            raise APIConfigurationError(
                "reject_invalid_correlation must be boolean.",
                component=_COMPONENT,
                operation="validate_settings",
                field="reject_invalid_correlation",
            )
        object.__setattr__(self, "openapi_url", _normalize_optional_path(self.openapi_url, field="openapi_url"))
        object.__setattr__(self, "docs_url", _normalize_optional_path(self.docs_url, field="docs_url"))
        object.__setattr__(self, "redoc_url", _normalize_optional_path(self.redoc_url, field="redoc_url"))
        if self.openapi_url is None and (self.docs_url is not None or self.redoc_url is not None):
            raise APIConfigurationError(
                "docs_url/redoc_url require openapi_url to be enabled.",
                component=_COMPONENT,
                operation="validate_settings",
                field="openapi_url",
            )

        configured_paths = [
            path
            for path in (self.openapi_url, self.docs_url, self.redoc_url)
            if path is not None
        ]
        if len(set(configured_paths)) != len(configured_paths):
            raise APIConfigurationError(
                "OpenAPI/docs endpoint paths must be distinct.",
                component=_COMPONENT,
                operation="validate_settings",
                field="openapi_url",
            )

        logger.info(
            {
                "event": "api_app_settings_validated",
                "api_prefix": self.api_prefix,
                "openapi_enabled": self.openapi_url is not None,
                "docs_enabled": self.docs_url is not None,
                "redoc_enabled": self.redoc_url is not None,
            }
        )


def _validate_rate_limiter(rate_limiter: RateLimiter | None) -> RateLimiter | None:
    """Validate the optional deployment-owned rate limiter before app startup."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating API rate-limiter dependency",
        event="api_app_rate_limiter_validate_start",
        context={"configured": rate_limiter is not None},
    )
    if rate_limiter is not None and not callable(getattr(rate_limiter, "check", None)):
        raise APIConfigurationError(
            "rate_limiter must provide check(scope) or be None.",
            component=_COMPONENT,
            operation="validate_rate_limiter",
            field="rate_limiter",
            context={"received_type": type(rate_limiter).__name__},
        )
    return rate_limiter


def _framework_http_error(error: StarletteHTTPException) -> Response:
    """Translate framework-owned routing HTTP errors without exposing details.

    BIMAP route/business failures use ``APIError`` and are handled by
    ``ErrorMapping``.  Starlette still owns routing outcomes such as 405; this
    handler returns only the status and validated protocol headers for those
    framework-generated outcomes.  Common statuses with an existing BIMAP API
    error type are re-raised so the normal problem-response boundary remains
    authoritative.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Translating FastAPI routing HTTP error",
        event="api_app_framework_http_error_start",
        context={"status_code": error.status_code},
    )

    error_map: dict[int, type[Exception]] = {
        400: APIValidationError,
        401: APIUnauthorizedError,
        403: APIForbiddenError,
        404: APINotFoundError,
        409: APIConflictError,
        413: APIRequestTooLargeError,
        415: APIUnsupportedMediaTypeError,
        422: APIUnprocessableError,
        429: APIRateLimitError,
        431: APIRequestHeadersTooLargeError,
        500: APIInternalError,
        503: APIServiceUnavailableError,
        504: APIGatewayTimeoutError,
    }
    safe_protocol_headers: dict[str, str] = {}
    if isinstance(error.headers, Mapping):
        allowed_protocol_headers = {"allow", "www-authenticate", "retry-after"}
        for raw_name, raw_value in error.headers.items():
            header_name = str(raw_name).strip().lower()
            if header_name in allowed_protocol_headers:
                safe_protocol_headers[header_name] = require_header_value(
                    raw_value,
                    field=header_name,
                    max_length=2048,
                )

    error_type = error_map.get(error.status_code)
    if error_type is not None:
        raise error_type(
            "FastAPI/Starlette generated an HTTP routing error.",
        ) from error

    response_headers: dict[str, str] = {"Cache-Control": "no-store"}
    response_headers.update(safe_protocol_headers)
    return Response(status_code=error.status_code, headers=response_headers)


async def _handle_framework_http_exception(request: Request, error: Exception) -> Response:
    """FastAPI exception-handler wrapper for routing-generated HTTP errors."""
    framework_error = cast(StarletteHTTPException, error)
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Handling FastAPI routing exception",
        event="api_app_framework_http_handler_start",
        context={"status_code": framework_error.status_code},
    )
    del request
    return _framework_http_error(framework_error)


async def _handle_request_validation_exception(request: Request, error: Exception) -> Response:
    """Route FastAPI/Pydantic validation failures through BIMAP's safe boundary."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Handling FastAPI request-validation exception",
        event="api_app_framework_validation_handler_start",
    )
    del request
    if isinstance(error, RequestValidationError):
        try:
            error_count = len(error.errors())
        except Exception:
            error_count = None
    else:
        error_count = None
    raise APIUnprocessableError(
        "FastAPI request parameter validation failed.",
        component=_COMPONENT,
        operation="framework_request_validation",
        context={"error_count": error_count},
        cause=error,
    ) from error


def _construct_route_groups(dependencies: APIDependencies) -> tuple[Any, ...]:
    """Construct current route groups from the already-built dependency container."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Constructing BIMAP API route groups",
        event="api_app_routes_construct_start",
        context={"admin_configured": dependencies.admin is not None},
    )
    use_cases = dependencies.use_cases
    hooks = dependencies.route_hooks
    health = dependencies.health

    route_groups: list[Any] = [
        RouteHealth(
            health.health_check,
            factory=health.factory,
            shared_memory=health.shared_memory,
            required_agents=health.required_agents,
            agents=health.agents,
            expose_details=health.expose_details,
        ),
        RouteProducts(use_cases.get_products),
        RouteOrders(
            use_cases.create_order,
            use_cases.cancel_order,
            use_cases.get_order,
            use_cases.list_orders,
            authorizer=hooks.authorizer,
        ),
        RouteUploads(
            use_cases.create_upload_slot,
            use_cases.validate_uploads,
            authorizer=hooks.authorizer,
            manifest_validator=hooks.upload_manifest_validator,
        ),
        RouteCheckout(
            use_cases.begin_checkout,
            authorizer=hooks.authorizer,
        ),
        RouteReports(
            use_cases.list_reports,
            report_id_resolver=hooks.report_id_resolver,
            authorizer=hooks.authorizer,
        ),
        RouteDownloads(
            use_cases.list_reports,
            report_id_resolver=hooks.report_id_resolver,
            download_url_issuer=hooks.download_url_issuer,
            authorizer=hooks.authorizer,
        ),
        RouteDeletion(
            use_cases.request_deletion,
            authorizer=hooks.authorizer,
            deletion_admission_gate=hooks.deletion_admission_gate,
            deletion_object_resolver=hooks.deletion_object_resolver,
        ),
        RouteWebhooks(
            use_cases.handle_payment,
            signature_header=hooks.payment_signature_header,
        ),
    ]

    if dependencies.admin is not None:
        route_groups.append(
            RouteAdmin(
                dependencies.admin.review_service,
                use_cases.get_order,
                use_cases.list_orders,
                use_cases.list_reports,
                authorizer=dependencies.admin.authorizer,
            )
        )

    for index, group in enumerate(route_groups):
        router = getattr(group, "router", None)
        if router is None or not hasattr(router, "routes"):
            raise APIConfigurationError(
                "API route group did not expose a FastAPI-compatible router.",
                component=_COMPONENT,
                operation="construct_route_groups",
                field=f"route_groups[{index}].router",
                context={"group_type": type(group).__name__},
            )

    logger.info(
        {
            "event": "api_app_routes_constructed",
            "route_group_count": len(route_groups),
            "admin_routes_enabled": dependencies.admin is not None,
        }
    )
    return tuple(route_groups)


def _install_middleware(application: FastAPI, settings: APISettings, *, rate_limiter: RateLimiter | None) -> None:
    """Install BIMAP middleware in the order required by Starlette stacking."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Installing BIMAP API middleware",
        event="api_app_middleware_install_start",
    )

    # Starlette's ``add_middleware`` inserts each declaration at the front of
    # the user-middleware list.  Add inner middleware first so runtime order is:
    # ErrorMapping -> Correlation -> Security -> RequestLimits -> FastAPI.
    application.add_middleware(
        RequestLimits,
        policy=settings.request_limits,
        rate_limiter=rate_limiter,
    )
    application.add_middleware(Security, policy=settings.security)
    application.add_middleware(
        CorrelationMiddleware,
        correlation_header=settings.correlation_header,
        request_header=settings.request_id_header,
        max_id_length=settings.max_correlation_id_length,
        reject_invalid_inbound=settings.reject_invalid_correlation,
    )
    application.add_middleware(ErrorMapping)

    logger.info(
        {
            "event": "api_app_middleware_installed",
            "middleware_order": (
                "ErrorMapping",
                "CorrelationMiddleware",
                "Security",
                "RequestLimits",
            ),
        }
    )


def create_app(
    dependencies: APIDependencies,
    *,
    settings: APISettings,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Create the fully wired FastAPI application for one BIMAP deployment.

    Parameters
    ----------
    dependencies:
        Already-constructed application handlers, trusted route hooks, and SLAI
        health dependencies supplied by ``bootstrap.py``.
    settings:
        Explicit API prefix, middleware security/limit policy, correlation, and
        optional API documentation exposure settings.
    rate_limiter:
        Optional infrastructure-owned asynchronous rate-limit implementation.
        ``None`` means no rate-limit adapter is installed; request body/header
        limits still apply according to ``settings.request_limits``.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Creating BIMAP FastAPI application",
        event="api_app_create_start",
    )
    if not isinstance(dependencies, APIDependencies):
        raise APIConfigurationError(
            "dependencies must be an APIDependencies instance.",
            component=_COMPONENT,
            operation="create_app",
            field="dependencies",
            context={"received_type": type(dependencies).__name__},
        )
    if not isinstance(settings, APISettings):
        raise APIConfigurationError(
            "settings must be an APISettings instance.",
            component=_COMPONENT,
            operation="create_app",
            field="settings",
            context={"received_type": type(settings).__name__},
        )
    rate_limiter = _validate_rate_limiter(rate_limiter)

    application = FastAPI(
        title=settings.title,
        openapi_url=settings.openapi_url,
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
    )
    install_api_dependencies(application, dependencies)

    # Convert framework-owned validation/routing exceptions before they become
    # FastAPI's default detail-bearing JSON responses.  Re-raised API errors
    # are caught by the outer ErrorMapping middleware.
    application.add_exception_handler(RequestValidationError, _handle_request_validation_exception)
    application.add_exception_handler(StarletteHTTPException, _handle_framework_http_exception)

    route_groups = _construct_route_groups(dependencies)
    for group in route_groups:
        application.include_router(group.router, prefix=settings.api_prefix)

    # Retain route objects for diagnostics/introspection without making them a
    # second dependency source for request handlers.
    application.state.route_groups = route_groups
    application.state.api_settings = settings

    _install_middleware(application, settings, rate_limiter=rate_limiter)

    total_routes = sum(len(group.router.routes) for group in route_groups)
    logger.info(
        {
            "event": "api_app_created",
            "api_prefix": settings.api_prefix,
            "route_group_count": len(route_groups),
            "registered_bimap_route_count": total_routes,
            "admin_routes_enabled": dependencies.admin is not None,
            "rate_limiter_configured": rate_limiter is not None,
        }
    )
    printer.status("API", f"BIMAP API initialized with {total_routes} registered routes", "success")
    return application


__all__ = ["APISettings", "create_app"]