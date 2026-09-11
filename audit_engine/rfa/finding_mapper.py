"""
Grounded FindingContract mapping policy for BIMAP Revit Family Audit.

Location
--------
applications/bimap/audit_engine/rfa/finding_mapper.py

The deterministic rule layer intentionally does not own finding severity,
customer-facing explanation, remediation, or verification text. This module is
the explicit policy boundary required by ``RFAAuditor``.

Only WARN/FAIL RuleResults become findings. PASS, UNKNOWN and NOT_APPLICABLE
remain visible as rule results/coverage state but are not misrepresented as
actionable findings.
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from typing import Final

from ..context import AuditContext
from ..rules.base import RuleResult, RuleStatus
from ..utils.engine_errors import (
    EngineConfigurationError,
    EngineIntegrityError,
)
from ..utils.engine_helpers import (
    require_engine_text,
    to_engine_primitive,
)
from .rules import (
    RFAFamilyIdentityIntegrityRule,
    RFAGeometryEvidenceIntegrityRule,
    RFAMaterialEvidenceIntegrityRule,
    RFAParameterEvidenceIntegrityRule,
    RFASourceIntegrityRule,
    RFATypeCatalogIntegrityRule,
)
from ...contracts.finding import (
    FindingContract,
    FindingScope,
)
from ...contracts.requirement import (
    AssessmentStatus,
    AutomationType,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP RFA Finding Mapper")
printer = PrettyPrinter()

_COMPONENT: Final[str] = "rfa_finding_mapper"


@dataclass(frozen=True, slots=True)
class RFAFindingPolicy:
    """Customer-facing policy attached to one deterministic RFA rule."""

    title: str
    category: str
    severity: str
    explanation: str
    remediation: str
    verification_method: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        for field_name in (
            "title",
            "category",
            "severity",
            "explanation",
            "remediation",
            "verification_method",
        ):
            value = getattr(self, field_name)
            normalized = require_engine_text(
                value,
                field=field_name,
                error_type=EngineConfigurationError,
            )
            object.__setattr__(self, field_name, normalized)

        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
        ):
            raise EngineConfigurationError(
                "Finding-policy confidence must be numeric.",
                component=_COMPONENT,
                operation="validate_policy",
                field="confidence",
            )

        normalized_confidence = float(self.confidence)

        if not 0.0 <= normalized_confidence <= 1.0:
            raise EngineConfigurationError(
                "Finding-policy confidence must be between 0 and 1.",
                component=_COMPONENT,
                operation="validate_policy",
                field="confidence",
            )

        object.__setattr__(
            self,
            "confidence",
            normalized_confidence,
        )


_POLICIES: Final[dict[str, RFAFindingPolicy]] = {
    RFASourceIntegrityRule.DEFINITION.rule_id: RFAFindingPolicy(
        title="Invalid Family Audit source identity",
        category="source_integrity",
        severity="critical",
        explanation=(
            "The normalized Family Audit evidence does not resolve "
            "to exactly one RFA source."
        ),
        remediation=(
            "Restage exactly one valid RFA family source and rerun "
            "the native Revit extraction pipeline."
        ),
        verification_method=(
            "Rerun the audit and verify that the source-integrity "
            "rule passes with one source of type rfa."
        ),
    ),
    RFAFamilyIdentityIntegrityRule.DEFINITION.rule_id: RFAFindingPolicy(
        title="Invalid Revit family identity evidence",
        category="family_identity",
        severity="high",
        explanation=(
            "Family identity evidence is missing or inconsistent "
            "with the canonical Revit inspection contract."
        ),
        remediation=(
            "Correct the native Revit extractor output so the "
            "identity evidence contains a valid RFA inspection, "
            "schema and positive product count."
        ),
        verification_method=(
            "Re-extract the RFA and rerun the identity-integrity rule."
        ),
    ),
    RFATypeCatalogIntegrityRule.DEFINITION.rule_id: RFAFindingPolicy(
        title="Invalid family type-catalog evidence",
        category="type_catalog",
        severity="medium",
        explanation=(
            "One or more type-catalog evidence records do not "
            "preserve the canonical extracted row structure or "
            "logical location."
        ),
        remediation=(
            "Correct the Revit elements extraction output and "
            "regenerate the Family Evidence package."
        ),
        verification_method=(
            "Re-extract the family and rerun the type-catalog "
            "integrity rule."
        ),
    ),
    RFAParameterEvidenceIntegrityRule.DEFINITION.rule_id: RFAFindingPolicy(
        title="Invalid family parameter evidence",
        category="parameter_evidence",
        severity="high",
        explanation=(
            "One or more parameter evidence records do not preserve "
            "the canonical properties-row structure or logical location."
        ),
        remediation=(
            "Correct the Revit properties extraction output and "
            "regenerate the Family Evidence package."
        ),
        verification_method=(
            "Re-extract the family and rerun the parameter-evidence "
            "integrity rule."
        ),
    ),
    RFAMaterialEvidenceIntegrityRule.DEFINITION.rule_id: RFAFindingPolicy(
        title="Invalid family material evidence",
        category="materials",
        severity="medium",
        explanation=(
            "One or more material evidence records do not preserve "
            "the canonical materials-row structure or logical location."
        ),
        remediation=(
            "Correct the Revit material extraction output and "
            "regenerate the Family Evidence package."
        ),
        verification_method=(
            "Re-extract the family and rerun the material-evidence "
            "integrity rule."
        ),
    ),
    RFAGeometryEvidenceIntegrityRule.DEFINITION.rule_id: RFAFindingPolicy(
        title="Invalid family geometry-metric evidence",
        category="geometry_metrics",
        severity="medium",
        explanation=(
            "One or more geometry-metric evidence records do not "
            "preserve the canonical quantities-row structure or "
            "logical location."
        ),
        remediation=(
            "Correct the Revit quantities extraction output and "
            "regenerate the Family Evidence package."
        ),
        verification_method=(
            "Re-extract the family and rerun the geometry-evidence "
            "integrity rule."
        ),
    ),
}


def _finding_id(result: RuleResult) -> str:
    identity = {
        "rule_id": result.rule_id,
        "rule_version": str(result.rule_version),
        "status": str(getattr(result.status, "value", result.status)),
        "evidence_refs": list(result.evidence_refs),
        "observed_value": to_engine_primitive(
            result.observed_value,
            field="rule_result.observed_value",
        ),
        "expected_value": to_engine_primitive(
            result.expected_value,
            field="rule_result.expected_value",
        ),
    }

    encoded = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = hashlib.sha256(encoded).hexdigest()[:24].upper()

    return f"RFA-FIND-{digest}"


def map_rfa_finding(
    result: RuleResult,
    context: AuditContext,
) -> FindingContract | None:
    """
    Map one grounded RFA RuleResult into a FindingContract.

    Non-actionable result statuses remain rule results and are intentionally not
    converted into findings.
    """
    if not isinstance(result, RuleResult):
        raise EngineIntegrityError(
            "RFA finding mapper requires a RuleResult.",
            component=_COMPONENT,
            operation="map",
            field="result",
            context={"received_type": type(result).__name__},
        )

    if not isinstance(context, AuditContext):
        raise EngineIntegrityError(
            "RFA finding mapper requires an AuditContext.",
            component=_COMPONENT,
            operation="map",
            field="context",
            context={"received_type": type(context).__name__},
        )

    status = RuleStatus.parse(result.status)

    if status in {
        RuleStatus.PASS,
        RuleStatus.UNKNOWN,
        RuleStatus.NOT_APPLICABLE,
    }:
        return None

    policy = _POLICIES.get(result.rule_id)

    if policy is None:
        raise EngineConfigurationError(
            "No approved RFA finding policy exists for the executed rule.",
            component=_COMPONENT,
            operation="map",
            field="rule_id",
            context={"rule_id": result.rule_id},
        )

    if not result.evidence_refs:
        raise EngineIntegrityError(
            "Actionable deterministic RFA result has no evidence references.",
            component=_COMPONENT,
            operation="map",
            field="evidence_refs",
            context={
                "rule_id": result.rule_id,
                "status": status.value,
            },
        )

    context_ids = set(context.evidence_ids)
    unresolved = tuple(
        evidence_id
        for evidence_id in result.evidence_refs
        if evidence_id not in context_ids
    )

    if unresolved:
        raise EngineIntegrityError(
            "RFA finding policy received unresolved evidence references.",
            component=_COMPONENT,
            operation="map",
            field="evidence_refs",
            context={
                "rule_id": result.rule_id,
                "unresolved_evidence_refs": unresolved,
            },
        )

    return FindingContract(
        finding_id=_finding_id(result),
        scope=FindingScope.FAMILY,
        rule_id=result.rule_id,
        title=policy.title,
        category=policy.category,
        automation_type=AutomationType.DETERMINISTIC,
        severity=policy.severity,
        confidence=policy.confidence,
        status=AssessmentStatus.parse(status.value),
        observed_value=result.observed_value,
        expected_value=result.expected_value,
        evidence_refs=tuple(result.evidence_refs),
        explanation=policy.explanation,
        remediation=policy.remediation,
        verification_method=policy.verification_method,
    )


__all__ = [
    "RFAFindingPolicy",
    "map_rfa_finding",
]
