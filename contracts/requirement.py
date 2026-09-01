"""
Versioned external BIMAP requirement contract.

The Requirement-Evidence Matrix is the core BIM QA artifact described by the
BIMAP implementation specification. Each requirement has a stable identifier,
normalized/source requirement text, evidence references, an assessment,
automation type, confidence, impact, and recommended action.

This module also owns the two shared external assessment vocabularies used by
both requirement and finding contracts:

``AutomationType``
    deterministic / inferred / manual-review-required

``AssessmentStatus``
    pass / warn / fail / unknown / not_applicable

They are defined once here to prevent duplicated or drifting string vocabularies
between requirement.py and finding.py.

The current ``domain/requirements/models.py`` remains a scaffold rather than a
stable canonical requirement model. This contract therefore deliberately does
not invent a domain conversion API. A domain conversion should be added only
when that domain model is implemented and stabilized.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..domain.utils.domain_errors import DomainError
from ..domain.utils.domain_helpers import *
from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from .versions import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Requirement")
printer = PrettyPrinter()

_CONTRACT = ContractName.REQUIREMENT.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer requirement content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "requirement_contract_method_start", "action": action})


def _normalize_text(value: Any, *, field: str) -> str:
    try:
        return require_text(value, field=field)
    except DomainError as exc:
        raise ContractValidationError(
            "Requirement contract contains invalid text.",
            contract=_CONTRACT,
            field=field,
            cause=exc,
        ) from exc


def _normalize_confidence(value: Any) -> float:
    try:
        normalized = normalize_probability(value, field="confidence")
    except DomainError as exc:
        raise ContractValidationError(
            "Requirement confidence must be a finite value between 0 and 1.",
            contract=_CONTRACT,
            field="confidence",
            cause=exc,
        ) from exc
    assert normalized is not None
    return normalized


class AutomationType(str, Enum):
    """Canonical external automation classification shared by BIMAP assessments."""

    DETERMINISTIC = "deterministic"
    INFERRED = "inferred"
    MANUAL_REVIEW_REQUIRED = "manual-review-required"

    @classmethod
    def parse(
        cls,
        value: Any,
        *,
        contract: str = _CONTRACT,
        field: str = "automation_type",
    ) -> "AutomationType":
        """Normalize an automation classification into the canonical enum."""
        _announce("Parsing automation type")
        if isinstance(value, cls):
            return value

        try:
            normalized = require_text(value, field=field).lower()
        except DomainError as exc:
            raise ContractValidationError(
                "Automation type must be non-empty canonical text.",
                contract=contract,
                field=field,
                cause=exc,
            ) from exc

        try:
            return cls(normalized)
        except ValueError as exc:
            raise ContractValidationError(
                "Unsupported BIMAP automation type.",
                contract=contract,
                field=field,
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc

    def __str__(self) -> str:
        return self.value


class AssessmentStatus(str, Enum):
    """Canonical requirement/finding assessment status vocabulary."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"

    @classmethod
    def parse(
        cls,
        value: Any,
        *,
        contract: str = _CONTRACT,
        field: str = "assessment",
    ) -> "AssessmentStatus":
        """Normalize a status into the canonical external assessment enum."""
        _announce("Parsing assessment status")
        if isinstance(value, cls):
            return value

        try:
            normalized = require_text(value, field=field).lower()
        except DomainError as exc:
            raise ContractValidationError(
                "Assessment status must be non-empty canonical text.",
                contract=contract,
                field=field,
                cause=exc,
            ) from exc

        try:
            return cls(normalized)
        except ValueError as exc:
            raise ContractValidationError(
                "Unsupported BIMAP assessment status.",
                contract=contract,
                field=field,
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RequirementContract:
    """Stable external Requirement-Evidence Matrix row representation."""

    requirement_id: str
    source_requirement: str
    assessment: AssessmentStatus
    automation_type: AutomationType
    confidence: float
    impact: str
    recommended_action: str
    evidence_refs: tuple[str, ...] = ()
    schema_version: str = REQUIREMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating requirement contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        requirement_id = _normalize_text(self.requirement_id, field="requirement_id")
        source_requirement = _normalize_text(
            self.source_requirement,
            field="source_requirement",
        )
        assessment = AssessmentStatus.parse(self.assessment)
        automation_type = AutomationType.parse(self.automation_type)
        confidence = _normalize_confidence(self.confidence)
        impact = _normalize_text(self.impact, field="impact")
        recommended_action = _normalize_text(
            self.recommended_action,
            field="recommended_action",
        )

        try:
            evidence_refs = stable_unique_text(
                self.evidence_refs,
                field="evidence_refs",
            )
        except (DomainError, TypeError) as exc:
            raise ContractValidationError(
                "Requirement evidence references are invalid.",
                contract=_CONTRACT,
                field="evidence_refs",
                cause=exc,
            ) from exc

        if assessment in {
            AssessmentStatus.PASS,
            AssessmentStatus.WARN,
            AssessmentStatus.FAIL,
        } and not evidence_refs:
            raise ContractIntegrityError(
                "A pass/warn/fail requirement assessment must reference evidence.",
                contract=_CONTRACT,
                field="evidence_refs",
                context={"assessment": assessment.value},
            )

        object.__setattr__(self, "schema_version", str(self.schema_version).strip())
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "source_requirement", source_requirement)
        object.__setattr__(self, "assessment", assessment)
        object.__setattr__(self, "automation_type", automation_type)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "impact", impact)
        object.__setattr__(self, "recommended_action", recommended_action)
        object.__setattr__(self, "evidence_refs", evidence_refs)

        logger.debug(
            {
                "event": "requirement_contract_validated",
                "requirement_id": self.requirement_id,
                "assessment": assessment.value,
                "automation_type": automation_type.value,
                "evidence_count": len(evidence_refs),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned external requirement representation."""
        _announce("Serializing requirement contract")
        return {
            "schema_version": self.schema_version,
            "requirement_id": self.requirement_id,
            "source_requirement": self.source_requirement,
            "evidence_refs": list(self.evidence_refs),
            "assessment": self.assessment.value,
            "automation_type": self.automation_type.value,
            "confidence": self.confidence,
            "impact": self.impact,
            "recommended_action": self.recommended_action,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the requirement using BIMAP canonical JSON rules."""
        _announce("Encoding requirement contract JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RequirementContract":
        """Parse a strict versioned Requirement-Evidence Matrix row."""
        _announce("Deserializing requirement contract")
        data = validate_contract_fields(
            payload,
            required=(
                "schema_version",
                "requirement_id",
                "source_requirement",
                "evidence_refs",
                "assessment",
                "automation_type",
                "confidence",
                "impact",
                "recommended_action",
            ),
            contract=_CONTRACT,
        )
        return cls(
            schema_version=data["schema_version"],
            requirement_id=data["requirement_id"],
            source_requirement=data["source_requirement"],
            evidence_refs=data["evidence_refs"] or (),
            assessment=data["assessment"],
            automation_type=data["automation_type"],
            confidence=data["confidence"],
            impact=data["impact"],
            recommended_action=data["recommended_action"],
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "RequirementContract":
        """Decode canonical JSON and validate a requirement contract."""
        _announce("Decoding requirement contract JSON")
        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Requirement JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


# Backward-compatible name retained from the initial scaffold.
ContractsRequirement = RequirementContract


__all__ = [
    "AutomationType",
    "AssessmentStatus",
    "RequirementContract",
    "ContractsRequirement",
]


if __name__ == "__main__":
    print("\n=== Running Requirement Contract Self-Test ===\n")
    printer.status("TEST", "Requirement contract module initialized", "info")

    contract = RequirementContract(
        requirement_id="REQ-BEP-001",
        source_requirement="FireRating parameter shall be present.",
        evidence_refs=("EV-0001",),
        assessment=AssessmentStatus.FAIL,
        automation_type=AutomationType.DETERMINISTIC,
        confidence=1.0,
        impact="Required information is absent from the assessed evidence.",
        recommended_action="Provide the required parameter and re-run the audit.",
    )
    assert RequirementContract.from_json(contract.to_json()) == contract
    printer.status("PASS", "Requirement contract round trip", "success")

    print("\n=== Test ran successfully ===\n")