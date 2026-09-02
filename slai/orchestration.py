"""
BIMAP orchestration bridge into the SLAI runtime.

The orchestrator is the only module in ``bimap/slai`` that constructs and
invokes SLAI agents.  It consumes a validated ``SLAIJobEnvelope`` and an
``SLAIAgentPolicy``, creates only the agents authorized for that envelope,
performs runtime readiness checks, coordinates shared-memory handoff, applies
BIMAP's ingress/analysis/egress phase ordering, and returns a structured
``SLAIOrchestrationResult``.

The module deliberately does not:

- parse raw customer files;
- execute deterministic BIM/RFA rules;
- mutate canonical BIMAP findings;
- decide customer-facing governance outcomes;
- render reports;
- own queue retry/exactly-once semantics.

Those responsibilities remain in the audit engine, governance/result mapping,
reporting, and application/worker layers respectively.

Dependency direction
--------------------

``utils -> agent_policy/job_envelope/health/governance -> orchestration``

``orchestration.py`` must not import ``result_mapper.py`` or ``adapter.py``.
That one-way dependency is a deliberate circular-import boundary.
"""

from __future__ import annotations

import datetime

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from time import perf_counter
from types import MappingProxyType
from typing import Any

from .utils.slai_errors import *
from .utils.slai_helpers import *
from .agent_policy import SLAIAgentPolicy
from .governance import *
from .health import *
from .job_envelope import SLAIJobEnvelope
from src.agents.agent_factory import AgentFactory # type: ignore
from src.agents.collaborative.shared_memory import SharedMemory # type: ignore
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("SLAI Orchestrator")
printer = PrettyPrinter()


class OrchestrationPhase(str, Enum):
    """Stable BIMAP phase names used for SLAI invocation telemetry."""

    INGRESS_QUALITY = "ingress_quality"
    INGRESS_PRIVACY = "ingress_privacy"
    ANALYSIS = "analysis"
    EGRESS_QUALITY = "egress_quality"
    EGRESS_EVALUATION = "egress_evaluation"
    EGRESS_SAFETY = "egress_safety"
    EGRESS_PRIVACY = "egress_privacy"
    OBSERVABILITY = "observability"


@dataclass(frozen=True, slots=True)
class AgentInvocationRecord:
    """One auditable SLAI agent invocation performed for a BIMAP job."""

    agent: str
    phase: OrchestrationPhase
    started_at: datetime.datetime
    completed_at: datetime.datetime
    duration_ms: float
    succeeded: bool
    output: Any = None
    error: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Validating SLAI agent invocation record",
            context={"agent": str(self.agent), "phase": str(self.phase)},
        )

        agent = normalize_agent_name(
            self.agent,
            field="agent",
            error_type=SLAIRuntimeContractError,
        )
        if isinstance(self.phase, OrchestrationPhase):
            phase = self.phase
        else:
            phase_text = require_text(
                self.phase,
                field="phase",
                error_type=SLAIRuntimeContractError,
            )
            try:
                phase = OrchestrationPhase(phase_text)
            except ValueError as exc:
                raise SLAIRuntimeContractError(
                    "Unsupported BIMAP SLAI orchestration phase.",
                    component="orchestration",
                    operation="validate_invocation_record",
                    field="phase",
                    context={
                        "received": phase_text,
                        "allowed": [item.value for item in OrchestrationPhase],
                    },
                    cause=exc,
                ) from exc

        started = ensure_utc_datetime(
            self.started_at,
            field="started_at",
            error_type=SLAIRuntimeContractError,
        )
        completed = ensure_utc_datetime(
            self.completed_at,
            field="completed_at",
            error_type=SLAIRuntimeContractError,
        )
        if completed < started:
            raise SLAIRuntimeContractError(
                "Invocation completion cannot predate invocation start.",
                component="orchestration",
                operation="validate_invocation_record",
                field="completed_at",
                context={"agent": agent, "phase": phase.value},
            )
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, (int, float)):
            raise SLAIRuntimeContractError(
                "duration_ms must be numeric.",
                component="orchestration",
                operation="validate_invocation_record",
                field="duration_ms",
            )
        if float(self.duration_ms) < 0.0:
            raise SLAIRuntimeContractError(
                "duration_ms must be non-negative.",
                component="orchestration",
                operation="validate_invocation_record",
                field="duration_ms",
            )
        succeeded = require_bool(
            self.succeeded,
            field="succeeded",
            error_type=SLAIRuntimeContractError,
        )
        if succeeded and self.error:
            raise SLAIRuntimeContractError(
                "A successful invocation cannot also carry an error record.",
                component="orchestration",
                operation="validate_invocation_record",
                field="error",
                context={"agent": agent, "phase": phase.value},
            )

        error: Mapping[str, Any] | None = None
        if self.error is not None:
            error = MappingProxyType(dict(require_mapping(
                self.error,
                field="error",
                error_type=SLAIRuntimeContractError,
            )))

        object.__setattr__(self, "agent", agent)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "completed_at", completed)
        object.__setattr__(self, "duration_ms", float(self.duration_ms))
        object.__setattr__(self, "succeeded", succeeded)
        object.__setattr__(self, "error", error)

    def to_dict(self, *, include_output: bool = False) -> dict[str, Any]:
        """Return invocation metadata; raw output is excluded by default."""

        phase_value = self.phase.value if isinstance(self.phase, OrchestrationPhase) else str(self.phase)
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Serializing SLAI invocation record",
            context={"agent": self.agent, "phase": phase_value},
        )
        payload: dict[str, Any] = {
            "agent": self.agent,
            "phase": phase_value,
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "duration_ms": self.duration_ms,
            "succeeded": self.succeeded,
            "output_type": type(self.output).__name__ if self.output is not None else None,
            "error": None if self.error is None else dict(self.error),
        }
        if include_output:
            payload["output"] = self.output
        return payload


