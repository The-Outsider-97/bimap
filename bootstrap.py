"""
BIMAP composition root.

This module is the only BIMAP module permitted to know about the concrete
application graph spanning:

- deterministic Audit Engine product coordinators;
- application services, commands, and queries;
- the concrete SLAI integration adapter;
- reporting builders;
- FastAPI route dependencies; and
- worker job runners.

It deliberately does not implement transport, persistence, payment, malware,
storage, queue, authorization, or SLAI runtime semantics. Those capabilities
are supplied through explicit host-owned adapters and validated here before
composition.

Lifecycle ownership
-------------------
Objects created by :class:`Bootstrap` are owned by the bootstrap runtime.

Objects injected through :class:`BootstrapInfrastructure` remain host-owned,
with one explicit exception:

``close_shared_memory_on_shutdown=True`` grants BIMAP permission to close the
injected SLAI ``SharedMemory`` during shutdown.

Configuration
-------------
No YAML files are parsed in this module. BIMAP's current ``configs/`` files do
not yet define a stable configuration-loading contract. The composition root
therefore consumes already-validated BIMAP configuration/domain objects rather
than inventing an implicit configuration schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from types import MappingProxyType
from typing import Any, cast
from starlette.types import Lifespan
from fastapi import FastAPI

from .api.app import APISettings, create_app
from .api.dependencies import *
from .api.middleware.request_limits import RateLimiter
from .api.routes._shared import RouteAuthorizer
from .app.commands.begin_checkout import BeginCheckout
from .app.commands.cancel_order import CancelOrder
from .app.commands.create_order import CreateOrder
from .app.commands.create_upload_slot import CreateUploadSlot
from .app.commands.enqueue_audit import EnqueueAudit
from .app.commands.handle_payment import HandlePayment
from .app.commands.release_report import ReleaseReport
from .app.commands.request_deletion import RequestDeletion
from .app.commands.validate_uploads import ValidateUploads
from .app.ports.clock import Clock
from .app.ports.malware import Malware
from .app.ports.notifications import Notifications
from .app.ports.payment import Payment
from .app.ports.queue import Queue
from .app.ports.repositories import Repository
from .app.ports.storage import Storage
from .app.queries.get_audit_status import GetAuditStatus
from .app.queries.get_order import GetOrder
from .app.queries.get_products import GetProducts
from .app.queries.list_orders import ListOrders
from .app.queries.list_reports import ListReports
from .app.services.audit_service import AuditService
from .app.services.fulfilment_service import FulfilmentService
from .app.services.order_service import OrderService
from .app.services.review_service import ReviewService
from .app.services.upload_service import UploadService
from .audit_engine.bim_qa.auditor import BIMQAAuditor
from .audit_engine.combined.auditor import CombinedAuditor
from .audit_engine.engine import AuditEngine
from .audit_engine.rfa.auditor import RFAAuditor
from .domain.products.limits import ProductLimits
from .domain.products.models import ProductCatalog
from .reporting.package_builder import PackageBuilder
from .reporting.report_builder import ReportBuilder, ReportRenderer
from .slai.adapter import SLAIAdapter
from .slai.agent_policy import SLAIAgentPolicy
from .slai.governance import SLAIGovernance
from .slai.health import SLAIHealthCheck
from .slai.orchestration import AgentTaskBuilder, SLAIOrchestrator
from .workers.jobs.audit import WorkerAudit
from .workers.jobs.deletion import JobDeletion
from .workers.jobs.report import JobReport
from .workers.jobs.retention import JobRetention
from .workers.runner import Runner

from logs.logger import PrettyPrinter, get_logger  # type: ignore
from src.agents.agent_factory import AgentFactory  # type: ignore
from src.agents.collaborative.shared_memory import SharedMemory  # type: ignore


logger = get_logger("BIMAP Bootstrap")
printer = PrettyPrinter()

_COMPONENT = "bootstrap"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _announce(action: str, *, event: str, context: Mapping[str, Any] | None = None) -> None:
    """
    Emit one bounded method-start diagnostic.

    Customer evidence, report content, access tokens, credentials, and raw
    payloads must never be supplied in ``context``.
    """

    printer.status("BOOTSTRAP", action, "info")

    payload: dict[str, Any] = {
        "event": event,
        "component": _COMPONENT,
        "action": action,
    }

    if context:
        payload["context"] = dict(context)

    logger.debug(payload)


def _safe_context(context: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return a shallow immutable operator-safe diagnostic context."""

    if context is None:
        return MappingProxyType({})

    safe: dict[str, Any] = {}

    for key, value in context.items():
        safe[str(key)] = (
            value
            if value is None or isinstance(value, (bool, int, float, str))
            else f"<{type(value).__name__}>"
        )

    return MappingProxyType(safe)


