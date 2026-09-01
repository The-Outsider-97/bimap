"""
Versioned external BIMAP finding contract.

The contract follows the BIMAP finding/evidence specification: every finding
has a stable identifier and rule identifier, scope, automation type, severity,
confidence, status, observed/expected state, evidence references, explanation,
remediation, and verification method. The broader RFA finding specification
also requires a title and category, which are included here.

Severity and confidence remain separate axes. Severity is normalized using the
canonical domain ``Severity`` value object; confidence is normalized using the
canonical domain ``Confidence`` value object. The contract does not collapse
those values into a single score.

``AutomationType`` and ``AssessmentStatus`` are imported from requirement.py so
both external contracts share one authoritative vocabulary rather than
redefining the same values.

The current canonical domain ``Finding`` does not yet carry all fields required
by the external BIMAP finding schema (for example rule_id, scope,
automation_type, status, observed/expected values, remediation, verification
method, and evidence-id references). Automatic domain round-trip methods are
therefore intentionally omitted until the domain model is expanded or a
separate mapping policy is defined. This avoids fabricating missing semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from .requirement import *
from .versions import *
from ..domain.findings.confidence import Confidence
from ..domain.findings.severity import Severity
from ..domain.utils.domain_errors import DomainError
from ..domain.utils.domain_helpers import require_text, stable_unique_text
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Finding")
printer = PrettyPrinter()

_CONTRACT = ContractName.FINDING.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer evidence content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "finding_contract_method_start", "action": action})


def _normalize_text(value: Any, *, field: str) -> str:
    try:
        return require_text(value, field=field)
    except DomainError as exc:
        raise ContractValidationError(
            "Finding contract contains invalid text.",
            contract=_CONTRACT,
            field=field,
            cause=exc,
        ) from exc


class FindingScope(str, Enum):
    """Canonical placement scope for a BIMAP finding."""

    FAMILY = "family"
    PROJECT = "project"
    CROSS_SCOPE = "cross-scope"

    @classmethod
    def parse(cls, value: Any) -> "FindingScope":
        """Normalize finding scope into the canonical external enum."""
        _announce("Parsing finding scope")
        if isinstance(value, cls):
            return value

        normalized = _normalize_text(value, field="scope").lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ContractValidationError(
                "Unsupported BIMAP finding scope.",
                contract=_CONTRACT,
                field="scope",
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class FindingContract:
    """Stable externally serializable BIMAP finding representation."""

    finding_id: str
    scope: FindingScope | str
    rule_id: str
    title: str
    category: str
    automation_type: AutomationType | str
    severity: Severity | str
    confidence: Confidence | float
    status: AssessmentStatus | str
    observed_value: Any
    expected_value: Any
    evidence_refs: tuple[str, ...]
    explanation: str
    remediation: str
    verification_method: str
    schema_version: str = FINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating finding contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        finding_id = _normalize_text(self.finding_id, field="finding_id")
        scope = FindingScope.parse(self.scope)
        rule_id = _normalize_text(self.rule_id, field="rule_id")
        title = _normalize_text(self.title, field="title")
        category = _normalize_text(self.category, field="category")
        automation_type = AutomationType.parse(
            self.automation_type,
            contract=_CONTRACT,
            field="automation_type",
        )
        status = AssessmentStatus.parse(
            self.status,
            contract=_CONTRACT,
            field="status",
        )
        explanation = _normalize_text(self.explanation, field="explanation")
        remediation = _normalize_text(self.remediation, field="remediation")
        verification_method = _normalize_text(
            self.verification_method,
            field="verification_method",
        )

        try:
            severity = (
                self.severity
                if isinstance(self.severity, Severity)
                else Severity.from_label(self.severity)
            )
        except (ValueError, DomainError) as exc:
            raise ContractValidationError(
                "Finding severity is invalid.",
                contract=_CONTRACT,
                field="severity",
                cause=exc,
            ) from exc

        try:
            confidence = (
                self.confidence
                if isinstance(self.confidence, Confidence)
                else Confidence(self.confidence)
            )
        except (ValueError, DomainError) as exc:
            raise ContractValidationError(
                "Finding confidence is invalid.",
                contract=_CONTRACT,
                field="confidence",
                cause=exc,
            ) from exc

        try:
            evidence_refs = stable_unique_text(
                self.evidence_refs,
                field="evidence_refs",
            )
        except (DomainError, TypeError) as exc:
            raise ContractValidationError(
                "Finding evidence references are invalid.",
                contract=_CONTRACT,
                field="evidence_refs",
                cause=exc,
            ) from exc

        if automation_type is AutomationType.DETERMINISTIC and not evidence_refs:
            raise ContractIntegrityError(
                "A deterministic finding must reference supporting evidence.",
                contract=_CONTRACT,
                field="evidence_refs",
                context={"automation_type": automation_type.value},
            )

        try:
            observed_value = to_json_primitive(
                self.observed_value,
                contract=_CONTRACT,
                field="observed_value",
            )
            expected_value = to_json_primitive(
                self.expected_value,
                contract=_CONTRACT,
                field="expected_value",
            )
        except ContractSerializationError:
            raise

        object.__setattr__(self, "schema_version", str(self.schema_version).strip())
        object.__setattr__(self, "finding_id", finding_id)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "automation_type", automation_type)
        object.__setattr__(self, "severity", severity.level.label)
        object.__setattr__(self, "confidence", confidence.score)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "observed_value", observed_value)
        object.__setattr__(self, "expected_value", expected_value)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "explanation", explanation)
        object.__setattr__(self, "remediation", remediation)
        object.__setattr__(self, "verification_method", verification_method)

        logger.debug(
            {
                "event": "finding_contract_validated",
                "finding_id": self.finding_id,
                "scope": scope.value,
                "severity": severity.level.label,
                "confidence": confidence.score,
                "status": status.value,
                "automation_type": automation_type.value,
                "evidence_count": len(evidence_refs),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned JSON-ready BIMAP finding representation."""
        _announce("Serializing finding contract")
        return {
            "schema_version": self.schema_version,
            "finding_id": self.finding_id,
            "scope": self.scope.value,
            "rule_id": self.rule_id,
            "title": self.title,
            "category": self.category,
            "automation_type": self.automation_type.value,
            "severity": self.severity,
            "confidence": self.confidence,
            "status": self.status.value,
            "observed_value": to_json_primitive(
                self.observed_value,
                contract=_CONTRACT,
                field="observed_value",
            ),
            "expected_value": to_json_primitive(
                self.expected_value,
                contract=_CONTRACT,
                field="expected_value",
            ),
            "evidence_refs": list(self.evidence_refs),
            "explanation": self.explanation,
            "remediation": self.remediation,
            "verification_method": self.verification_method,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the finding using BIMAP canonical JSON rules."""
        _announce("Encoding finding contract JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FindingContract":
        """Parse a strict versioned BIMAP finding mapping."""
        _announce("Deserializing finding contract")
        data = validate_contract_fields(
            payload,
            required=(
                "schema_version",
                "finding_id",
                "scope",
                "rule_id",
                "title",
                "category",
                "automation_type",
                "severity",
                "confidence",
                "status",
                "observed_value",
                "expected_value",
                "evidence_refs",
                "explanation",
                "remediation",
                "verification_method",
            ),
            contract=_CONTRACT,
        )
        return cls(
            schema_version=data["schema_version"],
            finding_id=data["finding_id"],
            scope=data["scope"],
            rule_id=data["rule_id"],
            title=data["title"],
            category=data["category"],
            automation_type=data["automation_type"],
            severity=data["severity"],
            confidence=data["confidence"],
            status=data["status"],
            observed_value=data["observed_value"],
            expected_value=data["expected_value"],
            evidence_refs=data["evidence_refs"] or (),
            explanation=data["explanation"],
            remediation=data["remediation"],
            verification_method=data["verification_method"],
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "FindingContract":
        """Decode canonical JSON and validate a finding contract."""
        _announce("Decoding finding contract JSON")
        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Finding JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


# Backward-compatible name retained from the initial scaffold.
ContractsFindings = FindingContract


__all__ = [
    "FindingScope",
    "FindingContract",
    "ContractsFindings",
]


if __name__ == "__main__":
    print("\n=== Running Finding Contract Self-Test ===\n")
    printer.status("TEST", "Finding contract module initialized", "info")

    contract = FindingContract(
        finding_id="RFA-PARAM-00042",
        scope=FindingScope.FAMILY,
        rule_id="R3D.RFA.PARAM.001",
        title="Required parameter missing",
        category="parameter_governance",
        automation_type=AutomationType.DETERMINISTIC,
        severity="high",
        confidence=0.97,
        status=AssessmentStatus.FAIL,
        observed_value="Parameter FireRating missing",
        expected_value="Required by configured organization rule set",
        evidence_refs=("EV-0041", "EV-0042"),
        explanation="The required parameter is absent from the supplied evidence.",
        remediation="Add the approved shared parameter according to the governing rule set.",
        verification_method="Re-export the evidence package and rerun the rule.",
    )
    assert FindingContract.from_json(contract.to_json()) == contract
    printer.status("PASS", "Finding contract round trip", "success")

    print("\n=== Test ran successfully ===\n")