@dataclass(frozen=True, slots=True)
class SLAIOrchestrationResult:
    """Complete runtime result prior to BIMAP result/governance mapping."""

    job_id: str
    order_id: str
    correlation_id: str
    requested_agents: tuple[str, ...]
    started_at: datetime.datetime
    completed_at: datetime.datetime
    invocations: tuple[AgentInvocationRecord, ...]
    outputs: Mapping[str, Any]
    phase_outputs: Mapping[str, Any]
    gate_outputs: Mapping[str, Mapping[str, Any]]
    health_report: SLAIHealthReport
    terminated_early: bool = False
    termination_reason: str | None = None
    privacy_sanitized_payload: Any = None

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Validating SLAI orchestration result",
            context={"job_id": str(self.job_id)},
        )

        job_id = require_text(
            self.job_id,
            field="job_id",
            error_type=SLAIRuntimeContractError,
        )
        order_id = require_text(
            self.order_id,
            field="order_id",
            error_type=SLAIRuntimeContractError,
        )
        correlation_id = require_text(
            self.correlation_id,
            field="correlation_id",
            error_type=SLAIRuntimeContractError,
        )
        agents = normalize_agent_sequence(
            self.requested_agents,
            field="requested_agents",
            error_type=SLAIRuntimeContractError,
        )
        if not agents:
            raise SLAIRuntimeContractError(
                "Orchestration result must identify at least one requested agent.",
                component="orchestration",
                operation="validate_result",
                field="requested_agents",
            )
        started_at = ensure_utc_datetime(
            self.started_at,
            field="started_at",
            error_type=SLAIRuntimeContractError,
        )
        completed_at = ensure_utc_datetime(
            self.completed_at,
            field="completed_at",
            error_type=SLAIRuntimeContractError,
        )
        if completed_at < started_at:
            raise SLAIRuntimeContractError(
                "Orchestration completion cannot predate start.",
                component="orchestration",
                operation="validate_result",
                field="completed_at",
            )
        invocations = tuple(self.invocations)
        if any(not isinstance(item, AgentInvocationRecord) for item in invocations):
            raise SLAIRuntimeContractError(
                "invocations must contain AgentInvocationRecord instances only.",
                component="orchestration",
                operation="validate_result",
                field="invocations",
            )
        if not isinstance(self.health_report, SLAIHealthReport):
            raise SLAIRuntimeContractError(
                "health_report must be an SLAIHealthReport instance.",
                component="orchestration",
                operation="validate_result",
                field="health_report",
                context={"received_type": type(self.health_report).__name__},
            )

        outputs = MappingProxyType(dict(require_mapping(
            self.outputs,
            field="outputs",
            error_type=SLAIRuntimeContractError,
        )))
        phase_outputs = MappingProxyType(dict(require_mapping(
            self.phase_outputs,
            field="phase_outputs",
            error_type=SLAIRuntimeContractError,
        )))

        raw_gates = require_mapping(
            self.gate_outputs,
            field="gate_outputs",
            error_type=SLAIRuntimeContractError,
        )
        gates: dict[str, Mapping[str, Any]] = {}
        for gate_name, gate_payload in raw_gates.items():
            name = require_text(
                gate_name,
                field="gate_outputs.key",
                error_type=SLAIRuntimeContractError,
            )
            gates[name] = MappingProxyType(dict(require_mapping(
                gate_payload,
                field=f"gate_outputs.{name}",
                error_type=SLAIRuntimeContractError,
            )))

        terminated_early = require_bool(
            self.terminated_early,
            field="terminated_early",
            error_type=SLAIRuntimeContractError,
        )
        termination_reason = None
        if self.termination_reason is not None:
            termination_reason = require_text(
                self.termination_reason,
                field="termination_reason",
                error_type=SLAIRuntimeContractError,
            )
        if terminated_early and termination_reason is None:
            raise SLAIRuntimeContractError(
                "Early termination requires an explicit termination_reason.",
                component="orchestration",
                operation="validate_result",
                field="termination_reason",
            )

        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "requested_agents", agents)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "completed_at", completed_at)
        object.__setattr__(self, "invocations", invocations)
        object.__setattr__(self, "outputs", outputs)
        object.__setattr__(self, "phase_outputs", phase_outputs)
        object.__setattr__(self, "gate_outputs", MappingProxyType(gates))
        object.__setattr__(self, "terminated_early", terminated_early)
        object.__setattr__(self, "termination_reason", termination_reason)

    @property
    def duration_ms(self) -> float:
        """Wall-clock orchestration duration in milliseconds."""

        return max(0.0, (self.completed_at - self.started_at).total_seconds() * 1000.0)

    def to_dict(self, *, include_outputs: bool = False) -> dict[str, Any]:
        """Return a logging-safe orchestration summary by default."""

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Serializing SLAI orchestration result",
            context={"job_id": self.job_id},
        )
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "order_id": self.order_id,
            "correlation_id": self.correlation_id,
            "requested_agents": list(self.requested_agents),
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "duration_ms": self.duration_ms,
            "invocations": [item.to_dict(include_output=False) for item in self.invocations],
            "output_agents": sorted(self.outputs),
            "phase_output_keys": sorted(self.phase_outputs),
            "gate_outputs": sorted(self.gate_outputs),
            "health": self.health_report.to_dict(),
            "terminated_early": self.terminated_early,
            "termination_reason": self.termination_reason,
            "privacy_sanitized_payload_present": self.privacy_sanitized_payload is not None,
        }
        if include_outputs:
            payload["outputs"] = dict(self.outputs)
            payload["phase_outputs"] = dict(self.phase_outputs)
            payload["gate_payloads"] = {
                key: dict(value) for key, value in self.gate_outputs.items()
            }
            payload["privacy_sanitized_payload"] = self.privacy_sanitized_payload
        return payload