# ---------------------------------------------------------------------------
# Bootstrap errors
# ---------------------------------------------------------------------------


class BootstrapError(RuntimeError):
    """Base exception for BIMAP composition and lifecycle failures."""

    code = "BIMAP.BOOTSTRAP.ERROR"

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        normalized_operation = str(operation).strip() or "unknown"

        self.message = normalized_message
        self.operation = normalized_operation
        self.field = (
            None
            if field is None
            else str(field).strip() or None
        )
        self.context = _safe_context(context)
        self.cause = cause

        rendered = (
            f"{normalized_message} "
            f"[operation={normalized_operation}"
        )

        if self.field:
            rendered += f", field={self.field}"

        rendered += "]"

        super().__init__(rendered)

    def to_dict(self) -> dict[str, Any]:
        """Return a bounded machine-readable diagnostic representation."""

        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "operation": self.operation,
        }

        if self.field:
            payload["field"] = self.field

        if self.context:
            payload["context"] = dict(self.context)

        if self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__

        return payload


class BootstrapConfigurationError(BootstrapError):
    """Raised when bootstrap inputs are structurally invalid."""

    code = "BIMAP.BOOTSTRAP.CONFIGURATION"


class BootstrapCompositionError(BootstrapError):
    """Raised when the runtime graph cannot be composed."""

    code = "BIMAP.BOOTSTRAP.COMPOSITION"


class BootstrapStateError(BootstrapError):
    """Raised when a lifecycle operation is invalid for the current state."""

    code = "BIMAP.BOOTSTRAP.STATE"


class BootstrapShutdownError(BootstrapError):
    """Raised when BIMAP-owned runtime resources cannot close cleanly."""

    code = "BIMAP.BOOTSTRAP.SHUTDOWN"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class BootstrapState(str, Enum):
    """Lifecycle states of one composition-root instance."""

    NEW = "new"
    BUILT = "built"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Composition inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BootstrapAuditComponents:
    """
    Product-policy-bearing deterministic Audit Engine components.

    Bootstrap intentionally does not create empty rule registries, select
    rule versions, invent finding mappers, or create Combined Audit correlation
    policy.

    Those concerns are already owned by the supplied product auditors.
    """

    rfa: RFAAuditor
    bim_qa: BIMQAAuditor
    combined: CombinedAuditor

    def __post_init__(self) -> None:
        _announce(
            "Validating deterministic audit components",
            event="bootstrap_audit_components_validate_start",
        )

        expected = (
            ("rfa", self.rfa, RFAAuditor),
            ("bim_qa", self.bim_qa, BIMQAAuditor),
            ("combined", self.combined, CombinedAuditor),
        )

        for field, value, expected_type in expected:
            if not isinstance(value, expected_type):
                raise BootstrapConfigurationError(
                    f"{field} must be a {expected_type.__name__}.",
                    operation="validate_audit_components",
                    field=field,
                    context={
                        "received_type": type(value).__name__,
                    },
                )


