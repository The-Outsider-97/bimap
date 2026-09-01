"""
Translate SLAI-native governance gate outputs into BIMAP governance semantics.

This module is an anti-corruption boundary: SLAI agents may expose different
native decision vocabularies, while BIMAP owns one stable governance domain
(``domain/governance``).  ``SLAIGovernance`` normalizes only decision shapes
that are supported by the current SLAI v2.3 interfaces and the BIMAP design:

- Quality: ``pass | warn | block`` (``verdict`` is the native QualityAgent key);
- Privacy: ``allow | modify | block | escalate``;
- Safety: ``allow | review | block``;
- Evaluation: an explicit boolean ``approved`` when present, otherwise a small
  decision/status vocabulary.

Unknown or missing gate decisions are never interpreted as approval.  They are
represented explicitly and, by default, route the finding to human review.
This preserves BIMAP's evidence-first/fail-safe rule that unresolved conditions
must not silently become customer-facing certainty.

The module does **not**:
- invoke SLAI agents;
- modify deterministic findings or their evidence/provenance;
- redefine finding severity or confidence;
- duplicate ``Review.requires_review``;
- invent contradictory-evidence or inference-only review triggers.  Those facts
  must be detected by higher grounded layers and supplied as reason codes;
- create customer-facing prose or compliance/certification claims.

Dependency direction
--------------------
    SLAI native output mappings
            -> slai/governance.py
            -> domain/governance/decisions.py
            -> domain/governance/review.py

No domain module imports this SLAI adapter module, preserving a one-way graph.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from ..domain.findings.models import Finding
from ..domain.governance.decisions import *
from ..domain.governance.review import Review
from ..domain.utils.domain_errors import DomainError
from ..domain.utils.domain_helpers import ensure_utc_datetime
from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("SLAI Governance Output")
printer = PrettyPrinter()


class GovernanceGate(str, Enum):
    """SLAI governance gates used by BIMAP's commercial release pipeline."""

    QUALITY = "quality"
    PRIVACY = "privacy"
    SAFETY = "safety"
    EVALUATION = "evaluation"


class GateDisposition(str, Enum):
    """Neutral BIMAP interpretation of one SLAI-native gate decision."""

    PASS = "pass"
    MODIFY = "modify"
    WARN = "warn"
    REVIEW = "review"
    BLOCK = "block"
    UNKNOWN = "unknown"

    @property
    def prevents_automatic_release(self) -> bool:
        """Return whether this disposition is not automatically releasable."""

        return self in {
            GateDisposition.REVIEW,
            GateDisposition.BLOCK,
            GateDisposition.UNKNOWN,
        }


