"""
Map SLAI orchestration outputs back into BIMAP-owned representations.

The result mapper is intentionally conservative.  SLAI outputs are
supplemental to BIMAP's deterministic evidence/rule results; this module does
not rewrite deterministic finding identity, rule identity, observed/expected
values, or evidence references.  Authoritative ``FindingContract`` instances
supplied by the audit/application layer are returned unchanged.

The mapper performs three bounded tasks:

1. normalize orchestration invocation outputs into explicitly serializable or
   explicitly opaque BIMAP integration records;
2. convert SLAI Quality/Privacy/Safety/Evaluation outputs through the existing
   ``SLAIGovernance`` gate mapper;
3. compose those results with unchanged authoritative finding contracts.

It deliberately does not manufacture new BIMAP findings from arbitrary language
or reasoning text.  A future inferred-finding extractor must have its own
validated contract and evidence policy rather than silently treating free-form
SLAI output as a deterministic finding.

Dependency direction
--------------------

``contracts/domain + governance + orchestration -> result_mapper``

``result_mapper.py`` must not import ``adapter.py``.  ``orchestration.py`` does
not import this module, preventing a reverse dependency.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from .utils.slai_errors import SLAIResultMappingError
from .utils.slai_helpers import *
from ..contracts.finding import FindingContract
from .governance import *
from .orchestration import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("SLAI Result Mapper")
printer = PrettyPrinter()


@dataclass(frozen=True, slots=True)
class MappedAgentOutput:
    """BIMAP-owned projection of one SLAI invocation output."""

    agent: str
    phase: str
    succeeded: bool
    output_type: str | None
    serializable: bool
    payload: Any = None
    error: Mapping[str, Any] | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Validating mapped SLAI agent output",
            context={"agent": str(self.agent), "phase": str(self.phase)},
        )
        agent = normalize_agent_name(
            self.agent,
            field="agent",
            error_type=SLAIResultMappingError,
        )
        phase = require_text(
            self.phase,
            field="phase",
            error_type=SLAIResultMappingError,
        )
        succeeded = require_bool(
            self.succeeded,
            field="succeeded",
            error_type=SLAIResultMappingError,
        )
        serializable = require_bool(
            self.serializable,
            field="serializable",
            error_type=SLAIResultMappingError,
        )
        output_type = None
        if self.output_type is not None:
            output_type = require_text(
                self.output_type,
                field="output_type",
                error_type=SLAIResultMappingError,
            )
        note = None
        if self.note is not None:
            note = require_text(
                self.note,
                field="note",
                error_type=SLAIResultMappingError,
            )
        error: Mapping[str, Any] | None = None
        if self.error is not None:
            raw_error = dict(require_mapping(
                self.error,
                field="error",
                error_type=SLAIResultMappingError,
            ))
            try:
                normalized_error = normalize_json_mapping(raw_error, field="error")
            except Exception as exc:
                raise SLAIResultMappingError(
                    "Mapped SLAI error metadata is not JSON-safe.",
                    component="result_mapper",
                    operation="validate_mapped_output",
                    field="error",
                    cause=exc,
                ) from exc
            error = MappingProxyType(normalized_error)

        payload = self.payload
        if not serializable and payload is not None:
            raise SLAIResultMappingError(
                "Opaque mapped SLAI outputs must not carry an unvalidated payload.",
                component="result_mapper",
                operation="validate_mapped_output",
                field="payload",
                context={"agent": agent, "phase": phase},
            )
        if serializable:
            try:
                wrapper = normalize_json_mapping({"value": payload}, field="payload")
            except Exception as exc:
                raise SLAIResultMappingError(
                    "Mapped SLAI output declared serializable but is not JSON-safe.",
                    component="result_mapper",
                    operation="validate_mapped_output",
                    field="payload",
                    context={"agent": agent, "phase": phase},
                    cause=exc,
                ) from exc
            payload = thaw_json_value(wrapper["value"])

        object.__setattr__(self, "agent", agent)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "succeeded", succeeded)
        object.__setattr__(self, "serializable", serializable)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "output_type", output_type)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "note", note)

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-ready mapped output record."""

        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Serializing mapped SLAI agent output",
            context={"agent": self.agent, "phase": self.phase},
        )
        return {
            "agent": self.agent,
            "phase": self.phase,
            "succeeded": self.succeeded,
            "output_type": self.output_type,
            "serializable": self.serializable,
            "payload": self.payload,
            "error": None if self.error is None else dict(self.error),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class SLAIMappedResult:
    """BIMAP-owned result of one completed SLAI orchestration run."""

    job_id: str
    order_id: str
    correlation_id: str
    authoritative_findings: tuple[FindingContract, ...]
    governance_gates: tuple[SLAIGateResult, ...]
    agent_outputs: tuple[MappedAgentOutput, ...]
    started_at: datetime
    completed_at: datetime
    terminated_early: bool = False
    termination_reason: str | None = None
    privacy_sanitized_payload: Any = None
    mapping_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Validating BIMAP SLAI mapped result",
            context={"job_id": str(self.job_id)},
        )
        job_id = require_text(
            self.job_id,
            field="job_id",
            error_type=SLAIResultMappingError,
        )
        order_id = require_text(
            self.order_id,
            field="order_id",
            error_type=SLAIResultMappingError,
        )
        correlation_id = require_text(
            self.correlation_id,
            field="correlation_id",
            error_type=SLAIResultMappingError,
        )

        findings = tuple(self.authoritative_findings)
        if any(not isinstance(item, FindingContract) for item in findings):
            raise SLAIResultMappingError(
                "authoritative_findings must contain FindingContract instances only.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="authoritative_findings",
            )
        finding_ids = [item.finding_id for item in findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise SLAIResultMappingError(
                "authoritative_findings contains duplicate finding identifiers.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="authoritative_findings",
                context={"finding_ids": finding_ids},
            )

        gates = tuple(self.governance_gates)
        if any(not isinstance(item, SLAIGateResult) for item in gates):
            raise SLAIResultMappingError(
                "governance_gates must contain SLAIGateResult instances only.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="governance_gates",
            )
        gate_names = [item.gate.value for item in gates]
        if len(gate_names) != len(set(gate_names)):
            raise SLAIResultMappingError(
                "governance_gates contains duplicate gate entries.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="governance_gates",
                context={"gates": gate_names},
            )

        outputs = tuple(self.agent_outputs)
        if any(not isinstance(item, MappedAgentOutput) for item in outputs):
            raise SLAIResultMappingError(
                "agent_outputs must contain MappedAgentOutput instances only.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="agent_outputs",
            )
        output_keys = [(item.phase, item.agent) for item in outputs]
        if len(output_keys) != len(set(output_keys)):
            raise SLAIResultMappingError(
                "agent_outputs contains duplicate phase/agent invocation projections.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="agent_outputs",
                context={"duplicates_possible": True},
            )

        started_at = ensure_utc_datetime(
            self.started_at,
            field="started_at",
            error_type=SLAIResultMappingError,
        )
        completed_at = ensure_utc_datetime(
            self.completed_at,
            field="completed_at",
            error_type=SLAIResultMappingError,
        )
        if completed_at < started_at:
            raise SLAIResultMappingError(
                "Mapped result completion cannot predate start.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="completed_at",
            )
        terminated_early = require_bool(
            self.terminated_early,
            field="terminated_early",
            error_type=SLAIResultMappingError,
        )
        termination_reason = None
        if self.termination_reason is not None:
            termination_reason = require_text(
                self.termination_reason,
                field="termination_reason",
                error_type=SLAIResultMappingError,
            )
        if terminated_early and termination_reason is None:
            raise SLAIResultMappingError(
                "Early-terminated mapped results require termination_reason.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="termination_reason",
            )
        if not terminated_early and termination_reason is not None:
            raise SLAIResultMappingError(
                "termination_reason must be absent when terminated_early is false.",
                component="result_mapper",
                operation="validate_mapped_result",
                field="termination_reason",
            )

        sanitized_payload = self.privacy_sanitized_payload
        if sanitized_payload is not None:
            try:
                wrapper = normalize_json_mapping(
                    {"value": sanitized_payload},
                    field="privacy_sanitized_payload",
                )
            except Exception as exc:
                raise SLAIResultMappingError(
                    "privacy_sanitized_payload must be JSON-safe.",
                    component="result_mapper",
                    operation="validate_mapped_result",
                    field="privacy_sanitized_payload",
                    cause=exc,
                ) from exc
            sanitized_payload = thaw_json_value(wrapper["value"])

        warnings = normalize_text_sequence(
            self.mapping_warnings,
            field="mapping_warnings",
            allow_empty=True,
            error_type=SLAIResultMappingError,
        )

        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "authoritative_findings", findings)
        object.__setattr__(self, "governance_gates", gates)
        object.__setattr__(self, "agent_outputs", outputs)
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "completed_at", completed_at)
        object.__setattr__(self, "terminated_early", terminated_early)
        object.__setattr__(self, "termination_reason", termination_reason)
        object.__setattr__(self, "privacy_sanitized_payload", sanitized_payload)
        object.__setattr__(self, "mapping_warnings", warnings)

    @property
    def gate_blocked(self) -> bool:
        """Return whether any normalized job-level SLAI gate explicitly blocks."""

        return any(item.disposition is GateDisposition.BLOCK for item in self.governance_gates)

    @property
    def gate_review_required(self) -> bool:
        """Return whether any job-level gate is review/unknown and therefore not auto-clear."""

        return any(
            item.disposition in {GateDisposition.REVIEW, GateDisposition.UNKNOWN}
            for item in self.governance_gates
        )

    @property
    def requires_modified_payload(self) -> bool:
        """Return whether Privacy requested a sanitized/modified payload."""

        return any(item.requires_modified_payload for item in self.governance_gates)

    @property
    def automatic_release_allowed(self) -> bool:
        """Return whether job-level SLAI gates contain no block/review/unknown state."""

        if self.terminated_early:
            return False
        return not any(
            item.disposition.prevents_automatic_release
            for item in self.governance_gates
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the full JSON-ready BIMAP SLAI mapped result."""

        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Serializing BIMAP SLAI mapped result",
            context={"job_id": self.job_id},
        )
        return {
            "job_id": self.job_id,
            "order_id": self.order_id,
            "correlation_id": self.correlation_id,
            "authoritative_findings": [item.to_dict() for item in self.authoritative_findings],
            "governance_gates": [item.to_dict() for item in self.governance_gates],
            "agent_outputs": [item.to_dict() for item in self.agent_outputs],
            "started_at": format_utc_datetime(self.started_at),
            "completed_at": format_utc_datetime(self.completed_at),
            "terminated_early": self.terminated_early,
            "termination_reason": self.termination_reason,
            "privacy_sanitized_payload": self.privacy_sanitized_payload,
            "mapping_warnings": list(self.mapping_warnings),
            "gate_blocked": self.gate_blocked,
            "gate_review_required": self.gate_review_required,
            "requires_modified_payload": self.requires_modified_payload,
            "automatic_release_allowed": self.automatic_release_allowed,
        }


class SLAIResultMapper:
    """Conservative SLAI -> BIMAP integration-result mapper."""

    def __init__(
        self,
        *,
        governance: SLAIGovernance | None = None,
        required_gates: Sequence[GovernanceGate | str] | None = None,
        strict_supplemental_outputs: bool = False,
    ) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Initializing BIMAP SLAI result mapper",
        )
        self.governance = governance if governance is not None else SLAIGovernance()
        if not isinstance(self.governance, SLAIGovernance):
            raise SLAIResultMappingError(
                "governance must be an SLAIGovernance instance.",
                component="result_mapper",
                operation="initialize",
                field="governance",
            )
        self.required_gates = None if required_gates is None else tuple(required_gates)
        self.strict_supplemental_outputs = require_bool(
            strict_supplemental_outputs,
            field="strict_supplemental_outputs",
            error_type=SLAIResultMappingError,
        )
        logger.info(
            "BIMAP SLAI result mapper initialized: strict_supplemental_outputs=%s",
            self.strict_supplemental_outputs,
        )

    def map_result(
        self,
        orchestration_result: SLAIOrchestrationResult,
        *,
        authoritative_findings: Sequence[FindingContract] = (),
    ) -> SLAIMappedResult:
        """
        Map one orchestration result without altering authoritative findings.

        The supplied ``FindingContract`` objects are used directly.  No SLAI
        agent output is allowed to overwrite deterministic ``finding_id``,
        ``rule_id``, ``observed_value``, ``expected_value``, or ``evidence_refs``.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Mapping SLAI orchestration result into BIMAP representation",
            context={"job_id": getattr(orchestration_result, "job_id", None)},
        )
        if not isinstance(orchestration_result, SLAIOrchestrationResult):
            raise SLAIResultMappingError(
                "orchestration_result must be an SLAIOrchestrationResult instance.",
                component="result_mapper",
                operation="map_result",
                field="orchestration_result",
                context={"received_type": type(orchestration_result).__name__},
            )

        findings = self._validate_authoritative_findings(authoritative_findings)

        try:
            gates = self.governance.evaluate_gates(
                orchestration_result.gate_outputs,
                required_gates=self.required_gates,
            )
        except Exception as exc:
            if isinstance(exc, SLAIResultMappingError):
                raise
            raise SLAIResultMappingError(
                "Unable to normalize SLAI governance gate outputs.",
                component="result_mapper",
                operation="map_governance",
                context={"job_id": orchestration_result.job_id},
                cause=exc,
            ) from exc

        mapped_outputs: list[MappedAgentOutput] = []
        warnings: list[str] = []
        for invocation in orchestration_result.invocations:
            mapped, warning = self._map_invocation(invocation)
            mapped_outputs.append(mapped)
            if warning is not None:
                warnings.append(warning)

        if orchestration_result.terminated_early:
            warnings.append(
                f"orchestration_terminated_early:{orchestration_result.termination_reason}"
            )

        sanitized_payload = None
        if orchestration_result.privacy_sanitized_payload is not None:
            sanitized_payload, serializable = self._project_json_value(
                orchestration_result.privacy_sanitized_payload,
                field="privacy_sanitized_payload",
            )
            if not serializable:
                raise SLAIResultMappingError(
                    "PrivacyAgent produced a modified egress payload that cannot be represented safely by BIMAP.",
                    component="result_mapper",
                    operation="map_privacy_payload",
                    field="privacy_sanitized_payload",
                    context={"job_id": orchestration_result.job_id},
                )

        result = SLAIMappedResult(
            job_id=orchestration_result.job_id,
            order_id=orchestration_result.order_id,
            correlation_id=orchestration_result.correlation_id,
            authoritative_findings=findings,
            governance_gates=gates,
            agent_outputs=tuple(mapped_outputs),
            started_at=orchestration_result.started_at,
            completed_at=orchestration_result.completed_at,
            terminated_early=orchestration_result.terminated_early,
            termination_reason=orchestration_result.termination_reason,
            privacy_sanitized_payload=sanitized_payload,
            mapping_warnings=tuple(warnings),
        )
        logger.info(
            "SLAI result mapped: job_id=%s findings=%d gates=%d outputs=%d warnings=%d",
            result.job_id,
            len(result.authoritative_findings),
            len(result.governance_gates),
            len(result.agent_outputs),
            len(result.mapping_warnings),
        )
        return result

    # Backward-compatible semantic alias for callers that used the scaffold's
    # ``process_job`` naming before the mapper contract was implemented.
    def process_job(
        self,
        orchestration_result: SLAIOrchestrationResult,
        *,
        authoritative_findings: Sequence[FindingContract] = (),
    ) -> SLAIMappedResult:
        """Alias for :meth:`map_result`."""

        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Processing SLAI orchestration result",
            context={"job_id": getattr(orchestration_result, "job_id", None)},
        )
        return self.map_result(
            orchestration_result,
            authoritative_findings=authoritative_findings,
        )

    def _validate_authoritative_findings(self, findings: Sequence[FindingContract]) -> tuple[FindingContract, ...]:
        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Validating authoritative BIMAP finding contracts",
        )
        if isinstance(findings, (str, bytes, bytearray)):
            raise SLAIResultMappingError(
                "authoritative_findings must be a sequence of FindingContract values.",
                component="result_mapper",
                operation="validate_authoritative_findings",
                field="authoritative_findings",
            )
        result = tuple(findings)
        if any(not isinstance(item, FindingContract) for item in result):
            raise SLAIResultMappingError(
                "authoritative_findings contains a non-FindingContract value.",
                component="result_mapper",
                operation="validate_authoritative_findings",
                field="authoritative_findings",
            )
        ids = [item.finding_id for item in result]
        if len(ids) != len(set(ids)):
            raise SLAIResultMappingError(
                "authoritative_findings contains duplicate finding IDs.",
                component="result_mapper",
                operation="validate_authoritative_findings",
                context={"finding_ids": ids},
            )
        return result

    def _map_invocation(self, invocation: AgentInvocationRecord) -> tuple[MappedAgentOutput, str | None]:
        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Mapping one SLAI invocation output",
            context={"agent": getattr(invocation, "agent", None)},
        )
        if not isinstance(invocation, AgentInvocationRecord):
            raise SLAIResultMappingError(
                "Invocation must be an AgentInvocationRecord.",
                component="result_mapper",
                operation="map_invocation",
                field="invocation",
            )

        output_type = type(invocation.output).__name__ if invocation.output is not None else None
        payload, serializable = self._project_json_value(
            invocation.output,
            field=f"agent_outputs.{invocation.phase.value}.{invocation.agent}",
        )
        warning: str | None = None
        note: str | None = None
        if not serializable and invocation.output is not None:
            note = f"opaque_non_json_output:{output_type}"
            warning = f"{invocation.phase.value}:{invocation.agent}:{note}"
            if self.strict_supplemental_outputs:
                raise SLAIResultMappingError(
                    "SLAI supplemental output is not JSON-safe and strict mapping is enabled.",
                    component="result_mapper",
                    operation="map_invocation",
                    context={
                        "agent": invocation.agent,
                        "phase": invocation.phase.value,
                        "output_type": output_type,
                    },
                )

        mapped = MappedAgentOutput(
            agent=invocation.agent,
            phase=invocation.phase.value,
            succeeded=invocation.succeeded,
            output_type=output_type,
            serializable=serializable,
            payload=payload if serializable else None,
            error=None if invocation.error is None else dict(invocation.error),
            note=note,
        )
        return mapped, warning

    def _project_json_value(self, value: Any, *, field: str) -> tuple[Any, bool]:
        announce_method_start(
            printer,
            logger,
            "SLAI RESULT",
            "Projecting SLAI output into JSON-safe BIMAP value",
            context={"field": field},
        )
        if value is None:
            return None, True

        candidate = value
        to_dict = getattr(candidate, "to_dict", None)
        if callable(to_dict):
            try:
                candidate = to_dict()
            except Exception:
                return None, False

        try:
            wrapper = normalize_json_mapping(
                {"value": candidate},
                field=field,
            )
        except Exception:
            return None, False
        return thaw_json_value(wrapper["value"]), True


__all__ = [
    "MappedAgentOutput",
    "SLAIMappedResult",
    "SLAIResultMapper",
]