@dataclass(frozen=True, slots=True)
class BootstrapInfrastructure:
    """
    Host/deployment-owned dependencies consumed by BIMAP.

    These dependencies are deliberately injected rather than constructed here.
    BIMAP therefore remains independent of a specific database, object store,
    payment processor, message broker, malware engine, authentication provider,
    or deployment environment.
    """

    repository: Repository
    payment: Payment
    clock: Clock
    malware: Malware
    storage: Storage
    queue: Queue

    shared_memory: SharedMemory
    route_hooks: APIRouteHooks

    notifications: Notifications | None = None

    agent_factory: AgentFactory | None = None

    rate_limiter: RateLimiter | None = None
    report_renderer: ReportRenderer | None = None

    admin_authorizer: RouteAuthorizer | None = None

    slai_health_check: SLAIHealthCheck | None = None
    slai_governance: SLAIGovernance | None = None
    slai_task_builder: AgentTaskBuilder | None = None

    close_shared_memory_on_shutdown: bool = False

    def __post_init__(self) -> None:
        _announce(
            "Validating host infrastructure dependencies",
            event="bootstrap_infrastructure_validate_start",
        )

        required = (
            ("repository", self.repository, Repository),
            ("payment", self.payment, Payment),
            ("clock", self.clock, Clock),
            ("malware", self.malware, Malware),
            ("storage", self.storage, Storage),
            ("queue", self.queue, Queue),
            ("shared_memory", self.shared_memory, SharedMemory),
            ("route_hooks", self.route_hooks, APIRouteHooks),
        )

        for field, value, expected_type in required:
            if not isinstance(value, expected_type):
                raise BootstrapConfigurationError(
                    f"{field} must be a {expected_type.__name__} instance.",
                    operation="validate_infrastructure",
                    field=field,
                    context={
                        "received_type": type(value).__name__,
                    },
                )

        if (
            self.notifications is not None
            and not isinstance(self.notifications, Notifications)
        ):
            raise BootstrapConfigurationError(
                "notifications must implement the BIMAP Notifications port "
                "or be None.",
                operation="validate_infrastructure",
                field="notifications",
                context={
                    "received_type": type(self.notifications).__name__,
                },
            )

        if (
            self.agent_factory is not None
            and not isinstance(self.agent_factory, AgentFactory)
        ):
            raise BootstrapConfigurationError(
                "agent_factory must be an SLAI AgentFactory or None.",
                operation="validate_infrastructure",
                field="agent_factory",
                context={
                    "received_type": type(self.agent_factory).__name__,
                },
            )

        if (
            self.rate_limiter is not None
            and not callable(getattr(self.rate_limiter, "check", None))
        ):
            raise BootstrapConfigurationError(
                "rate_limiter must provide check(scope) or be None.",
                operation="validate_infrastructure",
                field="rate_limiter",
                context={
                    "received_type": type(self.rate_limiter).__name__,
                },
            )

        if (
            self.report_renderer is not None
            and not callable(getattr(self.report_renderer, "render", None))
        ):
            raise BootstrapConfigurationError(
                "report_renderer must provide render(context=...) or be None.",
                operation="validate_infrastructure",
                field="report_renderer",
                context={
                    "received_type": type(self.report_renderer).__name__,
                },
            )

        if (
            self.admin_authorizer is not None
            and not callable(self.admin_authorizer)
        ):
            raise BootstrapConfigurationError(
                "admin_authorizer must be callable or None.",
                operation="validate_infrastructure",
                field="admin_authorizer",
                context={
                    "received_type": type(self.admin_authorizer).__name__,
                },
            )

        if (
            self.slai_health_check is not None
            and not isinstance(
                self.slai_health_check,
                SLAIHealthCheck,
            )
        ):
            raise BootstrapConfigurationError(
                "slai_health_check must be an SLAIHealthCheck or None.",
                operation="validate_infrastructure",
                field="slai_health_check",
                context={
                    "received_type": type(
                        self.slai_health_check
                    ).__name__,
                },
            )

        if (
            self.slai_governance is not None
            and not isinstance(
                self.slai_governance,
                SLAIGovernance,
            )
        ):
            raise BootstrapConfigurationError(
                "slai_governance must be an SLAIGovernance or None.",
                operation="validate_infrastructure",
                field="slai_governance",
                context={
                    "received_type": type(
                        self.slai_governance
                    ).__name__,
                },
            )

        if (
            self.slai_task_builder is not None
            and not callable(self.slai_task_builder)
        ):
            raise BootstrapConfigurationError(
                "slai_task_builder must be callable or None.",
                operation="validate_infrastructure",
                field="slai_task_builder",
                context={
                    "received_type": type(
                        self.slai_task_builder
                    ).__name__,
                },
            )

        if not isinstance(
            self.close_shared_memory_on_shutdown,
            bool,
        ):
            raise BootstrapConfigurationError(
                "close_shared_memory_on_shutdown must be boolean.",
                operation="validate_infrastructure",
                field="close_shared_memory_on_shutdown",
            )


