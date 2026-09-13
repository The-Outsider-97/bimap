"""Grounded FindingContract mapping policy for BIMAP Revit Family Audit."""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from typing import Final

from ..context import AuditContext
from ..rules.base import RuleResult, RuleStatus
from ..utils.engine_errors import EngineConfigurationError, EngineIntegrityError
from ..utils.engine_helpers import require_engine_text, to_engine_primitive
from .rules import (
    RFACalibratedComplexityRule,
    RFAComplexityMetricIntegrityRule,
    RFADuplicateParameterIdentityRule,
    RFADuplicateTypeIdentityRule,
    RFAFamilyCategoryConsistencyRule,
    RFAFamilyIdentityIntegrityRule,
    RFAFamilyIdentityPresenceRule,
    RFAFormulaGovernanceRule,
    RFAFormulaMetadataRule,
    RFAFormulaReferenceRule,
    RFAGeometryEvidenceIntegrityRule,
    RFAIdentityStandardsRule,
    RFAMaterialEvidenceIntegrityRule,
    RFAOrganizationProfileValidityRule,
    RFAParameterEvidenceIntegrityRule,
    RFAParameterGovernanceRule,
    RFAParameterMetadataRule,
    RFASourceIntegrityRule,
    RFATypeCatalogIntegrityRule,
    RFATypeNameIntegrityRule,
)
from ...contracts.finding import FindingContract, FindingScope
from ...contracts.requirement import AssessmentStatus, AutomationType
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP RFA Finding Mapper")
printer = PrettyPrinter()

_COMPONENT: Final[str] = "rfa_finding_mapper"


@dataclass(frozen=True, slots=True)
class RFAFindingPolicy:
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
            object.__setattr__(
                self,
                field_name,
                require_engine_text(
                    getattr(self, field_name),
                    field=field_name,
                    error_type=EngineConfigurationError,
                ),
            )
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise EngineConfigurationError(
                "Finding-policy confidence must be numeric.",
                component=_COMPONENT,
                operation="validate_policy",
                field="confidence",
            )
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise EngineConfigurationError(
                "Finding-policy confidence must be between 0 and 1.",
                component=_COMPONENT,
                operation="validate_policy",
                field="confidence",
            )
        object.__setattr__(self, "confidence", confidence)


def _policy(
    title: str,
    category: str,
    severity: str,
    explanation: str,
    remediation: str,
    verification: str,
    *,
    confidence: float = 1.0,
) -> RFAFindingPolicy:
    return RFAFindingPolicy(
        title=title,
        category=category,
        severity=severity,
        explanation=explanation,
        remediation=remediation,
        verification_method=verification,
        confidence=confidence,
    )


