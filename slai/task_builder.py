"""
BIMAP-owned task translation for SLAI v2.3 agents.

This module is the semantic translation boundary between BIMAP's validated
audit context and SLAI's heterogeneous agent task contracts.

Architectural role
------------------
The deterministic BIMAP Audit Engine remains authoritative.  This module does
not create findings, requirements, evidence, rules, governance decisions, or
audit state.  It only projects already-grounded BIMAP data into task payloads
accepted by verified SLAI v2.3 public agent APIs.

Dependency direction
--------------------
    contracts / SLAI integration primitives
                ↓
          task_builder.py
                ↓
      SLAIOrchestrator injection

This module MUST NOT:
- import or instantiate concrete SLAI agents;
- call AgentFactory or SharedMemory;
- modify AuditResult / FindingContract data;
- invent evidence or compliance conclusions;
- persist customer data;
- perform agent routing;
- bypass SLAIAgentPolicy;
- execute SLAI operations directly;
- fabricate tasks for incompatible agent APIs.

Task resolution remains:

    phase-specific task_overrides
        ↓
    agent-specific task_overrides
        ↓
    BIMAPSLAITaskBuilder
        ↓
    explicit failure

The explicit-override path therefore remains available for legitimate
agent-specific inputs that are outside the normal BIMAP audit-result contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .job_envelope import SLAIJobEnvelope
from .orchestration import OrchestrationPhase
from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SLAI Task Builder")
printer = PrettyPrinter()

_COMPONENT = "slai_task_builder"


class BIMAPSLAITaskBuilder:
    """
    Translate grounded BIMAP audit data into verified SLAI-native tasks.

    Automatic translation is intentionally restricted to SLAI operations whose
    current public contracts can consume BIMAP's normal post-audit data without
    semantic fabrication.

    Automatically supported
    -----------------------
    quality:
        QualityAgent.perform_task(mapping)

    privacy:
        PrivacyAgent.perform_task_privacy(mapping, context=...)

    collaborative:
        CollaborativeAgent.perform_task(
            {"mode": "assess_task", ...}
        )

    knowledge:
        BaseAgent-compatible KnowledgeAgent prediction/retrieval path

    reasoning:
        ReasoningAgent.perform_task(
            {"task_type": "reason", ...}
        )

    language:
        LanguageAgent.perform_task(mapping)

    safety:
        SafetyAgent.perform_task(mapping)

    observability:
        ObservabilityAgent.perform_task(mapping)

    Explicit-input-only
    -------------------
    reader:
        Requires actual reader/file inputs.

    perception:
        Requires valid perception inputs.

    planning:
        Current PlanningAgent public BIMAP-facing execution path does not expose
        a domain-remediation planning task.

    execution:
        Requires separately authorized executable work.

    evaluation:
        Current execute_validation_cycle() evaluates SLAI/system dimensions and
        is not a domain-neutral BIM audit-result evaluator.

    learning / adaptive / qnn:
        Not appropriate for implicit live-audit task construction.
    """

    _AUTO_SUPPORTED = frozenset(
        {
            "quality",
            "privacy",
            "collaborative",
            "knowledge",
            "reasoning",
            "language",
            "safety",
            "observability",
        }
    )

    _EXPLICIT_ONLY = {
        "reader": (
            "ReaderAgent requires reader-native file/document inputs that are "
            "not present in the normal post-audit BIMAP grounded context."
        ),
        "perception": (
            "PerceptionAgent requires perception-native input and must not be "
            "given fabricated image/tensor/representation data."
        ),
        "planning": (
            "The current PlanningAgent BIMAP-facing execution surface does not "
            "expose a verified audit-remediation planning operation."
        ),
        "execution": (
            "ExecutionAgent work must be explicitly constructed and separately "
            "authorized; BIMAP must never infer executable actions from findings."
        ),
        "evaluation": (
            "EvaluationAgent.execute_validation_cycle() is not a domain-neutral "
            "BIM audit-result evaluator and therefore requires an explicit "
            "verified SLAI-native task."
        ),
        "learning": (
            "LearningAgent must not implicitly learn from live customer audit "
            "data."
        ),
        "adaptive": (
            "AdaptiveAgent must not implicitly adapt policy from live BIMAP "
            "audit execution."
        ),
        "qnn": (
            "QNNAgent has no verified implicit BIMAP audit task contract."
        ),
    }

    _ALLOWED_PHASES = {
        "quality": frozenset(
            {
                OrchestrationPhase.INGRESS_QUALITY,
                OrchestrationPhase.EGRESS_QUALITY,
            }
        ),
        "privacy": frozenset(
            {
                OrchestrationPhase.INGRESS_PRIVACY,
                OrchestrationPhase.EGRESS_PRIVACY,
            }
        ),
        "collaborative": frozenset(
            {
                OrchestrationPhase.ANALYSIS,
            }
        ),
        "knowledge": frozenset(
            {
                OrchestrationPhase.ANALYSIS,
            }
        ),
        "reasoning": frozenset(
            {
                OrchestrationPhase.ANALYSIS,
            }
        ),
        "language": frozenset(
            {
                OrchestrationPhase.ANALYSIS,
            }
        ),
        "safety": frozenset(
            {
                OrchestrationPhase.EGRESS_SAFETY,
            }
        ),
        "observability": frozenset(
            {
                OrchestrationPhase.OBSERVABILITY,
            }
        ),
    }

    def __init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI TASK",
            "Initializing BIMAP SLAI task builder",
        )

        logger.info(
            {
                "event": "bimap_slai_task_builder_initialized",
                "auto_supported_agents": tuple(sorted(self._AUTO_SUPPORTED)),
                "explicit_only_agents": tuple(sorted(self._EXPLICIT_ONLY)),
            }
        )

    def __call__(
        self,
        agent_name: str,
        phase: OrchestrationPhase,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        prior_outputs: Mapping[str, Any],
        namespace: str,
    ) -> Any:
        """
        Build one verified SLAI-native task.

        Parameters match ``AgentTaskBuilder`` exactly.

        ``payload`` is the current grounded BIMAP context.  After an ingress
        PrivacyAgent MODIFY decision this is expected to be the sanitized
        current payload supplied by SLAIOrchestrator.

        ``prior_outputs`` may contain runtime SLAI values. Only JSON-safe
        projections are allowed to cross into subsequent automatically-built
        tasks.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI TASK",
            "Building SLAI agent task",
            context={
                "agent": str(agent_name),
                "phase": (
                    phase.value
                    if isinstance(phase, OrchestrationPhase)
                    else str(phase)
                ),
            },
        )

        agent = normalize_agent_name(
            agent_name,
            field="agent_name",
            error_type=SLAIRuntimeContractError,
        )

        if not isinstance(phase, OrchestrationPhase):
            raise SLAIRuntimeContractError(
                "SLAI task builder requires an OrchestrationPhase.",
                component=_COMPONENT,
                operation="build_task",
                field="phase",
                context={
                    "received_type": type(phase).__name__,
                    "agent": agent,
                },
            )

        if not isinstance(envelope, SLAIJobEnvelope):
            raise SLAIRuntimeContractError(
                "SLAI task builder requires an SLAIJobEnvelope.",
                component=_COMPONENT,
                operation="build_task",
                field="envelope",
                context={
                    "received_type": type(envelope).__name__,
                    "agent": agent,
                    "phase": phase.value,
                },
            )

        envelope.assert_integrity()

        grounded = normalize_json_mapping(
            require_mapping(
                payload,
                field="payload",
                error_type=SLAIRuntimeContractError,
            ),
            field="task_builder.payload",
        )

        if not isinstance(prior_outputs, Mapping):
            raise SLAIRuntimeContractError(
                "prior_outputs must be a mapping.",
                component=_COMPONENT,
                operation="build_task",
                field="prior_outputs",
                context={
                    "received_type": type(prior_outputs).__name__,
                    "agent": agent,
                    "phase": phase.value,
                },
            )

        target_namespace = require_text(
            namespace,
            field="namespace",
            error_type=SLAIRuntimeContractError,
        )

        if agent in self._EXPLICIT_ONLY:
            self._raise_explicit_task_required(
                agent,
                phase,
                envelope,
            )

        if agent not in self._AUTO_SUPPORTED:
            raise SLAIRuntimeContractError(
                "No verified automatic BIMAP task translation exists for the "
                "requested SLAI agent.",
                component=_COMPONENT,
                operation="build_task",
                field="agent_name",
                context={
                    "agent": agent,
                    "phase": phase.value,
                },
            )

        self._require_phase(
            agent,
            phase,
        )

        safe_prior, omitted_prior = (
            self._project_prior_outputs(
                prior_outputs
            )
        )

        integration = self._integration_context(
            envelope,
            phase,
            target_namespace,
            omitted_prior=omitted_prior,
        )

        try:
            if agent == "quality":
                task = self._build_quality_task(
                    phase,
                    envelope,
                    grounded,
                    integration,
                )

            elif agent == "privacy":
                task = self._build_privacy_task(
                    envelope,
                    grounded,
                )

            elif agent == "collaborative":
                task = self._build_collaborative_task(
                    envelope,
                    grounded,
                    integration,
                )

            elif agent == "knowledge":
                task = self._build_knowledge_task(
                    envelope,
                    grounded,
                    integration,
                )

            elif agent == "reasoning":
                task = self._build_reasoning_task(
                    envelope,
                    grounded,
                    safe_prior,
                    integration,
                )

            elif agent == "language":
                task = self._build_language_task(
                    envelope,
                    grounded,
                    safe_prior,
                    integration,
                    target_namespace,
                )

            elif agent == "safety":
                task = self._build_safety_task(
                    grounded,
                )

            elif agent == "observability":
                task = self._build_observability_task(
                    envelope,
                    grounded,
                    integration,
                )

            else:
                raise SLAIRuntimeContractError(
                    "SLAI task builder dispatch reached an unsupported agent.",
                    component=_COMPONENT,
                    operation="build_task",
                    context={
                        "agent": agent,
                        "phase": phase.value,
                    },
                )

        except SLAIIntegrationError:
            raise

        except Exception as exc:
            raise SLAIOrchestrationError(
                "BIMAP failed to translate grounded audit data into an "
                "SLAI-native task.",
                component=_COMPONENT,
                operation="build_task",
                context={
                    "agent": agent,
                    "phase": phase.value,
                    "job_id": envelope.audit_job.job_id,
                    "order_id": envelope.audit_job.order_id,
                },
                cause=exc,
            ) from exc

        logger.debug(
            {
                "event": "bimap_slai_task_built",
                "agent": agent,
                "phase": phase.value,
                "job_id": envelope.audit_job.job_id,
                "prior_output_count": len(safe_prior),
                "omitted_prior_output_count": len(omitted_prior),
            }
        )

        return task

    # ------------------------------------------------------------------
    # Verified task builders
    # ------------------------------------------------------------------

    def _build_quality_task(
        self,
        phase: OrchestrationPhase,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        integration: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Build a QualityAgent ``evaluate_batch`` task.

        No artificial schema or statistical baseline is created.

        Ingress quality prefers authoritative deterministic finding records,
        followed by evidence records, followed by ingestion-manifest records.

        Egress quality assesses supplemental SLAI outputs as individual records.
        """

        if phase is OrchestrationPhase.INGRESS_QUALITY:
            records = self._quality_ingress_records(
                payload
            )

        elif phase is OrchestrationPhase.EGRESS_QUALITY:
            records = self._quality_egress_records(
                payload
            )

        else:
            raise SLAIRuntimeContractError(
                "Quality task requested outside a Quality phase.",
                component=_COMPONENT,
                operation="build_quality_task",
                field="phase",
                context={"phase": phase.value},
            )

        return {
            "operation": "evaluate_batch",
            "records": records,
            "dataset_id": envelope.audit_job.job_id,
            "source_id": envelope.audit_job.order_id,
            "batch_id": envelope.correlation_id,
            "context": dict(integration),
        }

    def _build_privacy_task(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Build the minimal verified PrivacyAgent task.

        Purpose, policy, sensitive-field lists and retention periods are
        intentionally not invented here.
        """

        return {
            "payload": dict(payload),
            "request_id": envelope.correlation_id,
            "record_id": envelope.audit_job.job_id,
        }

    def _build_collaborative_task(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        integration: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Use CollaborativeAgent only for advisory task/risk assessment.

        It is deliberately not asked to delegate, execute, register agents or
        become a second orchestration authority.
        """

        summary = self._audit_summary(
            envelope,
            payload,
        )

        return {
            "mode": "assess_task",
            "task": {
                "id": envelope.audit_job.job_id,
                "task_type": "analysis",
                "type": "analysis",
                "product_code": envelope.audit_job.product_code,
                **summary,
            },
            "source_agent": "bimap",
            "context": dict(integration),
        }

    def _build_knowledge_task(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        integration: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Build a retrieval-only KnowledgeAgent request.

        Customer evidence values are intentionally excluded from the query.
        The query is based on stable finding/rule metadata only, so KnowledgeAgent
        is used for reference retrieval rather than customer-data ingestion.
        """

        query = self._knowledge_query(
            envelope,
            payload,
        )

        return {
            "operation": "predict",
            "task_data": query,
            "context": dict(integration),
        }

    def _build_reasoning_task(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        prior_outputs: Mapping[str, Any],
        integration: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Build stateless, task-local reasoning.

        ``reason`` is used instead of add_fact/load_knowledge/learning APIs so
        BIMAP does not intentionally write customer audit evidence into the
        ReasoningAgent's persistent knowledge base.

        Both selected reasoning strategies are existing SLAI v2.3 strategies.
        """

        return {
            "task_type": "reason",
            "problem": (
                "Identify supported shared causes, dependencies, relationships "
                "and remediation implications among the supplied BIMAP audit "
                "results. Treat all deterministic BIMAP findings as authoritative. "
                "Do not create, delete, rewrite, upgrade or downgrade a finding. "
                "Do not infer compliance where deterministic evidence does not "
                "establish it."
            ),
            "reasoning_type": "abduction+cause_effect",
            "context": {
                "audit_result": dict(payload),
                "supplemental_agent_outputs": dict(prior_outputs),
                "integration": dict(integration),
            },
        }

    def _build_language_task(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        prior_outputs: Mapping[str, Any],
        integration: Mapping[str, Any],
        namespace: str,
    ) -> dict[str, Any]:
        """
        Build a bounded-semantics LanguageAgent task.

        The language task may explain existing findings and prior SLAI analysis,
        but it is explicitly instructed not to redefine deterministic results.
        """

        findings = self._extract_findings(
            payload
        )

        source = {
            "audit_summary": self._audit_summary(
                envelope,
                payload,
            ),
            "findings": findings,
            "supplemental_agent_outputs": dict(
                prior_outputs
            ),
        }

        text = (
            "Explain the following BIMAP audit information clearly and "
            "concisely for the audit user. Preserve the exact meaning and "
            "severity of deterministic findings. Do not create new findings, "
            "change compliance status, change evidence references, or present "
            "SLAI interpretation as deterministic audit fact.\n\n"
            + canonical_json_dumps(source)
        )

        return {
            "text": text,
            "session_id": namespace,
            "context": dict(integration),
        }

    def _build_safety_task(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Build an egress SafetyAgent assessment over the actual SLAI egress data.

        Safety receives no fabricated safety score, risk score or policy result.
        """

        return {
            "content": canonical_json_dumps(
                payload
            )
        }

    def _build_observability_task(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
        integration: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Convert BIMAP's real invocation telemetry into ObservabilityAgent inputs.

        Only observed invocation durations and statuses are emitted. No synthetic
        latency, throughput, resource, SLO or incident measurements are created.
        """

        raw_invocations = payload.get(
            "invocations",
            (),
        )

        invocations: tuple[Mapping[str, Any], ...]

        if (
            isinstance(
                raw_invocations,
                Sequence,
            )
            and not isinstance(
                raw_invocations,
                (
                    str,
                    bytes,
                    bytearray,
                ),
            )
        ):
            invocations = tuple(
                item
                for item in raw_invocations
                if isinstance(item, Mapping)
            )
        else:
            invocations = ()

        latencies: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []

        for invocation in invocations:
            agent = str(
                invocation.get(
                    "agent",
                    "unknown",
                )
            )
            phase = str(
                invocation.get(
                    "phase",
                    "unknown",
                )
            )

            succeeded = (
                invocation.get("succeeded")
                is True
            )

            duration = invocation.get(
                "duration_ms"
            )

            if (
                isinstance(
                    duration,
                    (int, float),
                )
                and not isinstance(
                    duration,
                    bool,
                )
                and float(duration) >= 0.0
            ):
                latencies.append(
                    {
                        "subject": (
                            f"{agent}.{phase}"
                        ),
                        "duration_ms": float(
                            duration
                        ),
                        "status": (
                            "ok"
                            if succeeded
                            else "error"
                        ),
                        "metadata": {
                            "agent": agent,
                            "phase": phase,
                        },
                    }
                )

            events.append(
                {
                    "event_type": (
                        "slai_agent_invocation"
                    ),
                    "severity": (
                        "info"
                        if succeeded
                        else "error"
                    ),
                    "agent_name": agent,
                    "payload": {
                        "phase": phase,
                        "succeeded": succeeded,
                        "output_type": (
                            invocation.get(
                                "output_type"
                            )
                        ),
                    },
                }
            )

        gate_names = payload.get(
            "governance_gate_names",
            (),
        )

        gate_sequence: list[str] = []

        if (
            isinstance(
                gate_names,
                Sequence,
            )
            and not isinstance(
                gate_names,
                (
                    str,
                    bytes,
                    bytearray,
                ),
            )
        ):
            gate_sequence = [
                str(item)
                for item in gate_names
            ]

        return {
            "source": "bimap",
            "pipeline": "bimap_slai",
            "trace": {
                "task_name": "bimap_audit",
                "agent_name": "bimap",
                "operation_name": (
                    "slai_orchestration"
                ),
                "trace_id": (
                    envelope.correlation_id
                ),
                "service": "bimap",
                "metadata": {
                    "job_id": (
                        envelope.audit_job.job_id
                    ),
                    "order_id": (
                        envelope.audit_job.order_id
                    ),
                    "product_code": (
                        envelope.audit_job.product_code
                    ),
                    "governance_gate_names": (
                        gate_sequence
                    ),
                },
            },
            "latencies": latencies,
            "events": events,
            "context": dict(integration),
        }

    # ------------------------------------------------------------------
    # Deterministic projections
    # ------------------------------------------------------------------

    def _quality_ingress_records(
        self,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        findings = self._extract_findings(
            payload
        )

        if findings:
            return findings

        context = payload.get("context")

        if isinstance(context, Mapping):
            evidence = context.get("evidence")

            if self._is_mapping_sequence(
                evidence
            ):
                records = [
                    dict(item)
                    for item in evidence
                    if isinstance(item, Mapping)
                ]

                if records:
                    return records

        manifests = payload.get(
            "ingestion_manifests"
        )

        if self._is_mapping_sequence(
            manifests
        ):
            records = [
                dict(item)
                for item in manifests
                if isinstance(item, Mapping)
            ]

            if records:
                return records

        # QualityAgent requires a non-empty record collection.
        # This is not fabricated evidence: the one record is simply the
        # existing complete grounded BIMAP audit mapping.
        return [
            {
                "audit_result": dict(payload)
            }
        ]

    def _quality_egress_records(
        self,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        outputs = payload.get(
            "supplemental_agent_outputs"
        )

        records: list[dict[str, Any]] = []

        if isinstance(outputs, Mapping):
            for raw_agent, output in (
                outputs.items()
            ):
                agent = normalize_agent_name(
                    raw_agent,
                    field=(
                        "supplemental_agent_outputs."
                        "agent"
                    ),
                    error_type=(
                        SLAIRuntimeContractError
                    ),
                )

                records.append(
                    {
                        "agent": agent,
                        "output": output,
                    }
                )

        if records:
            return records

        return [
            {
                "slai_egress": dict(payload)
            }
        ]

    def _extract_findings(
        self,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Return deterministic finding mappings without changing their contents.

        Combined Audit source findings can also appear in source-stage results,
        so stable finding-id deduplication prevents double representation.
        """

        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def append_candidate(
            candidate: Any,
        ) -> None:
            if not isinstance(
                candidate,
                Mapping,
            ):
                return

            normalized = normalize_json_mapping(
                candidate,
                field="finding",
            )

            raw_id = normalized.get(
                "finding_id"
            )

            if isinstance(raw_id, str):
                finding_id = raw_id.strip()

                if (
                    finding_id
                    and finding_id in seen_ids
                ):
                    return

                if finding_id:
                    seen_ids.add(
                        finding_id
                    )

            collected.append(
                normalized
            )

        def append_sequence(
            value: Any,
        ) -> None:
            if not self._is_mapping_sequence(
                value
            ):
                return

            for candidate in value:
                append_candidate(
                    candidate
                )

        family = payload.get(
            "family_audit"
        )

        if isinstance(family, Mapping):
            append_sequence(
                family.get("findings")
            )

        project = payload.get(
            "bim_qa"
        )

        if isinstance(project, Mapping):
            append_sequence(
                project.get("findings")
            )

        combined = payload.get(
            "combined_audit"
        )

        if isinstance(combined, Mapping):
            append_sequence(
                combined.get(
                    "family_findings"
                )
            )
            append_sequence(
                combined.get(
                    "project_findings"
                )
            )
            append_sequence(
                combined.get(
                    "cross_scope_findings"
                )
            )

        # Also support a direct finding collection when the same helper is
        # reused against a narrowed deterministic projection.
        append_sequence(
            payload.get("findings")
        )

        return collected

    def _knowledge_query(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
    ) -> str:
        """
        Build retrieval terms from rule metadata only.

        Observed values, evidence values and uploaded model content are excluded
        from the KnowledgeAgent query by design.
        """

        findings = self._extract_findings(
            payload
        )

        terms: list[str] = []
        seen: set[str] = set()

        for finding in findings:
            for field in (
                "rule_id",
                "title",
                "category",
            ):
                value = finding.get(field)

                if not isinstance(
                    value,
                    str,
                ):
                    continue

                text = value.strip()

                if (
                    not text
                    or text in seen
                ):
                    continue

                seen.add(text)
                terms.append(text)

        product = str(
            envelope.audit_job.product_code
        ).strip()

        if terms:
            return (
                f"BIMAP {product} audit reference knowledge: "
                + " | ".join(terms)
            )

        return (
            f"BIMAP {product} audit reference knowledge"
        )

    def _audit_summary(
        self,
        envelope: SLAIJobEnvelope,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        findings = self._extract_findings(
            payload
        )

        evidence_count = 0

        context = payload.get("context")

        if isinstance(context, Mapping):
            evidence = context.get(
                "evidence"
            )

            if (
                isinstance(
                    evidence,
                    Sequence,
                )
                and not isinstance(
                    evidence,
                    (
                        str,
                        bytes,
                        bytearray,
                    ),
                )
            ):
                evidence_count = len(
                    evidence
                )

        manifests = payload.get(
            "ingestion_manifests"
        )

        manifest_count = 0

        if (
            isinstance(
                manifests,
                Sequence,
            )
            and not isinstance(
                manifests,
                (
                    str,
                    bytes,
                    bytearray,
                ),
            )
        ):
            manifest_count = len(
                manifests
            )

        return {
            "job_id": (
                envelope.audit_job.job_id
            ),
            "order_id": (
                envelope.audit_job.order_id
            ),
            "product_code": (
                envelope.audit_job.product_code
            ),
            "finding_count": len(
                findings
            ),
            "evidence_count": (
                evidence_count
            ),
            "ingestion_manifest_count": (
                manifest_count
            ),
        }

    # ------------------------------------------------------------------
    # Runtime-safe projections
    # ------------------------------------------------------------------

    def _project_prior_outputs(
        self,
        outputs: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[str, ...],
    ]:
        """
        Project prior runtime outputs into JSON-safe values.

        Non-serializable output is omitted rather than coerced to repr(), because
        repr() could leak runtime/object details or create misleading content.
        """

        projected: dict[str, Any] = {}
        omitted: list[str] = []

        for raw_name, raw_value in (
            outputs.items()
        ):
            name = normalize_agent_name(
                raw_name,
                field="prior_outputs.key",
                error_type=(
                    SLAIRuntimeContractError
                ),
            )

            if name in projected:
                raise SLAIRuntimeContractError(
                    "prior_outputs contains "
                    "duplicate normalized agent names.",
                    component=_COMPONENT,
                    operation=(
                        "project_prior_outputs"
                    ),
                    field="prior_outputs",
                    context={"agent": name},
                )

            candidate = raw_value

            to_dict = getattr(
                candidate,
                "to_dict",
                None,
            )

            if callable(to_dict):
                try:
                    candidate = to_dict()
                except Exception:
                    omitted.append(name)
                    continue

            try:
                wrapper = (
                    normalize_json_mapping(
                        {"value": candidate},
                        field=(
                            f"prior_outputs.{name}"
                        ),
                    )
                )
            except Exception:
                omitted.append(name)
                continue

            projected[name] = (
                wrapper["value"]
            )

        return (
            projected,
            tuple(omitted),
        )

    def _integration_context(
        self,
        envelope: SLAIJobEnvelope,
        phase: OrchestrationPhase,
        namespace: str,
        *,
        omitted_prior: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "source": "bimap",
            "job_id": (
                envelope.audit_job.job_id
            ),
            "order_id": (
                envelope.audit_job.order_id
            ),
            "product_code": (
                envelope.audit_job.product_code
            ),
            "correlation_id": (
                envelope.correlation_id
            ),
            "phase": phase.value,
            "shared_memory_namespace": (
                namespace
            ),
            "context_digest": (
                envelope.context_digest
            ),
            "omitted_non_json_prior_outputs": (
                list(omitted_prior)
            ),
        }

    # ------------------------------------------------------------------
    # Contract enforcement
    # ------------------------------------------------------------------

    def _require_phase(
        self,
        agent: str,
        phase: OrchestrationPhase,
    ) -> None:
        allowed = self._ALLOWED_PHASES.get(
            agent
        )

        if (
            allowed is None
            or phase not in allowed
        ):
            raise SLAIRuntimeContractError(
                "SLAI agent was requested in an "
                "unsupported BIMAP orchestration phase.",
                component=_COMPONENT,
                operation="validate_phase",
                field="phase",
                context={
                    "agent": agent,
                    "phase": phase.value,
                    "allowed_phases": (
                        []
                        if allowed is None
                        else [
                            item.value
                            for item in sorted(
                                allowed,
                                key=lambda item:
                                    item.value,
                            )
                        ]
                    ),
                },
            )

    def _raise_explicit_task_required(
        self,
        agent: str,
        phase: OrchestrationPhase,
        envelope: SLAIJobEnvelope,
    ) -> None:
        reason = self._EXPLICIT_ONLY[
            agent
        ]

        raise SLAIRuntimeContractError(
            "Automatic BIMAP task translation is "
            "not available for this SLAI agent. "
            "A verified explicit task override is required.",
            component=_COMPONENT,
            operation="build_task",
            field="task_overrides/task_builder",
            context={
                "agent": agent,
                "phase": phase.value,
                "job_id": (
                    envelope.audit_job.job_id
                ),
                "reason": reason,
            },
        )

    @staticmethod
    def _is_mapping_sequence(
        value: Any,
    ) -> bool:
        return (
            isinstance(
                value,
                Sequence,
            )
            and not isinstance(
                value,
                (
                    str,
                    bytes,
                    bytearray,
                ),
            )
            and all(
                isinstance(item, Mapping)
                for item in value
            )
        )


__all__ = [
    "BIMAPSLAITaskBuilder",
]