@dataclass(frozen=True, slots=True)
class BootstrapConfiguration:
    """
    Deployment policy consumed by the composition root.

    Existing BIMAP configuration/domain models are reused instead of creating
    duplicate bootstrap-specific representations of products, limits, API
    policy, or SLAI agent policy.
    """

    catalog: ProductCatalog
    api_settings: APISettings

    product_limits: tuple[ProductLimits, ...] = ()

    slai_profile: Mapping[str, Any] | None = None
    slai_required_agents: tuple[str, ...] | None = None

    allow_degraded_slai_readiness: bool = False
    retain_slai_shared_memory: bool = False
    expose_health_details: bool = False

    def __post_init__(self) -> None:
        _announce(
            "Validating bootstrap configuration",
            event="bootstrap_configuration_validate_start",
        )

        if not isinstance(self.catalog, ProductCatalog):
            raise BootstrapConfigurationError(
                "catalog must be a ProductCatalog.",
                operation="validate_configuration",
                field="catalog",
                context={
                    "received_type": type(self.catalog).__name__,
                },
            )

        if not isinstance(self.api_settings, APISettings):
            raise BootstrapConfigurationError(
                "api_settings must be an APISettings instance.",
                operation="validate_configuration",
                field="api_settings",
                context={
                    "received_type": type(
                        self.api_settings
                    ).__name__,
                },
            )

        if isinstance(
            self.product_limits,
            (str, bytes, bytearray, Mapping),
        ):
            raise BootstrapConfigurationError(
                "product_limits must be an iterable of ProductLimits.",
                operation="validate_configuration",
                field="product_limits",
                context={
                    "received_type": type(
                        self.product_limits
                    ).__name__,
                },
            )

        try:
            limits = tuple(self.product_limits)
        except TypeError as exc:
            raise BootstrapConfigurationError(
                "product_limits must be iterable.",
                operation="validate_configuration",
                field="product_limits",
                context={
                    "received_type": type(
                        self.product_limits
                    ).__name__,
                },
                cause=exc,
            ) from exc

        seen_limit_keys: set[tuple[Any, str | None]] = set()

        for index, configured in enumerate(limits):
            if not isinstance(configured, ProductLimits):
                raise BootstrapConfigurationError(
                    "product_limits may contain ProductLimits values only.",
                    operation="validate_configuration",
                    field=f"product_limits[{index}]",
                    context={
                        "received_type": type(configured).__name__,
                    },
                )

            key = (
                configured.product_code,
                configured.tier_code,
            )

            if key in seen_limit_keys:
                raise BootstrapConfigurationError(
                    "Duplicate ProductLimits configuration for one "
                    "product/tier scope.",
                    operation="validate_configuration",
                    field="product_limits",
                    context={
                        "product_code": configured.product_code.value,
                        "tier_code": configured.tier_code,
                    },
                )

            seen_limit_keys.add(key)

        profile: Mapping[str, Any] | None = None

        if self.slai_profile is not None:
            if not isinstance(self.slai_profile, Mapping):
                raise BootstrapConfigurationError(
                    "slai_profile must be a mapping or None.",
                    operation="validate_configuration",
                    field="slai_profile",
                    context={
                        "received_type": type(
                            self.slai_profile
                        ).__name__,
                    },
                )

            # Defensive top-level snapshot.
            profile = MappingProxyType(
                dict(self.slai_profile)
            )

        required_agents: tuple[str, ...] | None = None

        if self.slai_required_agents is not None:
            if isinstance(
                self.slai_required_agents,
                (str, bytes, bytearray),
            ):
                raise BootstrapConfigurationError(
                    "slai_required_agents must be a sequence of names "
                    "or None.",
                    operation="validate_configuration",
                    field="slai_required_agents",
                )

            try:
                raw_agents = tuple(
                    self.slai_required_agents
                )
            except TypeError as exc:
                raise BootstrapConfigurationError(
                    "slai_required_agents must be iterable.",
                    operation="validate_configuration",
                    field="slai_required_agents",
                    context={
                        "received_type": type(
                            self.slai_required_agents
                        ).__name__,
                    },
                    cause=exc,
                ) from exc

            names: list[str] = []
            seen_agents: set[str] = set()

            for index, raw_name in enumerate(raw_agents):
                if (
                    not isinstance(raw_name, str)
                    or not raw_name.strip()
                ):
                    raise BootstrapConfigurationError(
                        "SLAI required agent names must be "
                        "non-empty strings.",
                        operation="validate_configuration",
                        field=f"slai_required_agents[{index}]",
                        context={
                            "received_type": type(
                                raw_name
                            ).__name__,
                        },
                    )

                name = raw_name.strip().lower()

                if name in seen_agents:
                    raise BootstrapConfigurationError(
                        "slai_required_agents contains a duplicate name.",
                        operation="validate_configuration",
                        field="slai_required_agents",
                        context={
                            "agent": name,
                        },
                    )

                seen_agents.add(name)
                names.append(name)

            if not names:
                raise BootstrapConfigurationError(
                    "slai_required_agents cannot be empty when "
                    "explicitly supplied.",
                    operation="validate_configuration",
                    field="slai_required_agents",
                )

            required_agents = tuple(names)

        for field_name in (
            "allow_degraded_slai_readiness",
            "retain_slai_shared_memory",
            "expose_health_details",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise BootstrapConfigurationError(
                    f"{field_name} must be boolean.",
                    operation="validate_configuration",
                    field=field_name,
                )

        object.__setattr__(self, "product_limits", limits)
        object.__setattr__(self, "slai_profile", profile)
        object.__setattr__(self, "slai_required_agents", required_agents)


# ---------------------------------------------------------------------------
# Composition outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BootstrapServices:
    """Application services created exactly once by Bootstrap."""

    audit: AuditService
    order: OrderService
    upload: UploadService
    fulfilment: FulfilmentService
    review: ReviewService


@dataclass(frozen=True, slots=True)
class BootstrapCommands:
    """Application commands created exactly once by Bootstrap."""

    create_order: CreateOrder
    cancel_order: CancelOrder

    create_upload_slot: CreateUploadSlot
    validate_uploads: ValidateUploads

    begin_checkout: BeginCheckout
    handle_payment: HandlePayment

    enqueue_audit: EnqueueAudit

    release_report: ReleaseReport
    request_deletion: RequestDeletion


@dataclass(frozen=True, slots=True)
class BootstrapQueries:
    """Application queries created exactly once by Bootstrap."""

    get_order: GetOrder
    list_orders: ListOrders

    get_products: GetProducts

    get_audit_status: GetAuditStatus
    list_reports: ListReports


@dataclass(frozen=True, slots=True)
class BootstrapRuntime:
    """Complete runtime graph returned by :meth:`Bootstrap.build`."""

    application: FastAPI
    runner: Runner

    api_dependencies: APIDependencies

    audit_engine: AuditEngine
    slai: SLAIAdapter

    services: BootstrapServices
    commands: BootstrapCommands
    queries: BootstrapQueries


# ---------------------------------------------------------------------------
# Composition root
# ---------------------------------------------------------------------------


class Bootstrap:
    """
    Thread-safe and idempotent BIMAP composition root.

    ``build()`` creates one runtime graph.

    Repeated ``build()`` calls return the same runtime.

    ``close()`` is idempotent and releases only resources owned by the BIMAP
    graph. Injected infrastructure remains host-owned unless ownership has been
    explicitly transferred through a lifecycle option.
    """

    def __init__(
        self,
        *,
        infrastructure: BootstrapInfrastructure,
        configuration: BootstrapConfiguration,
        audit_components: BootstrapAuditComponents,
    ) -> None:
        _announce(
            "Initializing BIMAP composition root",
            event="bootstrap_init_start",
        )

        if not isinstance(
            infrastructure,
            BootstrapInfrastructure,
        ):
            raise BootstrapConfigurationError(
                "infrastructure must be a BootstrapInfrastructure.",
                operation="initialize",
                field="infrastructure",
                context={
                    "received_type": type(
                        infrastructure
                    ).__name__,
                },
            )

        if not isinstance(
            configuration,
            BootstrapConfiguration,
        ):
            raise BootstrapConfigurationError(
                "configuration must be a BootstrapConfiguration.",
                operation="initialize",
                field="configuration",
                context={
                    "received_type": type(
                        configuration
                    ).__name__,
                },
            )

        if not isinstance(
            audit_components,
            BootstrapAuditComponents,
        ):
            raise BootstrapConfigurationError(
                "audit_components must be a BootstrapAuditComponents.",
                operation="initialize",
                field="audit_components",
                context={
                    "received_type": type(
                        audit_components
                    ).__name__,
                },
            )

        self.infrastructure = infrastructure
        self.configuration = configuration
        self.audit_components = audit_components

        self._state = BootstrapState.NEW
        self._runtime: BootstrapRuntime | None = None
        self._lifespan: Lifespan[FastAPI] | None = None
        self._lock = RLock()

        logger.info(
            {
                "event": "bootstrap_initialized",
                "state": self._state.value,
                "admin_routes_configured": (
                    infrastructure.admin_authorizer is not None
                ),
                "custom_rate_limiter": (
                    infrastructure.rate_limiter is not None
                ),
                "pdf_renderer_configured": (
                    infrastructure.report_renderer is not None
                ),
            }
        )

    @property
    def state(self) -> BootstrapState:
        """Return the current bootstrap lifecycle state."""

        return self._state

    @property
    def runtime(self) -> BootstrapRuntime:
        """Return the composed runtime or fail if build has not completed."""

        _announce(
            "Accessing BIMAP runtime",
            event="bootstrap_runtime_access_start",
            context={
                "state": self._state.value,
            },
        )

        if (
            self._state is not BootstrapState.BUILT
            or self._runtime is None
        ):
            raise BootstrapStateError(
                "BIMAP runtime is not available in the current "
                "bootstrap state.",
                operation="get_runtime",
                context={
                    "state": self._state.value,
                },
            )

        return self._runtime

    def build(self, *, lifespan: Lifespan[FastAPI] | None = None) -> BootstrapRuntime:
        """
        Compose the complete BIMAP runtime.

        Composition is deterministic with respect to the supplied dependency
        objects. No infrastructure client or business policy is silently
        discovered from global state by this method.
        """

        _announce(
            "Building BIMAP runtime",
            event="bootstrap_build_start",
            context={
                "state": self._state.value,
            },
        )

        with self._lock:
            if (
                self._state is BootstrapState.BUILT
                and self._runtime is not None
            ):
                if (
                    lifespan is not None
                    and lifespan is not self._lifespan
                ):
                    raise BootstrapStateError(
                        "BIMAP runtime is already built with a different "
                        "ASGI lifespan.",
                        operation="build",
                        field="lifespan",
                        context={
                            "state": self._state.value,
                        },
                    )
            
                logger.debug(
                    {
                        "event": "bootstrap_build_reused",
                    }
                )
            
                return self._runtime

            if self._state is BootstrapState.CLOSED:
                raise BootstrapStateError(
                    "A closed Bootstrap instance cannot be rebuilt.",
                    operation="build",
                    context={
                        "state": self._state.value,
                    },
                )

            stage = "audit_engine"

            orchestrator: SLAIOrchestrator | None = None
            slai_adapter: SLAIAdapter | None = None

            try:
                # ---------------------------------------------------------
                # Deterministic Audit Engine
                # ---------------------------------------------------------

                audit_engine = AuditEngine(
                    rfa_auditor=self.audit_components.rfa,
                    bim_qa_auditor=self.audit_components.bim_qa,
                    combined_auditor=self.audit_components.combined,
                )

                # ---------------------------------------------------------
                # SLAI integration
                # ---------------------------------------------------------

                stage = "slai_policy"

                policy = SLAIAgentPolicy(
                    self.configuration.slai_profile
                )

                stage = "slai_orchestrator"

                orchestrator = SLAIOrchestrator(
                    policy=policy,
                    factory=self.infrastructure.agent_factory,
                    shared_memory=self.infrastructure.shared_memory,
                    health_check=(
                        self.infrastructure.slai_health_check
                    ),
                    governance=(
                        self.infrastructure.slai_governance
                    ),
                    task_builder=(
                        self.infrastructure.slai_task_builder
                    ),
                    allow_degraded_readiness=(
                        self.configuration
                        .allow_degraded_slai_readiness
                    ),
                    retain_shared_memory=(
                        self.configuration
                        .retain_slai_shared_memory
                    ),
                    close_shared_memory=(
                        self.infrastructure
                        .close_shared_memory_on_shutdown
                    ),
                )

                stage = "slai_adapter"

                slai_adapter = SLAIAdapter(
                    orchestrator=orchestrator,
                    # Bootstrap constructed the orchestrator and therefore
                    # owns its lifecycle.
                    close_orchestrator=True,
                )

                # ---------------------------------------------------------
                # Reporting
                # ---------------------------------------------------------

                stage = "reporting"

                report_builder = ReportBuilder(
                    renderer=self.infrastructure.report_renderer,
                )

                package_builder = PackageBuilder()

                # ---------------------------------------------------------
                # Application services
                # ---------------------------------------------------------

                stage = "application_services"

                order_service = OrderService(
                    self.infrastructure.repository,
                    self.infrastructure.payment,
                    self.infrastructure.clock,
                    catalog=self.configuration.catalog,
                    product_limits=(
                        self.configuration.product_limits
                    ),
                )

                upload_service = UploadService(
                    self.infrastructure.repository,
                    self.infrastructure.malware,
                    self.infrastructure.clock,
                    self.infrastructure.storage,
                )

                audit_service = AuditService(
                    audit_engine,
                    cast(Any, slai_adapter),
                    self.infrastructure.repository,
                    queue=self.infrastructure.queue,
                )

                fulfilment_service = FulfilmentService(
                    self.infrastructure.repository,
                    self.infrastructure.storage,
                    self.infrastructure.notifications,
                    self.infrastructure.clock,
                    report_builder=report_builder,
                    package_builder=package_builder,
                )

                review_service = ReviewService(
                    self.infrastructure.repository,
                    self.infrastructure.clock,
                )

                services = BootstrapServices(
                    audit=audit_service,
                    order=order_service,
                    upload=upload_service,
                    fulfilment=fulfilment_service,
                    review=review_service,
                )

                # ---------------------------------------------------------
                # Commands
                # ---------------------------------------------------------

                stage = "application_commands"

                create_order = CreateOrder(
                    order_service
                )

                cancel_order = CancelOrder(
                    order_service
                )

                create_upload_slot = CreateUploadSlot(
                    upload_service
                )

                validate_uploads = ValidateUploads(
                    upload_service
                )

                begin_checkout = BeginCheckout(
                    order_service
                )

                handle_payment = HandlePayment(
                    order_service
                )

                enqueue_audit = EnqueueAudit(
                    order_service,
                    audit_service,
                )

                release_report = ReleaseReport(
                    fulfilment_service
                )

                request_deletion = RequestDeletion(
                    fulfilment_service
                )

                commands = BootstrapCommands(
                    create_order=create_order,
                    cancel_order=cancel_order,
                    create_upload_slot=create_upload_slot,
                    validate_uploads=validate_uploads,
                    begin_checkout=begin_checkout,
                    handle_payment=handle_payment,
                    enqueue_audit=enqueue_audit,
                    release_report=release_report,
                    request_deletion=request_deletion,
                )

                # ---------------------------------------------------------
                # Queries
                # ---------------------------------------------------------

                stage = "application_queries"

                get_order = GetOrder(
                    self.infrastructure.repository
                )

                list_orders = ListOrders(
                    self.infrastructure.repository
                )

                get_products = GetProducts(
                    self.configuration.catalog,
                    product_limits=(
                        self.configuration.product_limits
                    ),
                )

                get_audit_status = GetAuditStatus(
                    self.infrastructure.repository
                )

                list_reports = ListReports(
                    self.infrastructure.repository
                )

                queries = BootstrapQueries(
                    get_order=get_order,
                    list_orders=list_orders,
                    get_products=get_products,
                    get_audit_status=get_audit_status,
                    list_reports=list_reports,
                )

                # ---------------------------------------------------------
                # API
                # ---------------------------------------------------------

                stage = "api_dependencies"

                api_use_cases = APIUseCases(
                    create_order=create_order,
                    cancel_order=cancel_order,
                    get_order=get_order,
                    list_orders=list_orders,
                    get_products=get_products,
                    create_upload_slot=create_upload_slot,
                    validate_uploads=validate_uploads,
                    begin_checkout=begin_checkout,
                    handle_payment=handle_payment,
                    list_reports=list_reports,
                    request_deletion=request_deletion,
                )

                api_health = APIHealthDependencies(
                    cast(Any, slai_adapter),
                    required_agents=(
                        self.configuration
                        .slai_required_agents
                    ),
                    expose_details=(
                        self.configuration
                        .expose_health_details
                    ),
                )

                api_admin = (
                    None
                    if self.infrastructure.admin_authorizer is None
                    else APIAdminDependencies(
                        review_service=review_service,
                        authorizer=(
                            self.infrastructure
                            .admin_authorizer
                        ),
                    )
                )

                api_dependencies = APIDependencies(
                    use_cases=api_use_cases,
                    route_hooks=(
                        self.infrastructure.route_hooks
                    ),
                    health=api_health,
                    admin=api_admin,
                )

                stage = "api_application"

                application = create_app(
                    api_dependencies,
                    settings=self.configuration.api_settings,
                    rate_limiter=(
                        self.infrastructure.rate_limiter
                    ),
                    lifespan=lifespan,
                )

                # ---------------------------------------------------------
                # Workers
                # ---------------------------------------------------------

                stage = "workers"

                worker_audit = WorkerAudit(audit_service)
                worker_report = JobReport(fulfilment_service)
                worker_retention = JobRetention(fulfilment_service)
                worker_deletion = JobDeletion(request_deletion)

                runner = Runner(
                    audit=worker_audit,
                    report=worker_report,
                    retention=worker_retention,
                    deletion=worker_deletion,
                )

                # ---------------------------------------------------------
                # Runtime
                # ---------------------------------------------------------

                runtime = BootstrapRuntime(
                    application=application,
                    runner=runner,
                    api_dependencies=api_dependencies,
                    audit_engine=audit_engine,
                    slai=slai_adapter,
                    services=services,
                    commands=commands,
                    queries=queries,
                )

            except Exception as exc:
                self._cleanup_failed_build(
                    slai_adapter=slai_adapter,
                    orchestrator=orchestrator,
                )

                logger.exception(
                    "BIMAP bootstrap composition failed during "
                    "stage=%s",
                    stage,
                )

                raise BootstrapCompositionError(
                    "BIMAP runtime composition failed.",
                    operation="build",
                    field=stage,
                    context={
                        "cause_type": type(exc).__name__,
                    },
                    cause=exc,
                ) from exc

            self._runtime = runtime
            self._lifespan = lifespan
            self._state = BootstrapState.BUILT

            printer.status(
                "BOOTSTRAP",
                "BIMAP runtime composed successfully",
                "success",
            )

            logger.info(
                {
                    "event": "bootstrap_build_completed",
                    "state": self._state.value,
                    "admin_routes_configured": (
                        api_admin is not None
                    ),
                    "worker_jobs": tuple(
                        item.value
                        for item
                        in runtime.runner.available_jobs
                    ),
                }
            )

            return runtime

    def _cleanup_failed_build(
        self,
        *,
        slai_adapter: SLAIAdapter | None,
        orchestrator: SLAIOrchestrator | None,
    ) -> None:
        """
        Best-effort cleanup for resources created before composition failed.

        Injected infrastructure is not closed here.
        """

        _announce(
            "Cleaning up failed BIMAP composition",
            event="bootstrap_failed_build_cleanup_start",
        )

        try:
            if slai_adapter is not None:
                slai_adapter.close()

            elif orchestrator is not None:
                orchestrator.close()

        except Exception as exc:
            logger.warning(
                "BIMAP failed-build cleanup encountered %s",
                type(exc).__name__,
            )

    def close(self) -> None:
        """
        Idempotently close BIMAP-owned runtime resources.

        Host-injected repositories, payment clients, storage clients, malware
        services, queues, notification clients, and externally supplied
        AgentFactory instances are not closed by Bootstrap.
        """

        _announce(
            "Closing BIMAP runtime",
            event="bootstrap_close_start",
            context={
                "state": self._state.value,
            },
        )

        with self._lock:
            if self._state is BootstrapState.CLOSED:
                return

            runtime = self._runtime

            # State becomes CLOSED even when there was no successful build.
            self._runtime = None
            self._lifespan = None
            self._state = BootstrapState.CLOSED

            if runtime is None:
                logger.info(
                    {
                        "event": "bootstrap_closed",
                        "runtime_was_built": False,
                    }
                )
                return

            try:
                runtime.slai.close()

            except Exception as exc:
                logger.exception(
                    "BIMAP SLAI shutdown failed"
                )

                raise BootstrapShutdownError(
                    "BIMAP runtime did not close cleanly.",
                    operation="close",
                    field="slai",
                    context={
                        "cause_type": type(exc).__name__,
                    },
                    cause=exc,
                ) from exc

            printer.status(
                "BOOTSTRAP",
                "BIMAP runtime closed successfully",
                "success",
            )

            logger.info(
                {
                    "event": "bootstrap_closed",
                    "runtime_was_built": True,
                }
            )

    shutdown = close

    def __enter__(self) -> BootstrapRuntime:
        """Build and return the runtime for context-manager usage."""

        _announce(
            "Entering BIMAP bootstrap context",
            event="bootstrap_enter_start",
        )

        return self.build()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        """Release BIMAP-owned resources when leaving the context."""

        _announce(
            "Exiting BIMAP bootstrap context",
            event="bootstrap_exit_start",
            context={
                "exception_present": exc is not None,
            },
        )

        self.close()


__all__ = [
    "BootstrapError",
    "BootstrapConfigurationError",
    "BootstrapCompositionError",
    "BootstrapStateError",
    "BootstrapShutdownError",
    "BootstrapState",
    "BootstrapAuditComponents",
    "BootstrapInfrastructure",
    "BootstrapConfiguration",
    "BootstrapServices",
    "BootstrapCommands",
    "BootstrapQueries",
    "BootstrapRuntime",
    "Bootstrap",
]



if __name__ == "__main__":
    print("\n=== Running BIMAP Bootstrap ===\n")
    printer.status("Init", "BIMAP Bootstrap initialized", "success")

    print("\n=== Successfully ran the BIMAP Bootstrap ===\n")