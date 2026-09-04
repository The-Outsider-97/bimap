"""
Dependency container and request-scoped dependency access for the BIMAP API.

The API layer is an outer composition/admission boundary.  It may consume
already-constructed application handlers and trusted HTTP hooks, but it must not
construct repositories, storage/payment/queue clients, malware scanners, SLAI
agents, or other concrete infrastructure.

The canonical runtime flow is therefore::

    bootstrap.py
        -> construct concrete adapters and application handlers
        -> APIDependencies(...)
        -> api.app.create_app(dependencies, ...)
        -> FastAPI.state.container

    request
        -> get_api_dependencies(request)
        -> request.app.state.container

Current route groups are constructor-injected by ``api.app.create_app``.  The
request accessor remains the canonical seam for future request-scoped FastAPI
``Depends`` functions and extensions that need the already-built container.

Dependency direction
--------------------
``api/dependencies.py`` may import application handlers, the SLAI health value,
and API utilities.  It must not import ``bootstrap.py``, concrete adapters, API
route implementations at runtime, workers, or provider SDKs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request # type: ignore

from .utils.api_errors import APIConfigurationError
from .utils.api_helpers import *
from ..app.commands.begin_checkout import BeginCheckout
from ..app.commands.cancel_order import CancelOrder
from ..app.commands.create_order import CreateOrder
from ..app.commands.create_upload_slot import CreateUploadSlot
from ..app.commands.handle_payment import HandlePayment
from ..app.commands.request_deletion import RequestDeletion
from ..app.commands.validate_uploads import ValidateUploads
from ..app.queries.get_order import GetOrder
from ..app.queries.get_products import GetProducts
from ..app.queries.list_orders import ListOrders
from ..app.queries.list_reports import ListReports
from ..app.services.review_service import ReviewService
from ..app.ports.slai import SLAIPort
from logs.logger import PrettyPrinter, get_logger  # type: ignore

if TYPE_CHECKING:
    from .routes._shared import OrderReportIdResolver, RouteAuthorizer
    from .routes.deletion import DeletionAdmissionGate, DeletionObjectResolver
    from .routes.downloads import DownloadURLIssuer
    from .routes.uploads import UploadManifestValidator


logger = get_logger("BIMAP API Dependencies")
printer = PrettyPrinter()

_COMPONENT = "api_dependencies"
_CONTAINER_STATE_ATTRIBUTE = "container"


def _require_handler(value: Any, expected: type[Any], *, field: str) -> Any:
    """Validate one already-constructed API-facing application handler."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating API dependency: {field}",
        event="api_dependency_handler_validate_start",
        context={"field": field, "expected_type": expected.__name__},
    )
    if not isinstance(value, expected):
        raise APIConfigurationError(
            f"{field} must be a {expected.__name__} instance.",
            component=_COMPONENT,
            operation="validate_handler",
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


def _require_hook(value: Any, *, field: str) -> Any:
    """Validate one trusted deployment hook without assigning its semantics."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating API route hook: {field}",
        event="api_dependency_hook_validate_start",
        context={"field": field},
    )
    if not callable(value):
        raise APIConfigurationError(
            f"{field} must be callable.",
            component=_COMPONENT,
            operation="validate_route_hook",
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


@dataclass(frozen=True, slots=True)
class APIUseCases:
    """Application handlers currently consumed by registered BIMAP API routes.

    The container intentionally includes only use cases that the current HTTP
    route surface actually invokes.  Audit enqueueing, report release, and
    other worker/composition operations are not injected here merely because
    they exist elsewhere in ``bimap.app``.
    """

    create_order: CreateOrder
    cancel_order: CancelOrder
    get_order: GetOrder
    list_orders: ListOrders
    get_products: GetProducts
    create_upload_slot: CreateUploadSlot
    validate_uploads: ValidateUploads
    begin_checkout: BeginCheckout
    handle_payment: HandlePayment
    list_reports: ListReports
    request_deletion: RequestDeletion

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating API application-use-case dependencies",
            event="api_use_cases_validate_start",
        )
        dependencies = (
            ("create_order", self.create_order, CreateOrder),
            ("cancel_order", self.cancel_order, CancelOrder),
            ("get_order", self.get_order, GetOrder),
            ("list_orders", self.list_orders, ListOrders),
            ("get_products", self.get_products, GetProducts),
            ("create_upload_slot", self.create_upload_slot, CreateUploadSlot),
            ("validate_uploads", self.validate_uploads, ValidateUploads),
            ("begin_checkout", self.begin_checkout, BeginCheckout),
            ("handle_payment", self.handle_payment, HandlePayment),
            ("list_reports", self.list_reports, ListReports),
            ("request_deletion", self.request_deletion, RequestDeletion),
        )
        for field, value, expected in dependencies:
            _require_handler(value, expected, field=field)

        logger.info(
            {
                "event": "api_use_cases_validated",
                "handler_count": len(dependencies),
            }
        )


@dataclass(frozen=True, slots=True)
class APIRouteHooks:
    """Trusted HTTP/deployment hooks required by the current route surface.

    These hooks deliberately remain interfaces/callables.  Authentication and
    tenant authorization, staged-upload completeness, report ownership lookup,
    signed-download issuance, deletion admission/object resolution, and payment
    provider signature-header naming all depend on deployment/application
    capabilities that BIMAP must not fabricate inside the API package.
    """

    authorizer: "RouteAuthorizer"
    upload_manifest_validator: "UploadManifestValidator"
    report_id_resolver: "OrderReportIdResolver"
    download_url_issuer: "DownloadURLIssuer"
    deletion_admission_gate: "DeletionAdmissionGate"
    deletion_object_resolver: "DeletionObjectResolver"
    payment_signature_header: str

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating trusted API route hooks",
            event="api_route_hooks_validate_start",
        )
        hooks = (
            ("authorizer", self.authorizer),
            ("upload_manifest_validator", self.upload_manifest_validator),
            ("report_id_resolver", self.report_id_resolver),
            ("download_url_issuer", self.download_url_issuer),
            ("deletion_admission_gate", self.deletion_admission_gate),
            ("deletion_object_resolver", self.deletion_object_resolver),
        )
        for field, value in hooks:
            _require_hook(value, field=field)

        object.__setattr__(
            self,
            "payment_signature_header",
            require_header_name(
                self.payment_signature_header,
                field="payment_signature_header",
            ),
        )
        logger.info(
            {
                "event": "api_route_hooks_validated",
                "hook_count": len(hooks),
                "payment_signature_header": self.payment_signature_header,
            }
        )


@dataclass(frozen=True, slots=True)
class APIHealthDependencies:
    """
    Application-facing SLAI health dependencies for the API layer.

    The API depends only on BIMAP's SLAI application port.  AgentFactory,
    SharedMemory, concrete SLAI health objects, and individual agents remain
    behind the concrete integration adapter.
    """

    slai: SLAIPort
    required_agents: Sequence[str] | None = None
    expose_details: bool = False

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating API health dependencies",
            event="api_health_dependencies_validate_start",
        )

        if not isinstance(self.slai, SLAIPort):
            raise APIConfigurationError(
                "slai must implement the BIMAP SLAI application port.",
                component=_COMPONENT,
                operation="validate_health_dependencies",
                field="slai",
                context={"received_type": type(self.slai).__name__},
            )

        normalized_required: tuple[str, ...] | None = None

        if self.required_agents is not None:
            if isinstance(
                self.required_agents,
                (str, bytes, bytearray),
            ):
                raise APIConfigurationError(
                    "required_agents must be a sequence of agent names or None.",
                    component=_COMPONENT,
                    operation="validate_health_dependencies",
                    field="required_agents",
                    context={
                        "received_type": type(self.required_agents).__name__,
                    },
                )

            try:
                raw_required = tuple(self.required_agents)
            except TypeError as exc:
                raise APIConfigurationError(
                    "required_agents must be iterable.",
                    component=_COMPONENT,
                    operation="validate_health_dependencies",
                    field="required_agents",
                    context={
                        "received_type": type(self.required_agents).__name__,
                    },
                    cause=exc,
                ) from exc

            names: list[str] = []
            seen: set[str] = set()

            for index, raw_name in enumerate(raw_required):
                name = require_api_text(
                    raw_name,
                    field=f"required_agents[{index}]",
                    error_type=APIConfigurationError,
                    component=_COMPONENT,
                    operation="validate_health_dependencies",
                    max_length=256,
                )

                if name in seen:
                    raise APIConfigurationError(
                        "required_agents contains a duplicate agent name.",
                        component=_COMPONENT,
                        operation="validate_health_dependencies",
                        field="required_agents",
                        context={"agent": name},
                    )

                seen.add(name)
                names.append(name)

            if not names:
                raise APIConfigurationError(
                    "required_agents cannot be empty when explicitly supplied.",
                    component=_COMPONENT,
                    operation="validate_health_dependencies",
                    field="required_agents",
                )

            normalized_required = tuple(names)

        if not isinstance(self.expose_details, bool):
            raise APIConfigurationError(
                "expose_details must be boolean.",
                component=_COMPONENT,
                operation="validate_health_dependencies",
                field="expose_details",
                context={
                    "received_type": type(self.expose_details).__name__,
                },
            )

        object.__setattr__(
            self,
            "required_agents",
            normalized_required,
        )

        logger.info(
            {
                "event": "api_health_dependencies_validated",
                "required_agent_count": (
                    None
                    if normalized_required is None
                    else len(normalized_required)
                ),
                "uses_policy_default_agents": normalized_required is None,
                "expose_details": self.expose_details,
            }
        )


@dataclass(frozen=True, slots=True)
class APIAdminDependencies:
    """Optional internal-admin route dependencies.

    Supplying this bundle is the explicit opt-in for mounting ``RouteAdmin``.
    Its authorization hook is separate from the customer-route authorizer so an
    internal deployment can apply a stricter administrative access policy.
    """

    review_service: ReviewService
    authorizer: "RouteAuthorizer"

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating optional admin API dependencies",
            event="api_admin_dependencies_validate_start",
        )
        _require_handler(
            self.review_service,
            ReviewService,
            field="review_service",
        )
        _require_hook(self.authorizer, field="admin_authorizer")
        logger.info({"event": "api_admin_dependencies_validated"})


@dataclass(frozen=True, slots=True)
class APIDependencies:
    """Complete already-constructed dependency set consumed by ``create_app``."""

    use_cases: APIUseCases
    route_hooks: APIRouteHooks
    health: APIHealthDependencies
    admin: APIAdminDependencies | None = None

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating BIMAP API dependency container",
            event="api_dependencies_validate_start",
        )
        expected = (
            ("use_cases", self.use_cases, APIUseCases),
            ("route_hooks", self.route_hooks, APIRouteHooks),
            ("health", self.health, APIHealthDependencies),
        )
        for field, value, expected_type in expected:
            if not isinstance(value, expected_type):
                raise APIConfigurationError(
                    f"{field} must be an {expected_type.__name__} instance.",
                    component=_COMPONENT,
                    operation="validate_dependencies",
                    field=field,
                    context={"received_type": type(value).__name__},
                )
        if self.admin is not None and not isinstance(self.admin, APIAdminDependencies):
            raise APIConfigurationError(
                "admin must be an APIAdminDependencies instance or None.",
                component=_COMPONENT,
                operation="validate_dependencies",
                field="admin",
                context={"received_type": type(self.admin).__name__},
            )
        logger.info(
            {
                "event": "api_dependencies_validated",
                "admin_routes_configured": self.admin is not None,
            }
        )


def install_api_dependencies(application: FastAPI, dependencies: APIDependencies) -> None:
    """Install one immutable API dependency container on FastAPI application state.

    Installation is idempotent for the exact same container instance.  Replacing
    a previously installed container is rejected so a running application cannot
    silently switch repositories, authorization hooks, payment handlers, or SLAI
    runtime dependencies after route construction.
    """
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Installing BIMAP API dependencies",
        event="api_dependencies_install_start",
    )
    if not isinstance(application, FastAPI):
        raise APIConfigurationError(
            "application must be a FastAPI instance.",
            component=_COMPONENT,
            operation="install_dependencies",
            field="application",
            context={"received_type": type(application).__name__},
        )
    if not isinstance(dependencies, APIDependencies):
        raise APIConfigurationError(
            "dependencies must be an APIDependencies instance.",
            component=_COMPONENT,
            operation="install_dependencies",
            field="dependencies",
            context={"received_type": type(dependencies).__name__},
        )

    state = application.state
    if hasattr(state, _CONTAINER_STATE_ATTRIBUTE):
        existing = getattr(state, _CONTAINER_STATE_ATTRIBUTE)
        if existing is dependencies:
            logger.debug({"event": "api_dependencies_install_idempotent"})
            return
        raise APIConfigurationError(
            "FastAPI application already has a different BIMAP dependency container.",
            component=_COMPONENT,
            operation="install_dependencies",
            field="application.state.container",
            context={"existing_type": type(existing).__name__},
        )

    setattr(state, _CONTAINER_STATE_ATTRIBUTE, dependencies)
    logger.info({"event": "api_dependencies_installed"})


def get_api_dependencies(request: Request) -> APIDependencies:
    """Return the request's already-constructed BIMAP API dependency container."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Resolving BIMAP API request dependencies",
        event="api_dependencies_resolve_start",
    )
    if not isinstance(request, Request):
        raise APIConfigurationError(
            "request must be a FastAPI Request instance.",
            component=_COMPONENT,
            operation="get_dependencies",
            field="request",
            context={"received_type": type(request).__name__},
        )

    state = request.app.state
    if not hasattr(state, _CONTAINER_STATE_ATTRIBUTE):
        raise APIConfigurationError(
            "BIMAP API dependency container is not installed on application state.",
            component=_COMPONENT,
            operation="get_dependencies",
            field="request.app.state.container",
        )

    dependencies = getattr(state, _CONTAINER_STATE_ATTRIBUTE)
    if not isinstance(dependencies, APIDependencies):
        raise APIConfigurationError(
            "Installed BIMAP API dependency container has an invalid type.",
            component=_COMPONENT,
            operation="get_dependencies",
            field="request.app.state.container",
            context={"received_type": type(dependencies).__name__},
        )
    return dependencies


__all__ = [
    "APIUseCases",
    "APIRouteHooks",
    "APIHealthDependencies",
    "APIAdminDependencies",
    "APIDependencies",
    "install_api_dependencies",
    "get_api_dependencies",
]