_POLICIES: Final[dict[str, RFAFindingPolicy]] = {
    RFASourceIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid Family Audit source identity",
        "source_integrity",
        "critical",
        "The normalized Family Audit evidence does not resolve to exactly one RFA source.",
        "Restage exactly one valid RFA family source and rerun the native Revit extraction pipeline.",
        "Rerun the audit and verify that the source-integrity rule passes with one RFA source.",
    ),
    RFAFamilyIdentityIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid Revit family identity evidence",
        "family_identity",
        "high",
        "Family identity evidence is inconsistent with the canonical Revit inspection contract.",
        "Correct the native Revit extraction output and regenerate canonical Family Evidence.",
        "Re-extract the RFA and rerun the identity-integrity rule.",
    ),
    RFATypeCatalogIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid family type-catalog evidence",
        "type_catalog",
        "medium",
        "One or more type-catalog records do not preserve canonical evidence structure.",
        "Correct the Revit type extraction output and regenerate Family Evidence.",
        "Re-extract the family and rerun type-catalog integrity validation.",
    ),
    RFAParameterEvidenceIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid family parameter evidence",
        "parameter_evidence",
        "high",
        "One or more parameter records do not preserve canonical evidence structure.",
        "Correct the Revit parameter extraction output and regenerate Family Evidence.",
        "Re-extract the family and rerun parameter-evidence integrity validation.",
    ),
    RFAMaterialEvidenceIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid family material evidence",
        "materials",
        "medium",
        "One or more material records do not preserve canonical evidence structure.",
        "Correct the Revit material extraction output and regenerate Family Evidence.",
        "Re-extract the family and rerun material-evidence integrity validation.",
    ),
    RFAGeometryEvidenceIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid family geometry evidence",
        "geometry_metrics",
        "medium",
        "One or more geometry-metric records do not preserve canonical evidence structure.",
        "Correct the Revit geometry extraction output and regenerate Family Evidence.",
        "Re-extract the family and rerun geometry-evidence integrity validation.",
    ),
    RFAFamilyIdentityPresenceRule.DEFINITION.rule_id: _policy(
        "Missing family identity metadata",
        "family_identity",
        "high",
        "The extracted family name or category is missing.",
        "Ensure the native Revit worker exports the family name and Revit category from the source document.",
        "Re-extract the family and verify both identity fields are present.",
    ),
    RFATypeNameIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid or missing family type name",
        "type_identity",
        "medium",
        "At least one extracted family type lacks a usable type name or the assessed family exposes no type record.",
        "Correct the family type definition or native extraction output so every family type has a stable name.",
        "Rerun extraction and verify every type record contains a non-empty type name.",
    ),
    RFADuplicateTypeIdentityRule.DEFINITION.rule_id: _policy(
        "Duplicate family type identity",
        "type_identity",
        "medium",
        "Two or more extracted type records resolve to the same stable type identity.",
        "Resolve duplicate type identities or correct duplicated extraction rows.",
        "Rerun the audit and verify duplicate type identity count is zero.",
    ),
    RFAFamilyCategoryConsistencyRule.DEFINITION.rule_id: _policy(
        "Inconsistent family category evidence",
        "family_identity",
        "high",
        "Type-level category metadata contradicts the canonical family category.",
        "Correct the source/extractor inconsistency and regenerate the evidence package.",
        "Rerun the audit and verify all published type categories agree with the family category.",
    ),
    RFAParameterMetadataRule.DEFINITION.rule_id: _policy(
        "Incomplete Revit parameter metadata",
        "parameter_governance",
        "high",
        "One or more parameters lack auditable name, data type, type/instance scope, or shared-parameter state.",
        "Update the native Revit extraction worker so parameter governance metadata is exported completely.",
        "Re-extract the family and verify each parameter contains the required metadata.",
    ),
    RFADuplicateParameterIdentityRule.DEFINITION.rule_id: _policy(
        "Duplicate parameter identity",
        "parameter_governance",
        "medium",
        "Two or more extracted parameter records resolve to the same stable parameter identity.",
        "Remove duplicate parameter definitions or correct duplicated extraction rows.",
        "Rerun the audit and verify duplicate parameter identity count is zero.",
    ),
    RFAParameterGovernanceRule.DEFINITION.rule_id: _policy(
        "Parameter policy deviation",
        "parameter_governance",
        "high",
        "Observed parameter metadata does not satisfy the explicit organization profile.",
        "Align required parameters, naming, data types, scope, and shared-parameter policy with the approved profile.",
        "Rerun the same versioned organization profile and confirm the parameter governance rule passes.",
    ),
    RFAFormulaMetadataRule.DEFINITION.rule_id: _policy(
        "Invalid family formula metadata",
        "formulas",
        "medium",
        "An extracted formula is missing its bound parameter or formula expression.",
        "Correct the family formula or native formula extraction metadata.",
        "Re-extract the family and verify all formula records contain parameter and expression fields.",
    ),
    RFAFormulaReferenceRule.DEFINITION.rule_id: _policy(
        "Unresolved family formula reference",
        "formulas",
        "high",
        "A published formula dependency does not resolve to an extracted family parameter.",
        "Correct the formula reference or the native parameter/formula dependency export.",
        "Rerun the audit and verify every published formula reference resolves to a family parameter.",
    ),
    RFAFormulaGovernanceRule.DEFINITION.rule_id: _policy(
        "Expected family formula differs from policy",
        "formulas",
        "high",
        "An expected formula is missing or differs from the expression declared by the explicit organization profile.",
        "Update the family formula or revise the approved versioned organization profile through the proper governance process.",
        "Rerun the same profile version and verify all expected formula relationships pass.",
    ),
    RFAOrganizationProfileValidityRule.DEFINITION.rule_id: _policy(
        "Invalid organization Family Audit profile",
        "standards",
        "high",
        "The supplied organization/customer Family Audit profile is structurally invalid or ambiguous.",
        "Correct the versioned profile so exactly one valid profile is supplied.",
        "Validate the profile and rerun the Family Audit.",
    ),
    RFAIdentityStandardsRule.DEFINITION.rule_id: _policy(
        "Family identity policy deviation",
        "standards",
        "medium",
        "Family identity/category/type naming does not satisfy explicit organization policy.",
        "Align the family identity with the approved profile or govern a profile revision.",
        "Rerun the audit using the same profile version and verify identity standards pass.",
    ),
    RFAComplexityMetricIntegrityRule.DEFINITION.rule_id: _policy(
        "Invalid family complexity metric evidence",
        "complexity",
        "medium",
        "One or more complexity metrics are missing numeric values/names or duplicate the same metric identity.",
        "Correct native geometry/complexity extraction and regenerate the evidence package.",
        "Re-extract the family and verify the metric-integrity rule passes.",
    ),
    RFACalibratedComplexityRule.DEFINITION.rule_id: _policy(
        "Family complexity exceeds calibrated policy",
        "complexity",
        "medium",
        "One or more observed complexity metrics exceed thresholds in the explicit organization profile.",
        "Reduce model complexity where appropriate or govern a justified profile calibration change.",
        "Rerun the audit against the same calibrated profile and verify the metrics fall within accepted thresholds.",
        confidence=0.95,
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
    return f"RFA-FIND-{hashlib.sha256(encoded).hexdigest()[:24].upper()}"


def map_rfa_finding(
    result: RuleResult,
    context: AuditContext,
) -> FindingContract | None:
    """Map WARN/FAIL deterministic rule results to grounded findings."""
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
    if status in {RuleStatus.PASS, RuleStatus.UNKNOWN, RuleStatus.NOT_APPLICABLE}:
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
            context={"rule_id": result.rule_id, "status": status.value},
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


__all__ = ["RFAFindingPolicy", "map_rfa_finding"]
