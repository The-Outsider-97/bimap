"""
Side-effect-free health and readiness assessment for BIMAP's SLAI boundary.

``SLAIHealthCheck`` deliberately does not construct agents, import the BIMAP
orchestrator, or own runtime lifecycle.  It inspects only injected runtime
objects and a small configurable set of importable SLAI modules.  This keeps
health probing independent from ``slai/orchestration.py`` and
``slai/adapter.py`` and prevents the health endpoint from accidentally creating
or mutating the very runtime it is supposed to observe.

The module distinguishes two operational questions:

``liveness``
    Can the SLAI integration surface required by BIMAP be discovered and
    inspected without an integration failure?

``readiness``
    Are the injected SLAI runtime components and the required BIMAP agent set
    available to accept governed audit work now?

Health is intentionally operational, not a BIM-quality judgement.  It does not
reinterpret SLAI Quality/Evaluation scores as customer-facing BIM compliance.

Dependency direction
--------------------
    slai/utils/*
        -> slai/health.py

``health.py`` MUST NOT import:
    - slai/orchestration.py
    - slai/adapter.py
    - slai/agent_policy.py
    - concrete SLAI agents
    - BIMAP API/application services

Runtime objects are supplied by bootstrap/orchestration through dependency
injection.  Public probe methods never expose raw runtime payloads in their
returned report; only bounded operational metadata is retained.
"""

from __future__ import annotations

import importlib.util

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("SLAI Health Check")
printer = PrettyPrinter()


class HealthMode(str, Enum):
    """Supported operational probe modes."""

    LIVENESS = "liveness"
    READINESS = "readiness"