@dataclass(frozen=True, slots=True)
class SLAIGateResult:
    """Normalized, evidence-content-free result for one SLAI governance gate."""

    gate: GovernanceGate
    disposition: GateDisposition
    source_token: str | None = None
    reason_codes: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Validating normalized SLAI gate result",
            context={"gate": getattr(self.gate, "value", str(self.gate))},
        )

        gate = (
            self.gate
            if isinstance(self.gate, GovernanceGate)
            else parse_enum(
                GovernanceGate,
                self.gate,
                field="gate",
                error_type=SLAIGovernanceValidationError,
            )
        )
        disposition = (
            self.disposition
            if isinstance(self.disposition, GateDisposition)
            else parse_enum(
                GateDisposition,
                self.disposition,
                field="disposition",
                error_type=SLAIGovernanceValidationError,
            )
        )
        source_token = normalize_decision_token(self.source_token)
        reasons = normalize_text_sequence(
            self.reason_codes,
            field="reason_codes",
            allow_empty=True,
            error_type=SLAIGovernanceValidationError,
        )

        if not isinstance(self.details, Mapping):
            raise SLAIGovernanceValidationError(
                "Gate-result details must be a mapping.",
                component="governance",
                operation="validate_gate_result",
                field="details",
                context={"received_type": type(self.details).__name__},
            )
        # Only bounded operational metadata is retained.  Raw SLAI payloads and
        # privacy-sanitized customer content are deliberately not copied here.
        details = MappingProxyType(safe_log_context(self.details))

        object.__setattr__(self, "gate", gate)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "source_token", source_token)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "details", details)

    @property
    def requires_modified_payload(self) -> bool:
        """Return whether a Privacy ``modify`` decision requires sanitized data."""

        return self.gate is GovernanceGate.PRIVACY and self.disposition is GateDisposition.MODIFY

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready representation without raw gate payload content."""

        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Serializing normalized SLAI gate result",
            context={"gate": self.gate.value},
        )
        return {
            "gate": self.gate.value,
            "disposition": self.disposition.value,
            "source_token": self.source_token,
            "reason_codes": list(self.reason_codes),
            "details": dict(self.details),
            "requires_modified_payload": self.requires_modified_payload,
        }


@dataclass(frozen=True, slots=True)
class SLAIGovernanceResult:
    """BIMAP governance outcome produced from one finding and its SLAI gates."""

    finding_id: str
    decision: GovernanceDecision
    gates: tuple[SLAIGateResult, ...]
    review: Review | None = None

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Validating BIMAP SLAI governance result",
            context={"finding_id": str(self.finding_id)},
        )

        finding_id = require_text(
            self.finding_id,
            field="finding_id",
            error_type=SLAIGovernanceValidationError,
        )
        if not isinstance(self.decision, GovernanceDecision):
            raise SLAIGovernanceValidationError(
                "decision must be a GovernanceDecision instance.",
                component="governance",
                operation="validate_result",
                field="decision",
                context={"received_type": type(self.decision).__name__},
            )
        if self.decision.finding_id != finding_id:
            raise SLAIGovernanceValidationError(
                "Governance decision belongs to a different finding.",
                component="governance",
                operation="validate_result",
                field="decision.finding_id",
                context={
                    "finding_id": finding_id,
                    "decision_finding_id": self.decision.finding_id,
                },
            )

        gates = tuple(self.gates)
        if any(not isinstance(item, SLAIGateResult) for item in gates):
            raise SLAIGovernanceValidationError(
                "gates must contain only SLAIGateResult instances.",
                component="governance",
                operation="validate_result",
                field="gates",
            )
        gate_names = [item.gate.value for item in gates]
        if len(gate_names) != len(set(gate_names)):
            raise SLAIGovernanceValidationError(
                "Governance result contains duplicate gate entries.",
                component="governance",
                operation="validate_result",
                field="gates",
                context={"gates": gate_names},
            )

        if self.review is not None:
            if not isinstance(self.review, Review):
                raise SLAIGovernanceValidationError(
                    "review must be a Review instance or None.",
                    component="governance",
                    operation="validate_result",
                    field="review",
                    context={"received_type": type(self.review).__name__},
                )
            if self.review.finding_id != finding_id:
                raise SLAIGovernanceValidationError(
                    "Governance review belongs to a different finding.",
                    component="governance",
                    operation="validate_result",
                    field="review.finding_id",
                )

        if self.decision.outcome is DecisionOutcome.REVIEW_REQUIRED and self.review is None:
            raise SLAIGovernanceValidationError(
                "A review-required SLAI governance result must carry a Review aggregate.",
                component="governance",
                operation="validate_result",
                field="review",
            )

        object.__setattr__(self, "finding_id", finding_id)
        object.__setattr__(self, "gates", gates)

    @property
    def release_allowed(self) -> bool:
        """Return whether the finding is approved for customer-facing release."""

        return self.decision.outcome.permits_finding_release

    @property
    def review_required(self) -> bool:
        """Return whether human governance review remains mandatory."""

        return self.decision.outcome is DecisionOutcome.REVIEW_REQUIRED

    @property
    def blocked(self) -> bool:
        """Return whether governance explicitly blocks this finding's release."""

        return self.decision.outcome is DecisionOutcome.BLOCKED

    @property
    def requires_modified_payload(self) -> bool:
        """Return whether any gate requires a sanitized/transformed payload."""

        return any(item.requires_modified_payload for item in self.gates)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready governance summary."""

        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Serializing BIMAP SLAI governance result",
            context={"finding_id": self.finding_id},
        )
        return {
            "finding_id": self.finding_id,
            "decision": self.decision.to_dict(),
            "gates": [item.to_dict() for item in self.gates],
            "review": None if self.review is None else self.review.to_dict(),
            "release_allowed": self.release_allowed,
            "review_required": self.review_required,
            "blocked": self.blocked,
            "requires_modified_payload": self.requires_modified_payload,
        }


class SLAIGovernance:
    """
    Normalize SLAI gate outputs and apply BIMAP's release/review semantics.

    Parameters
    ----------
    warning_requires_review:
        Quality ``warn`` is not a hard block in SLAI.  Product policy may
        choose to route warnings to manual review; the default preserves SLAI's
        non-blocking warning semantics while retaining the warning in the gate
        result.
    """

    DEFAULT_REQUIRED_GATES: tuple[GovernanceGate, ...] = (
        GovernanceGate.QUALITY,
        GovernanceGate.PRIVACY,
        GovernanceGate.SAFETY,
        GovernanceGate.EVALUATION,
    )

    __slots__ = ("warning_requires_review",)

    def __init__(
        self,
        *,
        warning_requires_review: bool = False,
    ) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Initializing SLAI governance mapper",
        )
        self.warning_requires_review = require_bool(
            warning_requires_review,
            field="warning_requires_review",
            error_type=SLAIGovernanceValidationError,
        )
        logger.info(
            "SLAI governance mapper initialized: warning_requires_review=%s",
            self.warning_requires_review,
        )

    def normalize_gate_output(
        self,
        gate: GovernanceGate | str,
        payload: Mapping[str, Any],
    ) -> SLAIGateResult:
        """Normalize one supported SLAI native gate mapping."""

        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Normalizing SLAI governance gate output",
            context={"gate": getattr(gate, "value", str(gate))},
        )

        parsed_gate = (
            gate
            if isinstance(gate, GovernanceGate)
            else parse_enum(
                GovernanceGate,
                gate,
                field="gate",
                error_type=SLAIGovernanceValidationError,
            )
        )
        data = require_mapping(
            payload,
            field=f"{parsed_gate.value}_output",
            error_type=SLAIGovernanceValidationError,
        )

        source_key: str | None
        raw_token: Any
        if parsed_gate is GovernanceGate.QUALITY:
            source_key, raw_token = first_present(data, ("verdict", "decision", "status"))
            token = normalize_decision_token(raw_token)
            disposition = self._map_quality(token)
        elif parsed_gate is GovernanceGate.PRIVACY:
            source_key, raw_token = first_present(data, ("decision", "status"))
            token = normalize_decision_token(raw_token)
            disposition = self._map_privacy(token)
            if disposition is GateDisposition.MODIFY and "sanitized_payload" not in data:
                raise SLAIGovernanceMappingError(
                    "Privacy 'modify' decision is missing the SLAI sanitized_payload contract field.",
                    component="governance",
                    operation="normalize_gate_output",
                    field="sanitized_payload",
                    context={"gate": parsed_gate.value},
                )
        elif parsed_gate is GovernanceGate.SAFETY:
            source_key, raw_token = first_present(data, ("decision", "status"))
            token = normalize_decision_token(raw_token)
            disposition = self._map_safety(token)
        else:
            if "approved" in data:
                source_key = "approved"
                approved = data["approved"]
                if not isinstance(approved, bool):
                    raise SLAIGovernanceMappingError(
                        "Evaluation 'approved' must be a boolean when present.",
                        component="governance",
                        operation="normalize_gate_output",
                        field="approved",
                        context={"received_type": type(approved).__name__},
                    )
                token = "approved" if approved else "rejected"
                disposition = GateDisposition.PASS if approved else GateDisposition.BLOCK
            else:
                source_key, raw_token = first_present(
                    data,
                    ("decision", "verdict", "status", "outcome"),
                )
                token = normalize_decision_token(raw_token)
                disposition = self._map_evaluation(token)

        native_reasons = self._extract_reason_codes(data)
        generated_reason = self._generated_gate_reason(parsed_gate, disposition)
        reasons = normalize_text_sequence(
            (generated_reason, *native_reasons),
            field="reason_codes",
            allow_empty=False,
            error_type=SLAIGovernanceValidationError,
        )

        details = {
            "source_key": source_key,
            "native_reason_count": len(native_reasons),
            "sanitized_output_present": (
                parsed_gate is GovernanceGate.PRIVACY and "sanitized_payload" in data
            ),
        }

        result = SLAIGateResult(
            gate=parsed_gate,
            disposition=disposition,
            source_token=token,
            reason_codes=reasons,
            details=details,
        )
        logger.info(
            "SLAI governance gate normalized: gate=%s disposition=%s source_token=%s",
            parsed_gate.value,
            disposition.value,
            token,
        )
        return result

    def evaluate_gates(
        self,
        gate_outputs: Mapping[GovernanceGate | str, Mapping[str, Any]],
        *,
        required_gates: Sequence[GovernanceGate | str] | None = None,
    ) -> tuple[SLAIGateResult, ...]:
        """
        Normalize a complete gate-output set in deterministic gate order.

        A missing required gate becomes an explicit ``UNKNOWN`` result instead
        of approval.  Unknown extra gate names are rejected because silently
        ignoring a governance source would make the release path ambiguous.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Evaluating SLAI governance gate set",
        )
        if not isinstance(gate_outputs, Mapping):
            raise SLAIGovernanceValidationError(
                "gate_outputs must be a mapping.",
                component="governance",
                operation="evaluate_gates",
                field="gate_outputs",
                context={"received_type": type(gate_outputs).__name__},
            )

        normalized_outputs: dict[GovernanceGate, Mapping[str, Any]] = {}
        for raw_gate, raw_payload in gate_outputs.items():
            parsed_gate = (
                raw_gate
                if isinstance(raw_gate, GovernanceGate)
                else parse_enum(
                    GovernanceGate,
                    raw_gate,
                    field="gate_outputs.key",
                    error_type=SLAIGovernanceValidationError,
                )
            )
            if parsed_gate in normalized_outputs:
                raise SLAIGovernanceValidationError(
                    "Duplicate SLAI governance gate after normalization.",
                    component="governance",
                    operation="evaluate_gates",
                    field="gate_outputs",
                    context={"gate": parsed_gate.value},
                )
            normalized_outputs[parsed_gate] = require_mapping(
                raw_payload,
                field=f"gate_outputs.{parsed_gate.value}",
                error_type=SLAIGovernanceValidationError,
            )

        required = self._normalize_gate_sequence(
            self.DEFAULT_REQUIRED_GATES if required_gates is None else required_gates
        )
        required_set = set(required)

        ordered_gates = list(required)
        for gate in self.DEFAULT_REQUIRED_GATES:
            if gate in normalized_outputs and gate not in required_set:
                ordered_gates.append(gate)

        results: list[SLAIGateResult] = []
        for gate in ordered_gates:
            if gate not in normalized_outputs:
                results.append(
                    SLAIGateResult(
                        gate=gate,
                        disposition=GateDisposition.UNKNOWN,
                        source_token=None,
                        reason_codes=(f"SLAI.{gate.value.upper()}.MISSING",),
                        details={"source_key": None, "native_reason_count": 0},
                    )
                )
                continue
            results.append(self.normalize_gate_output(gate, normalized_outputs[gate]))

        return tuple(results)

    def evaluate_finding(
        self,
        finding: Finding,
        gate_outputs: Mapping[GovernanceGate | str, Mapping[str, Any]],
        *,
        confidence_threshold: float,
        required_gates: Sequence[GovernanceGate | str] | None = None,
        additional_review_reasons: Sequence[str] | None = None,
        review_id: str | None = None,
        decision_id: str | None = None,
        requested_at: datetime | str | None = None,
        requested_by: str | None = None,
        decided_at: datetime | str | None = None,
        decided_by: str | None = None,
    ) -> SLAIGovernanceResult:
        """
        Convert SLAI gate results into the canonical BIMAP governance domain.

        ``confidence_threshold`` is mandatory and caller-supplied because BIMAP
        requires a calibrated *product threshold* but the design does not define
        a universal numeric value.  The existing domain method
        ``Review.requires_review`` remains the single implementation of the
        high/critical-severity + low-confidence rule.

        ``additional_review_reasons`` is the explicit handoff for grounded
        conditions the current Finding model does not encode, such as
        contradictory evidence or inference-only support for a material
        recommendation.  This module does not attempt to infer those conditions.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Evaluating SLAI governance for BIMAP finding",
            context={"finding_id": getattr(finding, "finding_id", None)},
        )

        if not isinstance(finding, Finding):
            raise SLAIGovernanceValidationError(
                "finding must be a canonical Finding instance.",
                component="governance",
                operation="evaluate_finding",
                field="finding",
                context={"received_type": type(finding).__name__},
            )

        threshold = normalize_probability(
            confidence_threshold,
            field="confidence_threshold",
            error_type=SLAIGovernanceValidationError,
        )
        assert threshold is not None

        gates = self.evaluate_gates(gate_outputs, required_gates=required_gates)
        review_reasons = list(
            normalize_text_sequence(
                additional_review_reasons or (),
                field="additional_review_reasons",
                allow_empty=True,
                error_type=SLAIGovernanceValidationError,
            )
        )

        try:
            if Review.requires_review(finding, confidence_threshold=threshold):
                review_reasons.append("BIMAP.REVIEW.HIGH_SEVERITY_LOW_CONFIDENCE")
        except DomainError as exc:
            raise SLAIGovernanceValidationError(
                "Unable to evaluate the BIMAP domain review threshold.",
                component="governance",
                operation="evaluate_finding",
                field="confidence_threshold",
                cause=exc,
            ) from exc

        blocking_reasons: list[str] = []
        for gate in gates:
            if gate.disposition is GateDisposition.BLOCK:
                blocking_reasons.extend(gate.reason_codes)
                continue
            if gate.disposition is GateDisposition.REVIEW:
                review_reasons.extend(gate.reason_codes)
                continue
            if gate.disposition is GateDisposition.UNKNOWN:
                review_reasons.extend(gate.reason_codes)
                continue
            if gate.disposition is GateDisposition.WARN and self.warning_requires_review:
                review_reasons.extend(gate.reason_codes)

        blocking_reasons = list(
            normalize_text_sequence(
                blocking_reasons,
                field="blocking_reasons",
                allow_empty=True,
                error_type=SLAIGovernanceValidationError,
            )
        )
        review_reasons = list(
            normalize_text_sequence(
                review_reasons,
                field="review_reasons",
                allow_empty=True,
                error_type=SLAIGovernanceValidationError,
            )
        )

        if blocking_reasons:
            outcome = DecisionOutcome.BLOCKED
            reason_code = blocking_reasons[0]
        elif review_reasons:
            outcome = DecisionOutcome.REVIEW_REQUIRED
            reason_code = review_reasons[0]
        else:
            outcome = DecisionOutcome.APPROVED
            if any(gate.disposition is GateDisposition.MODIFY for gate in gates):
                reason_code = "SLAI.GOVERNANCE.APPROVED_WITH_MODIFICATION"
            elif any(gate.disposition is GateDisposition.WARN for gate in gates):
                reason_code = "SLAI.GOVERNANCE.APPROVED_WITH_WARNING"
            else:
                reason_code = "SLAI.GOVERNANCE.APPROVED"

        decision_timestamp = ensure_utc_datetime(
            decided_at if decided_at is not None else utc_now(),
            field="decided_at",
        )
        decision_identifier = decision_id or generate_identifier("GOV")

        try:
            decision = GovernanceDecision(
                decision_id=decision_identifier,
                finding_id=finding.finding_id,
                outcome=outcome,
                reason_code=reason_code,
                rationale=None,
                decided_at=decision_timestamp,
                decided_by=decided_by,
                is_override=False,
            )

            review: Review | None = None
            if outcome is DecisionOutcome.REVIEW_REQUIRED:
                requested_timestamp = (
                    requested_at if requested_at is not None else decision.decided_at
                )
                review = Review(
                    review_id=review_id or generate_identifier("REV"),
                    finding=finding,
                    reason_codes=review_reasons,
                    requested_at=requested_timestamp,
                    requested_by=requested_by,
                )
                review.add_decision(decision)
        except DomainError as exc:
            raise SLAIGovernanceValidationError(
                "Unable to construct BIMAP governance domain objects from SLAI gate results.",
                component="governance",
                operation="evaluate_finding",
                context={
                    "finding_id": finding.finding_id,
                    "outcome": outcome.value,
                },
                cause=exc,
            ) from exc

        result = SLAIGovernanceResult(
            finding_id=finding.finding_id,
            decision=decision,
            gates=gates,
            review=review,
        )
        logger.info(
            "SLAI governance evaluated: finding_id=%s outcome=%s gate_count=%d review_required=%s",
            finding.finding_id,
            outcome.value,
            len(gates),
            result.review_required,
        )
        return result

    def assert_releasable(self, result: SLAIGovernanceResult) -> None:
        """Raise a structured error unless the finding is explicitly approved."""

        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Asserting BIMAP finding release eligibility",
            context={"finding_id": getattr(result, "finding_id", None)},
        )

        if not isinstance(result, SLAIGovernanceResult):
            raise SLAIGovernanceValidationError(
                "result must be an SLAIGovernanceResult instance.",
                component="governance",
                operation="assert_releasable",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.release_allowed:
            return

        raise SLAIReleaseBlockedError(
            "SLAI/BIMAP governance has not approved this finding for release.",
            component="governance",
            operation="assert_releasable",
            context={
                "finding_id": result.finding_id,
                "outcome": result.decision.outcome.value,
                "reason_code": result.decision.reason_code,
            },
        )

    @staticmethod
    def _map_quality(token: str | None) -> GateDisposition:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Mapping SLAI Quality decision",
            context={"token": token},
        )
        normalized = (token or "").strip().lower()
        mapping = {
            "pass": GateDisposition.PASS,
            "warn": GateDisposition.WARN,
            "warning": GateDisposition.WARN,
            "block": GateDisposition.BLOCK,
            "blocked": GateDisposition.BLOCK,
        }
        return mapping.get(normalized, GateDisposition.UNKNOWN)

    @staticmethod
    def _map_privacy(token: str | None) -> GateDisposition:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Mapping SLAI Privacy decision",
            context={"token": token},
        )
        normalized = (token or "").strip().lower()
        mapping = {
            "allow": GateDisposition.PASS,
            "modify": GateDisposition.MODIFY,
            "block": GateDisposition.BLOCK,
            "blocked": GateDisposition.BLOCK,
            "escalate": GateDisposition.REVIEW,
        }
        return mapping.get(normalized, GateDisposition.UNKNOWN)

    @staticmethod
    def _map_safety(token: str | None) -> GateDisposition:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Mapping SLAI Safety decision",
            context={"token": token},
        )
        normalized = (token or "").strip().lower()
        mapping = {
            "allow": GateDisposition.PASS,
            "review": GateDisposition.REVIEW,
            "block": GateDisposition.BLOCK,
            "blocked": GateDisposition.BLOCK,
        }
        return mapping.get(normalized, GateDisposition.UNKNOWN)

    @staticmethod
    def _map_evaluation(token: str | None) -> GateDisposition:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Mapping SLAI Evaluation decision",
            context={"token": token},
        )
        normalized = (token or "").strip().lower()
        mapping = {
            "pass": GateDisposition.PASS,
            "approved": GateDisposition.PASS,
            "warn": GateDisposition.WARN,
            "warning": GateDisposition.WARN,
            "degraded": GateDisposition.REVIEW,
            "review": GateDisposition.REVIEW,
            "escalate": GateDisposition.REVIEW,
            "block": GateDisposition.BLOCK,
            "blocked": GateDisposition.BLOCK,
            "fail": GateDisposition.BLOCK,
            "failed": GateDisposition.BLOCK,
            "rejected": GateDisposition.BLOCK,
        }
        return mapping.get(normalized, GateDisposition.UNKNOWN)

    @staticmethod
    def _generated_gate_reason(
        gate: GovernanceGate,
        disposition: GateDisposition,
    ) -> str:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Generating stable SLAI gate reason code",
            context={"gate": gate.value, "disposition": disposition.value},
        )
        return f"SLAI.{gate.value.upper()}.{disposition.value.upper()}"

    @staticmethod
    def _extract_reason_codes(payload: Mapping[str, Any]) -> tuple[str, ...]:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Extracting SLAI governance reason codes",
        )

        collected: list[str] = []
        if "reason_code" in payload and payload["reason_code"] is not None:
            collected.append(
                require_text(
                    payload["reason_code"],
                    field="reason_code",
                    error_type=SLAIGovernanceMappingError,
                )
            )

        for key in ("reason_codes", "blockers"):
            if key not in payload or payload[key] is None:
                continue
            raw = payload[key]
            if isinstance(raw, str):
                values: Sequence[str] = (raw,)
            elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
                values = raw  # type: ignore[assignment]
            else:
                raise SLAIGovernanceMappingError(
                    f"{key} must be a string or sequence of strings.",
                    component="governance",
                    operation="extract_reason_codes",
                    field=key,
                    context={"received_type": type(raw).__name__},
                )
            collected.extend(
                normalize_text_sequence(
                    values,
                    field=key,
                    allow_empty=True,
                    error_type=SLAIGovernanceMappingError,
                )
            )

        return normalize_text_sequence(
            collected,
            field="reason_codes",
            allow_empty=True,
            error_type=SLAIGovernanceMappingError,
        )

    @staticmethod
    def _normalize_gate_sequence(
        values: Sequence[GovernanceGate | str],
    ) -> tuple[GovernanceGate, ...]:
        announce_method_start(
            printer,
            logger,
            "SLAI GOVERNANCE",
            "Normalizing required SLAI governance gates",
        )

        if isinstance(values, (str, bytes, bytearray)):
            raise SLAIGovernanceValidationError(
                "required_gates must be a sequence, not one string.",
                component="governance",
                operation="normalize_gate_sequence",
                field="required_gates",
            )
        normalized: list[GovernanceGate] = []
        seen: set[GovernanceGate] = set()
        for value in values:
            gate = (
                value
                if isinstance(value, GovernanceGate)
                else parse_enum(
                    GovernanceGate,
                    value,
                    field="required_gates[]",
                    error_type=SLAIGovernanceValidationError,
                )
            )
            if gate not in seen:
                seen.add(gate)
                normalized.append(gate)
        if not normalized:
            raise SLAIGovernanceValidationError(
                "At least one governance gate must be required.",
                component="governance",
                operation="normalize_gate_sequence",
                field="required_gates",
            )
        return tuple(normalized)


__all__ = [
    "GovernanceGate",
    "GateDisposition",
    "SLAIGateResult",
    "SLAIGovernanceResult",
    "SLAIGovernance",
]