# A custom task builder translates BIMAP's grounded orchestration state into
# the *native* task contract of a specific SLAI agent. BIMAP deliberately does
# not invent those payload schemas here: QualityAgent, PrivacyAgent and
# EvaluationAgent already expose different native call contracts in SLAI v2.3.
AgentTaskBuilder = Callable[
    [str, OrchestrationPhase, SLAIJobEnvelope, Mapping[str, Any], Mapping[str, Any], str],
    Any,
]


class SLAIOrchestrator:
    """Policy-governed runtime bridge from BIMAP into SLAI v2.3 agents."""

    _ANALYSIS_ORDER: tuple[str, ...] = (
        "collaborative",
        "reader",
        "perception",
        "knowledge",
        "reasoning",
        "planning",
        "language",
        "execution",
    )

    def __init__(
        self,
        *,
        policy: SLAIAgentPolicy | None = None,
        factory: Any | None = None,
        shared_memory: Any | None = None,
        health_check: SLAIHealthCheck | None = None,
        governance: SLAIGovernance | None = None,
        task_builder: AgentTaskBuilder | None = None,
        allow_degraded_readiness: bool = False,
        retain_shared_memory: bool = False,
    ) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Initializing BIMAP SLAI orchestrator",
        )

        self.policy = policy if policy is not None else SLAIAgentPolicy()
        if not isinstance(self.policy, SLAIAgentPolicy):
            raise SLAIRuntimeContractError(
                "policy must be an SLAIAgentPolicy instance.",
                component="orchestration",
                operation="initialize",
                field="policy",
                context={"received_type": type(self.policy).__name__},
            )

        self._owns_factory = factory is None
        self._owns_shared_memory = shared_memory is None
        self.factory = factory if factory is not None else AgentFactory()
        self.shared_memory = shared_memory if shared_memory is not None else SharedMemory()
        self.health = health_check if health_check is not None else SLAIHealthCheck()
        self.governance = governance if governance is not None else SLAIGovernance()

        if not isinstance(self.health, SLAIHealthCheck):
            raise SLAIRuntimeContractError(
                "health_check must be an SLAIHealthCheck instance.",
                component="orchestration",
                operation="initialize",
                field="health_check",
            )
        if not isinstance(self.governance, SLAIGovernance):
            raise SLAIRuntimeContractError(
                "governance must be an SLAIGovernance instance.",
                component="orchestration",
                operation="initialize",
                field="governance",
            )
        if task_builder is not None and not callable(task_builder):
            raise SLAIRuntimeContractError(
                "task_builder must be callable or None.",
                component="orchestration",
                operation="initialize",
                field="task_builder",
            )

        self.task_builder = task_builder
        self.allow_degraded_readiness = require_bool(
            allow_degraded_readiness,
            field="allow_degraded_readiness",
            error_type=SLAIRuntimeContractError,
        )
        self.retain_shared_memory = require_bool(
            retain_shared_memory,
            field="retain_shared_memory",
            error_type=SLAIRuntimeContractError,
        )
        self._agents: dict[str, Any] = {}
        self._closed = False
        self._lock = RLock()

        logger.info(
            "BIMAP SLAI orchestrator initialized: owns_factory=%s owns_shared_memory=%s",
            self._owns_factory,
            self._owns_shared_memory,
        )

    @property
    def agents(self) -> Mapping[str, Any]:
        """Read-only view of SLAI agent instances prepared by this orchestrator."""

        return MappingProxyType(dict(self._agents))

    def prepare_agents(self, agent_names: Sequence[str]) -> Mapping[str, Any]:
        """Create/cache the exact policy-approved SLAI agents required by a job."""

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Preparing policy-approved SLAI agents",
        )
        self._ensure_open()
        names = normalize_agent_sequence(
            agent_names,
            field="agent_names",
            error_type=SLAIRuntimeContractError,
        )
        if not names:
            raise SLAIRuntimeContractError(
                "prepare_agents() requires a non-empty agent sequence.",
                component="orchestration",
                operation="prepare_agents",
                field="agent_names",
            )

        for name in names:
            self.policy.require_allowed(name)
            if name in self._agents:
                continue
            try:
                agent = self.factory.get_agent(name, shared_memory=self.shared_memory)
            except SLAIIntegrationError:
                raise
            except Exception as exc:
                raise SLAIOrchestrationError(
                    "SLAI AgentFactory failed to prepare a requested BIMAP agent.",
                    component="orchestration",
                    operation="prepare_agents",
                    context={"agent": name},
                    cause=exc,
                ) from exc

            if agent is None:
                raise SLAIRuntimeContractError(
                    "SLAI AgentFactory returned no agent instance.",
                    component="orchestration",
                    operation="prepare_agents",
                    context={"agent": name},
                )
            required_method = self._native_method_name(name)
            if not callable(getattr(agent, required_method, None)):
                raise SLAIRuntimeContractError(
                    "Requested SLAI agent does not expose the native method required by BIMAP orchestration.",
                    component="orchestration",
                    operation="prepare_agents",
                    context={
                        "agent": name,
                        "required_method": required_method,
                        "instance_type": type(agent).__name__,
                    },
                )
            self._agents[name] = agent

        return MappingProxyType({name: self._agents[name] for name in names})

    def check_liveness(self) -> SLAIHealthReport:
        """Delegate import-level liveness probing to ``SLAIHealthCheck``."""

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Checking SLAI orchestrator liveness",
        )
        self._ensure_open()
        return self.health.check_liveness()

    def check_readiness(
        self,
        required_agents: Sequence[str],
        *,
        prepare: bool = False,
    ) -> SLAIHealthReport:
        """Check runtime readiness for a declared agent set."""

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Checking SLAI orchestrator readiness",
        )
        self._ensure_open()
        names = normalize_agent_sequence(
            required_agents,
            field="required_agents",
            error_type=SLAIRuntimeContractError,
        )
        if not names:
            raise SLAIRuntimeContractError(
                "check_readiness() requires a non-empty required-agent set.",
                component="orchestration",
                operation="check_readiness",
                field="required_agents",
            )
        if prepare:
            self.prepare_agents(names)
        return self.health.check_readiness(
            factory=self.factory,
            shared_memory=self.shared_memory,
            required_agents=names,
            agents={name: self._agents.get(name) for name in names},
        )

    def orchestrate(
        self,
        job_envelope: SLAIJobEnvelope,
        *,
        task_overrides: Mapping[str, Any] | None = None,
    ) -> SLAIOrchestrationResult:
        """
        Execute one policy-authorized BIMAP SLAI job.

        ``task_overrides`` supplies agent-native task payloads for this runtime
        execution. Keys may be either ``"agent"`` or ``"phase:agent"``;
        phase-specific values take precedence. The payload mapping is an internal
        application/orchestration input, not an external queue contract, and it
        never mutates the immutable ``SLAIJobEnvelope``.

        BIMAP intentionally has **no implicit fallback task schema**. The current
        repository does not yet define a canonical application-level SLAI task
        contract, while SLAI v2.3 agents expose different native task surfaces.
        Therefore every invocation must resolve from ``task_overrides`` or the
        injected ``task_builder``. Missing task definitions fail explicitly
        rather than fabricating agent inputs.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Orchestrating BIMAP SLAI job",
            context={"job_id": getattr(getattr(job_envelope, "audit_job", None), "job_id", None)},
        )
        self._ensure_open()
        if not isinstance(job_envelope, SLAIJobEnvelope):
            raise SLAIRuntimeContractError(
                "job_envelope must be an SLAIJobEnvelope instance.",
                component="orchestration",
                operation="orchestrate",
                field="job_envelope",
                context={"received_type": type(job_envelope).__name__},
            )

        overrides = self._normalize_task_overrides(task_overrides)

        with self._lock:
            started_at = utc_now()
            job_envelope.assert_integrity()
            job_envelope.assert_policy(self.policy)
            agents = self.prepare_agents(job_envelope.requested_agents)
            readiness = self.health.check_readiness(
                factory=self.factory,
                shared_memory=self.shared_memory,
                required_agents=job_envelope.requested_agents,
                agents=agents,
            )
            self.health.assert_ready(
                readiness,
                allow_degraded=self.allow_degraded_readiness,
            )

            namespace = self._namespace(job_envelope)
            memory_keys: list[str] = []
            invocations: list[AgentInvocationRecord] = []
            outputs: dict[str, Any] = {}
            phase_outputs: dict[str, Any] = {}
            gate_outputs: dict[str, Mapping[str, Any]] = {}
            privacy_sanitized_payload: Any = None
            terminated_early = False
            termination_reason: str | None = None

            current_payload = normalize_json_mapping(
                thaw_json_value(job_envelope.grounded_context),
                field="grounded_context",
            )

            try:
                memory_keys.extend(
                    self._publish_job_context(namespace, job_envelope, current_payload)
                )

                # ----------------------------------------------------------
                # Ingress gates: quality then privacy.
                # ----------------------------------------------------------
                if "quality" in agents:
                    quality_output, record, key = self._invoke_agent(
                        "quality",
                        OrchestrationPhase.INGRESS_QUALITY,
                        job_envelope,
                        current_payload,
                        outputs,
                        namespace,
                        overrides,
                    )
                    invocations.append(record)
                    memory_keys.append(key)
                    outputs["quality"] = quality_output
                    phase_outputs[self._phase_key(record.phase, "quality")] = quality_output
                    quality_mapping = self._require_gate_mapping(
                        quality_output,
                        gate=GovernanceGate.QUALITY,
                        phase=record.phase,
                    )
                    gate_outputs[GovernanceGate.QUALITY.value] = quality_mapping
                    quality_gate = self.governance.normalize_gate_output(
                        GovernanceGate.QUALITY,
                        quality_mapping,
                    )
                    if quality_gate.disposition.prevents_automatic_release:
                        terminated_early = True
                        termination_reason = (
                            f"ingress:{GovernanceGate.QUALITY.value}:"
                            f"{quality_gate.disposition.value}"
                        )

                if not terminated_early and "privacy" in agents:
                    privacy_output, record, key = self._invoke_agent(
                        "privacy",
                        OrchestrationPhase.INGRESS_PRIVACY,
                        job_envelope,
                        current_payload,
                        outputs,
                        namespace,
                        overrides,
                    )
                    invocations.append(record)
                    memory_keys.append(key)
                    outputs["privacy"] = privacy_output
                    phase_outputs[self._phase_key(record.phase, "privacy")] = privacy_output
                    privacy_mapping = self._require_gate_mapping(
                        privacy_output,
                        gate=GovernanceGate.PRIVACY,
                        phase=record.phase,
                    )
                    gate_outputs[GovernanceGate.PRIVACY.value] = privacy_mapping
                    privacy_gate = self.governance.normalize_gate_output(
                        GovernanceGate.PRIVACY,
                        privacy_mapping,
                    )
                    if privacy_gate.disposition is GateDisposition.MODIFY:
                        current_payload = self._normalize_sanitized_mapping(
                            privacy_mapping["sanitized_payload"],
                            phase=record.phase,
                        )
                        privacy_sanitized_payload = dict(current_payload)
                    elif privacy_gate.disposition.prevents_automatic_release:
                        terminated_early = True
                        termination_reason = (
                            f"ingress:{GovernanceGate.PRIVACY.value}:"
                            f"{privacy_gate.disposition.value}"
                        )

                # ----------------------------------------------------------
                # Contextual analysis.  Deterministic audit evidence remains
                # authoritative; SLAI outputs are supplemental only.
                # ----------------------------------------------------------
                if not terminated_early:
                    for agent_name in self._ANALYSIS_ORDER:
                        if agent_name not in agents:
                            continue
                        output, record, key = self._invoke_agent(
                            agent_name,
                            OrchestrationPhase.ANALYSIS,
                            job_envelope,
                            current_payload,
                            outputs,
                            namespace,
                            overrides,
                        )
                        invocations.append(record)
                        memory_keys.append(key)
                        outputs[agent_name] = output
                        phase_outputs[self._phase_key(record.phase, agent_name)] = output

                    egress_payload = self._build_egress_payload(
                        job_envelope,
                        current_payload,
                        outputs,
                    )

                    # ------------------------------------------------------
                    # Egress governance gates in documented release order.
                    # ------------------------------------------------------
                    for agent_name, phase, gate in (
                        (
                            "quality",
                            OrchestrationPhase.EGRESS_QUALITY,
                            GovernanceGate.QUALITY,
                        ),
                        (
                            "evaluation",
                            OrchestrationPhase.EGRESS_EVALUATION,
                            GovernanceGate.EVALUATION,
                        ),
                        (
                            "safety",
                            OrchestrationPhase.EGRESS_SAFETY,
                            GovernanceGate.SAFETY,
                        ),
                        (
                            "privacy",
                            OrchestrationPhase.EGRESS_PRIVACY,
                            GovernanceGate.PRIVACY,
                        ),
                    ):
                        if agent_name not in agents:
                            continue
                        output, record, key = self._invoke_agent(
                            agent_name,
                            phase,
                            job_envelope,
                            egress_payload,
                            outputs,
                            namespace,
                            overrides,
                        )
                        invocations.append(record)
                        memory_keys.append(key)
                        outputs[agent_name] = output
                        phase_outputs[self._phase_key(record.phase, agent_name)] = output
                        gate_mapping = self._require_gate_mapping(
                            output,
                            gate=gate,
                            phase=record.phase,
                        )
                        gate_outputs[gate.value] = gate_mapping

                        if gate is GovernanceGate.PRIVACY:
                            privacy_gate = self.governance.normalize_gate_output(
                                gate,
                                gate_mapping,
                            )
                            if privacy_gate.disposition is GateDisposition.MODIFY:
                                privacy_sanitized_payload = gate_mapping["sanitized_payload"]

                    if "observability" in agents:
                        obs_payload = self._build_observability_payload(
                            job_envelope,
                            invocations,
                            gate_outputs,
                        )
                        output, record, key = self._invoke_agent(
                            "observability",
                            OrchestrationPhase.OBSERVABILITY,
                            job_envelope,
                            obs_payload,
                            outputs,
                            namespace,
                            overrides,
                        )
                        invocations.append(record)
                        memory_keys.append(key)
                        outputs["observability"] = output
                        phase_outputs[
                            self._phase_key(record.phase, "observability")
                        ] = output

                completed_at = utc_now()
                result = SLAIOrchestrationResult(
                    job_id=job_envelope.audit_job.job_id,
                    order_id=job_envelope.audit_job.order_id,
                    correlation_id=job_envelope.correlation_id,
                    requested_agents=job_envelope.requested_agents,
                    started_at=started_at,
                    completed_at=completed_at,
                    invocations=tuple(invocations),
                    outputs=outputs,
                    phase_outputs=phase_outputs,
                    gate_outputs=gate_outputs,
                    health_report=readiness,
                    terminated_early=terminated_early,
                    termination_reason=termination_reason,
                    privacy_sanitized_payload=privacy_sanitized_payload,
                )
                logger.info(
                    "BIMAP SLAI orchestration completed: job_id=%s correlation_id=%s "
                    "invocations=%d terminated_early=%s",
                    result.job_id,
                    result.correlation_id,
                    len(result.invocations),
                    result.terminated_early,
                )
                return result
            finally:
                if not self.retain_shared_memory:
                    self._cleanup_job_memory(memory_keys)

    def close(self) -> None:
        """Release orchestrator-owned SLAI runtime resources exactly once."""

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Closing BIMAP SLAI orchestrator",
        )
        with self._lock:
            if self._closed:
                return
            self._closed = True

            if self._owns_factory:
                shutdown = getattr(self.factory, "shutdown", None)
                if callable(shutdown):
                    try:
                        shutdown()
                    except Exception as exc:
                        logger.warning(
                            "Owned SLAI AgentFactory shutdown failed: %s",
                            type(exc).__name__,
                        )

            if self._owns_shared_memory:
                for method_name in ("close", "shutdown"):
                    method = getattr(self.shared_memory, method_name, None)
                    if callable(method):
                        try:
                            method()
                        except Exception as exc:
                            logger.warning(
                                "Owned SLAI SharedMemory shutdown failed: %s",
                                type(exc).__name__,
                            )
                        break

            self._agents.clear()
            logger.info("BIMAP SLAI orchestrator closed")

    shutdown = close

    def __enter__(self) -> "SLAIOrchestrator":
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Entering SLAI orchestrator context",
        )
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Exiting SLAI orchestrator context",
        )
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_open(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Checking SLAI orchestrator lifecycle",
        )
        if self._closed:
            raise SLAIOrchestrationError(
                "SLAI orchestrator is closed.",
                component="orchestration",
                operation="ensure_open",
            )

    @staticmethod
    def _namespace(job_envelope: SLAIJobEnvelope) -> str:
        return f"bimap.{job_envelope.correlation_id}"

    @staticmethod
    def _phase_key(phase: OrchestrationPhase, agent: str) -> str:
        return f"{phase.value}:{agent}"

    def _publish_job_context(
        self,
        namespace: str,
        envelope: SLAIJobEnvelope,
        grounded_context: Mapping[str, Any],
    ) -> tuple[str, ...]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Publishing BIMAP job context to SLAI shared memory",
            context={"job_id": envelope.audit_job.job_id},
        )
        keys = (
            f"{namespace}.envelope",
            f"{namespace}.grounded_context",
        )
        self._memory_set(keys[0], envelope.summary())
        self._memory_set(keys[1], grounded_context)
        return keys

    def _memory_set(self, key: str, value: Any) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Writing BIMAP namespace value to SLAI shared memory",
            context={"key": key},
        )
        setter = getattr(self.shared_memory, "set", None)
        if not callable(setter):
            raise SLAIRuntimeContractError(
                "SharedMemory-compatible runtime must expose set().",
                component="orchestration",
                operation="memory_set",
            )
        try:
            setter(key, value)
        except Exception as exc:
            raise SLAIOrchestrationError(
                "Unable to publish BIMAP state to SLAI shared memory.",
                component="orchestration",
                operation="memory_set",
                context={"key": key},
                cause=exc,
            ) from exc

    def _cleanup_job_memory(self, keys: Sequence[str]) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Cleaning BIMAP job namespace from SLAI shared memory",
            context={"key_count": len(tuple(keys))},
        )
        deleter = getattr(self.shared_memory, "delete", None)
        if not callable(deleter):
            logger.warning("SLAI SharedMemory does not expose delete(); BIMAP job namespace retained")
            return
        for key in reversed(tuple(keys)):
            try:
                deleter(key)
            except Exception as exc:
                logger.warning(
                    "Failed to delete BIMAP SLAI shared-memory key '%s': %s",
                    key,
                    type(exc).__name__,
                )

    def _invoke_agent(
        self,
        agent_name: str,
        phase: OrchestrationPhase,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        prior_outputs: Mapping[str, Any],
        namespace: str,
        overrides: Mapping[str, Any],
    ) -> tuple[Any, AgentInvocationRecord, str]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Invoking SLAI agent for BIMAP",
            context={"agent": agent_name, "phase": phase.value, "job_id": envelope.audit_job.job_id},
        )

        agent = self._agents.get(agent_name)
        if agent is None:
            raise SLAIRuntimeContractError(
                "Requested SLAI agent has not been prepared.",
                component="orchestration",
                operation="invoke_agent",
                context={"agent": agent_name, "phase": phase.value},
            )
        required_method = self._native_method_name(agent_name)
        method = getattr(agent, required_method, None)
        if not callable(method):
            raise SLAIRuntimeContractError(
                "Prepared SLAI agent lacks the native method required by BIMAP.",
                component="orchestration",
                operation="invoke_agent",
                context={
                    "agent": agent_name,
                    "phase": phase.value,
                    "required_method": required_method,
                },
            )

        task = self._resolve_task(
            agent_name,
            phase,
            envelope,
            payload,
            prior_outputs,
            namespace,
            overrides,
        )
        started_at = utc_now()
        started_perf = perf_counter()
        try:
            output = self._execute_native_agent_method(
                agent_name,
                method,
                task,
                envelope=envelope,
                phase=phase,
                namespace=namespace,
            )
        except Exception as exc:
            completed_at = utc_now()
            duration_ms = max(0.0, (perf_counter() - started_perf) * 1000.0)
            error = SLAIAgentInvocationError(
                "SLAI agent invocation failed for BIMAP job.",
                component="orchestration",
                operation="invoke_agent",
                context={
                    "agent": agent_name,
                    "phase": phase.value,
                    "job_id": envelope.audit_job.job_id,
                    "correlation_id": envelope.correlation_id,
                },
                cause=exc,
            )
            failed_record = AgentInvocationRecord(
                agent=agent_name,
                phase=phase,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                succeeded=False,
                output=None,
                error=error.to_dict(include_context=True),
            )
            logger.error(
                "SLAI agent invocation failed: agent=%s phase=%s job_id=%s error=%s",
                agent_name,
                phase.value,
                envelope.audit_job.job_id,
                type(exc).__name__,
            )
            # The record is attached to the structured exception context only as
            # metadata; raw customer evidence is never logged here.
            error.context["invocation"] = failed_record.to_dict(include_output=False)
            raise error from exc

        completed_at = utc_now()
        duration_ms = max(0.0, (perf_counter() - started_perf) * 1000.0)
        record = AgentInvocationRecord(
            agent=agent_name,
            phase=phase,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            succeeded=True,
            output=output,
        )
        memory_key = f"{namespace}.output.{phase.value}.{agent_name}"
        self._memory_set(memory_key, output)
        logger.info(
            "SLAI agent invocation completed: agent=%s phase=%s job_id=%s duration_ms=%.3f",
            agent_name,
            phase.value,
            envelope.audit_job.job_id,
            duration_ms,
        )
        return output, record, memory_key

    def _resolve_task(
        self,
        agent_name: str,
        phase: OrchestrationPhase,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        prior_outputs: Mapping[str, Any],
        namespace: str,
        overrides: Mapping[str, Any],
    ) -> Any:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Resolving SLAI agent task payload",
            context={"agent": agent_name, "phase": phase.value},
        )

        phase_key = self._phase_key(phase, agent_name)
        if phase_key in overrides:
            return overrides[phase_key]
        if agent_name in overrides:
            return overrides[agent_name]
        if self.task_builder is not None:
            try:
                return self.task_builder(
                    agent_name,
                    phase,
                    envelope,
                    payload,
                    prior_outputs,
                    namespace,
                )
            except SLAIIntegrationError:
                raise
            except Exception as exc:
                raise SLAIOrchestrationError(
                    "Injected SLAI task builder failed.",
                    component="orchestration",
                    operation="resolve_task",
                    context={
                        "agent": agent_name,
                        "phase": phase.value,
                        "job_id": envelope.audit_job.job_id,
                    },
                    cause=exc,
                ) from exc

        raise SLAIRuntimeContractError(
            "No agent-native SLAI task payload is defined for this BIMAP invocation.",
            component="orchestration",
            operation="resolve_task",
            field="task_overrides/task_builder",
            context={
                "agent": agent_name,
                "phase": phase.value,
                "job_id": envelope.audit_job.job_id,
                "accepted_keys": [phase_key, agent_name],
            },
        )

    @staticmethod
    def _native_method_name(agent_name: str) -> str:
        """Return the verified SLAI v2.3 public method BIMAP invokes for an agent."""

        if agent_name == "privacy":
            return "perform_task_privacy"
        if agent_name == "evaluation":
            return "execute_validation_cycle"
        return "perform_task"

    def _execute_native_agent_method(
        self,
        agent_name: str,
        method: Callable[..., Any],
        task: Any,
        *,
        envelope: SLAIJobEnvelope,
        phase: OrchestrationPhase,
        namespace: str,
    ) -> Any:
        """
        Invoke one verified SLAI v2.3 method without guessing its input schema.

        QualityAgent accepts a mapping task through ``perform_task``;
        PrivacyAgent exposes ``perform_task_privacy(input_data, context=None)``;
        EvaluationAgent exposes ``execute_validation_cycle(params: dict)``.
        Other selected SLAI agents are invoked through their BaseAgent-compatible
        ``perform_task`` surface. The task *content* remains caller/task-builder
        supplied.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Dispatching agent-native SLAI method",
            context={"agent": agent_name, "phase": phase.value},
        )

        if agent_name in {"quality", "evaluation"} and not isinstance(task, Mapping):
            raise SLAIRuntimeContractError(
                "The selected SLAI agent requires a mapping-shaped task payload.",
                component="orchestration",
                operation="execute_native_agent_method",
                field="task",
                context={
                    "agent": agent_name,
                    "phase": phase.value,
                    "received_type": type(task).__name__,
                },
            )

        integration_context = {
            "source": "bimap",
            "job_id": envelope.audit_job.job_id,
            "order_id": envelope.audit_job.order_id,
            "correlation_id": envelope.correlation_id,
            "phase": phase.value,
            "agent": agent_name,
            "shared_memory_namespace": namespace,
            "context_digest": envelope.context_digest,
        }

        if agent_name == "privacy":
            return method(task, context=integration_context)
        if agent_name == "evaluation":
            return method(dict(task))
        return method(task)

    def _build_egress_payload(
        self,
        envelope: SLAIJobEnvelope,
        grounded_context: Mapping[str, Any],
        outputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Building SLAI egress governance payload",
            context={"job_id": envelope.audit_job.job_id},
        )
        safe_outputs, omitted = self._json_safe_output_projection(outputs)
        return {
            "grounded_context": dict(grounded_context),
            "supplemental_agent_outputs": safe_outputs,
            "omitted_non_json_outputs": omitted,
            "job": envelope.summary(),
        }

    def _build_observability_payload(
        self,
        envelope: SLAIJobEnvelope,
        invocations: Sequence[AgentInvocationRecord],
        gate_outputs: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Building SLAI observability payload",
            context={"job_id": envelope.audit_job.job_id},
        )
        return {
            "job": envelope.summary(),
            "invocations": [item.to_dict(include_output=False) for item in invocations],
            "governance_gate_names": sorted(gate_outputs),
        }

    def _require_gate_mapping(
        self,
        output: Any,
        *,
        gate: GovernanceGate,
        phase: OrchestrationPhase,
    ) -> Mapping[str, Any]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Validating SLAI governance-agent output contract",
            context={"gate": gate.value, "phase": phase.value},
        )
        if not isinstance(output, Mapping):
            raise SLAIRuntimeContractError(
                "SLAI governance agents must return mapping outputs for BIMAP governance conversion.",
                component="orchestration",
                operation="require_gate_mapping",
                context={
                    "gate": gate.value,
                    "phase": phase.value,
                    "received_type": type(output).__name__,
                },
            )
        return dict(output)

    def _normalize_sanitized_mapping(
        self,
        payload: Any,
        *,
        phase: OrchestrationPhase,
    ) -> dict[str, Any]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Normalizing PrivacyAgent sanitized BIMAP context",
            context={"phase": phase.value},
        )
        if not isinstance(payload, Mapping):
            raise SLAIRuntimeContractError(
                "PrivacyAgent sanitized_payload must preserve BIMAP's mapping-shaped grounded context.",
                component="orchestration",
                operation="normalize_sanitized_mapping",
                field="sanitized_payload",
                context={"received_type": type(payload).__name__},
            )
        return normalize_json_mapping(payload, field="sanitized_payload")

    def _json_safe_output_projection(
        self,
        outputs: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Projecting JSON-safe prior SLAI outputs",
            context={"output_count": len(outputs)},
        )
        projected: dict[str, Any] = {}
        omitted: list[str] = []
        for raw_name, raw_value in outputs.items():
            name = normalize_agent_name(
                raw_name,
                field="outputs.key",
                error_type=SLAIRuntimeContractError,
            )
            candidate = raw_value
            to_dict = getattr(candidate, "to_dict", None)
            if callable(to_dict):
                try:
                    candidate = to_dict()
                except Exception:
                    omitted.append(name)
                    continue
            try:
                wrapper = normalize_json_mapping(
                    {"value": candidate},
                    field=f"outputs.{name}",
                )
            except Exception:
                omitted.append(name)
                continue
            projected[name] = wrapper["value"]
        return projected, omitted

    def _normalize_task_overrides(
        self,
        task_overrides: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        announce_method_start(
            printer,
            logger,
            "SLAI ORCHESTRATION",
            "Normalizing SLAI task overrides",
        )
        if task_overrides is None:
            return MappingProxyType({})
        raw = require_mapping(
            task_overrides,
            field="task_overrides",
            error_type=SLAIRuntimeContractError,
        )
        normalized: dict[str, Any] = {}
        for raw_key, value in raw.items():
            key = require_text(
                raw_key,
                field="task_overrides.key",
                error_type=SLAIRuntimeContractError,
            ).lower()
            if key in normalized:
                raise SLAIRuntimeContractError(
                    "Duplicate task override key after normalization.",
                    component="orchestration",
                    operation="normalize_task_overrides",
                    context={"key": key},
                )
            normalized[key] = value
        return MappingProxyType(normalized)


__all__ = [
    "OrchestrationPhase",
    "AgentInvocationRecord",
    "SLAIOrchestrationResult",
    "AgentTaskBuilder",
    "SLAIOrchestrator",
]


if __name__ == "__main__":
    print("\n=== Running SLAI Adapter Self-Test ===\n")
    printer.status("TEST", "SLAI Adapter initialized", "info")

    orchestrator =SLAIOrchestrator()

    printer.status("START", orchestrator, "success" if orchestrator is not None else "error")
    printer.status("PASS", "SLAI Orchestrator initialized", "success")
    print("\n===* * * Preparation * * *===\n")
    agents = ["quality", "privacy"]
    SLAIAgents = orchestrator.prepare_agents(agent_names=agents)

    printer.status("AGENTS", SLAIAgents, "success" if SLAIAgents is not None else "error")
    printer.status("PASS", "SLAI Orchestrator initialized", "success")
    print("\n===* * * Orchastrate * * *===\n")
    import datetime
    from ..domain.products.models import ProductCode
    from ..contracts.audit_job import AuditJob
    job_id = "test_job_001"
    order_id = "test_order_001"
    order_version = 1
    product_code = ProductCode.FAMILY_AUDIT.value
    submitted_at = datetime.datetime.now(datetime.timezone.utc)
    requested_agents = ["knowledge", "reader", "evaluate", "quality", "privacy", "observability"]
    grounded_context = {"test_key": "test_value"}
    correlation_id = "test_correlation_001"
    created_at = submitted_at

    audit_job = AuditJob(job_id, order_id, order_version=order_version,
                         product_code=product_code, submitted_at=submitted_at,
                         evidence_manifest_ref="manifest://test")
    job_envelope = SLAIJobEnvelope(audit_job=audit_job, correlation_id=correlation_id,
                                   requested_agents=agents, grounded_context=grounded_context, # type: ignore
                                   created_at=created_at)
    task_overrides = {
        "quality": {"records": [{"id": "1", "text": "sample record", "label": "test"}], "dataset_id": "test_dataset"},
        "privacy": {"input_data": {"field": "value"}},   # PrivacyAgent expects a mapping
        }
    result = orchestrator.orchestrate(job_envelope=job_envelope, task_overrides=task_overrides)

    printer.status("AGENTS", result, "success" if result is not None else "error")
    printer.status("PASS", "SLAI Orchestrator initialized", "success")

    print("\n=== Test ran successfully ===\n")