class HealthState(str, Enum):
    """Neutral BIMAP health vocabulary for SLAI integration components."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"

    @property
    def is_operational(self) -> bool:
        """Return whether the component is presently usable at some level."""

        return self in {HealthState.HEALTHY, HealthState.DEGRADED}


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    """Bounded operational result for one SLAI-related component."""

    name: str
    component_type: str
    state: HealthState
    required: bool = True
    probe: str | None = None
    detail: str | None = None
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Validating SLAI component health result",
            context={"component": str(self.name)},
        )

        name = require_text(
            self.name,
            field="name",
            error_type=SLAIRuntimeContractError,
        )
        component_type = require_text(
            self.component_type,
            field="component_type",
            error_type=SLAIRuntimeContractError,
        )
        state = (
            self.state
            if isinstance(self.state, HealthState)
            else parse_enum(
                HealthState,
                self.state,
                field="state",
                error_type=SLAIRuntimeContractError,
            )
        )
        required = require_bool(
            self.required,
            field="required",
            error_type=SLAIRuntimeContractError,
        )
        probe = None if self.probe is None else require_text(
            self.probe,
            field="probe",
            error_type=SLAIRuntimeContractError,
        )
        detail = None if self.detail is None else require_text(
            self.detail,
            field="detail",
            error_type=SLAIRuntimeContractError,
        )

        if not isinstance(self.diagnostics, Mapping):
            raise SLAIRuntimeContractError(
                "Component health diagnostics must be a mapping.",
                component="health",
                operation="validate_component",
                field="diagnostics",
                context={"received_type": type(self.diagnostics).__name__},
            )
        diagnostics = MappingProxyType(safe_log_context(self.diagnostics))

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "component_type", component_type)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "probe", probe)
        object.__setattr__(self, "detail", detail)
        object.__setattr__(self, "diagnostics", diagnostics)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe, content-free health representation."""

        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Serializing SLAI component health result",
            context={"component": self.name},
        )
        return {
            "name": self.name,
            "component_type": self.component_type,
            "state": self.state.value,
            "required": self.required,
            "probe": self.probe,
            "detail": self.detail,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class SLAIHealthReport:
    """Immutable aggregate health report for one liveness/readiness probe."""

    mode: HealthMode
    overall_state: HealthState
    components: tuple[ComponentHealth, ...]
    checked_at: datetime | str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Validating SLAI health report",
        )

        mode = (
            self.mode
            if isinstance(self.mode, HealthMode)
            else parse_enum(
                HealthMode,
                self.mode,
                field="mode",
                error_type=SLAIRuntimeContractError,
            )
        )
        overall_state = (
            self.overall_state
            if isinstance(self.overall_state, HealthState)
            else parse_enum(
                HealthState,
                self.overall_state,
                field="overall_state",
                error_type=SLAIRuntimeContractError,
            )
        )
        components = tuple(self.components)
        if any(not isinstance(item, ComponentHealth) for item in components):
            raise SLAIRuntimeContractError(
                "SLAI health report components must be ComponentHealth instances.",
                component="health",
                operation="validate_report",
                field="components",
            )

        checked_at = ensure_utc_datetime(
            self.checked_at,
            field="checked_at",
            error_type=SLAIRuntimeContractError,
        )

        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "overall_state", overall_state)
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "checked_at", checked_at)

    @property
    def ready(self) -> bool:
        """Return whether the report satisfies strict readiness."""

        return self.mode is HealthMode.READINESS and self.overall_state is HealthState.HEALTHY

    @property
    def live(self) -> bool:
        """Return whether the integration is alive enough to answer probes."""

        return self.overall_state is not HealthState.UNAVAILABLE

    def component(self, name: str) -> ComponentHealth | None:
        """Return one component result by exact normalized name."""

        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Looking up SLAI health component",
            context={"component": str(name)},
        )
        key = require_text(name, field="name", error_type=SLAIRuntimeContractError)
        for item in self.components:
            if item.name == key:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-safe health report."""

        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Serializing SLAI health report",
        )
        checked_at = self.checked_at
        if not isinstance(checked_at, datetime):
            checked_at = ensure_utc_datetime(
                checked_at,
                field="checked_at",
                error_type=SLAIRuntimeContractError,
            )

        return {
            "mode": self.mode.value,
            "overall_state": self.overall_state.value,
            "ready": self.ready,
            "live": self.live,
            "checked_at": format_utc_datetime(checked_at),
            "components": [item.to_dict() for item in self.components],
        }


class SLAIHealthCheck:
    """
    Side-effect-free SLAI integration health checker.

    ``required_modules`` defaults only to stable runtime infrastructure used by
    the current BIMAP/SLAI integration.  Agent availability is checked through
    injected objects rather than by importing/constructing concrete agents.
    """

    DEFAULT_REQUIRED_MODULES: tuple[str, ...] = (
        "logs.logger",
        "src.agents.agent_factory",
        "src.agents.collaborative.shared_memory",
    )

    __slots__ = ("_required_modules",)

    def __init__(self, required_modules: Sequence[str] | None = None) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Initializing SLAI health checker",
        )

        raw_modules = (
            self.DEFAULT_REQUIRED_MODULES if required_modules is None else required_modules
        )
        if isinstance(raw_modules, (str, bytes, bytearray)):
            raise SLAIRuntimeContractError(
                "required_modules must be a sequence of module names, not one string.",
                component="health",
                operation="initialize",
                field="required_modules",
            )

        normalized: list[str] = []
        seen: set[str] = set()
        for raw_name in raw_modules:
            name = require_text(
                raw_name,
                field="required_modules[]",
                error_type=SLAIRuntimeContractError,
            )
            if name not in seen:
                seen.add(name)
                normalized.append(name)

        if not normalized:
            raise SLAIRuntimeContractError(
                "At least one SLAI runtime module must be configured for health probing.",
                component="health",
                operation="initialize",
                field="required_modules",
            )

        self._required_modules = tuple(normalized)
        logger.info(
            "SLAI health checker initialized: required_modules=%d",
            len(self._required_modules),
        )

    @property
    def required_modules(self) -> tuple[str, ...]:
        """Return configured module-probe targets."""

        return self._required_modules

    def check_liveness(self) -> SLAIHealthReport:
        """Probe import-level SLAI integration liveness without runtime mutation."""

        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Checking SLAI integration liveness",
        )
        components = tuple(
            self._probe_import(module_name, required=True)
            for module_name in self._required_modules
        )
        return self._build_report(HealthMode.LIVENESS, components)

    def check_readiness(
        self,
        *,
        factory: Any,
        shared_memory: Any,
        required_agents: Sequence[str],
        agents: Mapping[str, Any] | None = None,
    ) -> SLAIHealthReport:
        """
        Probe whether injected SLAI runtime components can accept BIMAP work.

        Parameters
        ----------
        factory:
            Initialized SLAI ``AgentFactory``-compatible object.
        shared_memory:
            Initialized SLAI ``SharedMemory``-compatible object.
        agents:
            Mapping of normalized agent names to already-created agent objects.
            Optional/extra agents may be included and are reported as optional.
        required_agents:
            Exact agent names that must be available for this readiness check.
            The caller should normally pass ``SLAIAgentPolicy.required_agents()``
            or the current envelope's required subset.  No product-specific
            agent list is duplicated in this module.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Checking SLAI runtime readiness",
        )

        required_agent_names = normalize_agent_sequence(
            required_agents,
            field="required_agents",
            error_type=SLAIRuntimeContractError,
        )
        if not required_agent_names:
            raise SLAIRuntimeContractError(
                "Readiness requires an explicit non-empty required-agent set.",
                component="health",
                operation="check_readiness",
                field="required_agents",
            )

        if agents is None:
            agent_mapping: Mapping[str, Any] = {}
        elif not isinstance(agents, Mapping):
            raise SLAIRuntimeContractError(
                "agents must be a mapping of agent name to runtime object.",
                component="health",
                operation="check_readiness",
                field="agents",
                context={"received_type": type(agents).__name__},
            )
        else:
            normalized_agents: dict[str, Any] = {}
            for raw_name, instance in agents.items():
                name = normalize_agent_name(
                    raw_name,
                    field="agents.key",
                    error_type=SLAIRuntimeContractError,
                )
                if name in normalized_agents and normalized_agents[name] is not instance:
                    raise SLAIRuntimeContractError(
                        "Duplicate normalized SLAI agent names were provided.",
                        component="health",
                        operation="check_readiness",
                        field="agents",
                        context={"agent": name},
                    )
                normalized_agents[name] = instance
            agent_mapping = normalized_agents

        components: list[ComponentHealth] = [
            self._probe_import(module_name, required=True)
            for module_name in self._required_modules
        ]
        components.append(
            self._probe_runtime_object(
                "agent_factory",
                factory,
                component_type="runtime",
                required=True,
            )
        )
        components.append(
            self._probe_runtime_object(
                "shared_memory",
                shared_memory,
                component_type="runtime",
                required=True,
            )
        )

        required_set = set(required_agent_names)
        names_to_probe = list(required_agent_names)
        for name in sorted(agent_mapping):
            if name not in required_set:
                names_to_probe.append(name)

        for name in names_to_probe:
            components.append(
                self._probe_runtime_object(
                    name,
                    agent_mapping.get(name),
                    component_type="agent",
                    required=name in required_set,
                )
            )

        return self._build_report(HealthMode.READINESS, tuple(components))

    def assert_ready(
        self,
        report: SLAIHealthReport,
        *,
        allow_degraded: bool = False,
    ) -> None:
        """Raise a structured integration error when readiness is insufficient."""

        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Asserting SLAI readiness",
        )

        if not isinstance(report, SLAIHealthReport):
            raise SLAIRuntimeContractError(
                "report must be an SLAIHealthReport instance.",
                component="health",
                operation="assert_ready",
                field="report",
                context={"received_type": type(report).__name__},
            )
        if report.mode is not HealthMode.READINESS:
            raise SLAIRuntimeContractError(
                "assert_ready() requires a readiness report.",
                component="health",
                operation="assert_ready",
                field="report.mode",
                context={"mode": report.mode.value},
            )
        allow_degraded = require_bool(
            allow_degraded,
            field="allow_degraded",
            error_type=SLAIRuntimeContractError,
        )

        if report.overall_state is HealthState.HEALTHY:
            return
        if allow_degraded and report.overall_state is HealthState.DEGRADED:
            logger.warning("Proceeding with explicitly permitted degraded SLAI readiness")
            return

        failed_required = [
            component
            for component in report.components
            if component.required and component.state is not HealthState.HEALTHY
        ]
        failed_agents = [
            component.name
            for component in failed_required
            if component.component_type == "agent"
        ]

        if failed_agents:
            raise SLAIAgentHealthError(
                "One or more required SLAI agents are not ready for BIMAP work.",
                component="health",
                operation="assert_ready",
                context={
                    "overall_state": report.overall_state.value,
                    "agents": failed_agents,
                },
            )

        raise SLAIRuntimeUnavailableError(
            "The required SLAI runtime is not ready for BIMAP work.",
            component="health",
            operation="assert_ready",
            context={
                "overall_state": report.overall_state.value,
                "components": [item.name for item in failed_required],
            },
        )

    def _probe_import(self, module_name: str, *, required: bool) -> ComponentHealth:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Probing SLAI module availability",
            context={"module": module_name},
        )

        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, AttributeError, ValueError) as exc:
            logger.warning(
                "SLAI module probe failed: module=%s error_type=%s",
                module_name,
                type(exc).__name__,
            )
            return ComponentHealth(
                name=module_name,
                component_type="module",
                state=HealthState.UNAVAILABLE,
                required=required,
                probe="find_spec",
                detail="module_probe_failed",
                diagnostics={"error_type": type(exc).__name__},
            )

        if spec is None:
            return ComponentHealth(
                name=module_name,
                component_type="module",
                state=HealthState.UNAVAILABLE,
                required=required,
                probe="find_spec",
                detail="module_not_found",
            )

        return ComponentHealth(
            name=module_name,
            component_type="module",
            state=HealthState.HEALTHY,
            required=required,
            probe="find_spec",
            detail="module_available",
        )

    def _probe_runtime_object(
        self,
        name: str,
        runtime_object: Any,
        *,
        component_type: str,
        required: bool,
    ) -> ComponentHealth:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Probing injected SLAI runtime object",
            context={"component": name, "component_type": component_type},
        )

        if runtime_object is None:
            return ComponentHealth(
                name=name,
                component_type=component_type,
                state=HealthState.UNAVAILABLE if required else HealthState.UNKNOWN,
                required=required,
                probe="injected_object",
                detail="runtime_object_missing",
            )

        for method_name in ("health_check", "runtime_status"):
            method = getattr(runtime_object, method_name, None)
            if not callable(method):
                continue
            try:
                payload = method()
            except Exception as exc:  # Runtime telemetry is an external boundary.
                logger.warning(
                    "SLAI health probe raised: component=%s probe=%s error_type=%s",
                    name,
                    method_name,
                    type(exc).__name__,
                )
                return ComponentHealth(
                    name=name,
                    component_type=component_type,
                    state=HealthState.DEGRADED,
                    required=required,
                    probe=method_name,
                    detail="health_probe_exception",
                    diagnostics={"error_type": type(exc).__name__},
                )

            token = extract_health_token(payload)
            state = self._state_from_token(token)
            return ComponentHealth(
                name=name,
                component_type=component_type,
                state=state,
                required=required,
                probe=method_name,
                detail=f"reported_{token}",
                diagnostics={"runtime_type": type(runtime_object).__name__},
            )

        # A runtime object can exist before every implementation exposes a
        # health method.  It is not silently called "healthy"; unknown state is
        # explicit and therefore degrades strict readiness.
        return ComponentHealth(
            name=name,
            component_type=component_type,
            state=HealthState.UNKNOWN,
            required=required,
            probe="interface",
            detail="no_health_surface",
            diagnostics={"runtime_type": type(runtime_object).__name__},
        )

    @staticmethod
    def _state_from_token(token: str) -> HealthState:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Normalizing SLAI health token",
            context={"token": token},
        )
        if token == "healthy":
            return HealthState.HEALTHY
        if token == "degraded":
            return HealthState.DEGRADED
        if token == "unavailable":
            return HealthState.UNAVAILABLE
        return HealthState.UNKNOWN

    @staticmethod
    def _aggregate_state(components: Sequence[ComponentHealth]) -> HealthState:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Aggregating SLAI component health",
        )

        required_components = [item for item in components if item.required]
        if any(item.state is HealthState.UNAVAILABLE for item in required_components):
            return HealthState.UNAVAILABLE
        if any(
            item.state in {HealthState.DEGRADED, HealthState.UNKNOWN}
            for item in required_components
        ):
            return HealthState.DEGRADED
        if any(item.state is not HealthState.HEALTHY for item in components):
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    @classmethod
    def _build_report(
        cls,
        mode: HealthMode,
        components: Sequence[ComponentHealth],
    ) -> SLAIHealthReport:
        announce_method_start(
            printer,
            logger,
            "SLAI HEALTH",
            "Building SLAI health report",
            context={"mode": mode.value},
        )
        component_tuple = tuple(components)
        overall = cls._aggregate_state(component_tuple)
        report = SLAIHealthReport(
            mode=mode,
            overall_state=overall,
            components=component_tuple,
        )
        logger.info(
            "SLAI %s probe completed: state=%s components=%d",
            mode.value,
            overall.value,
            len(component_tuple),
        )
        return report


__all__ = [
    "HealthMode",
    "HealthState",
    "ComponentHealth",
    "SLAIHealthReport",
    "SLAIHealthCheck",
]