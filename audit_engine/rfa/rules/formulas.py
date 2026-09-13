"""Formula and parameter-reference rules for BIMAP Revit Family Audit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...context import AuditContext
from ...rules.base import BaseRules, RuleDefinition, RuleResult, RuleStatus
from ....domain.products.models import ProductCode
from ._helpers import (
    canonical_name,
    formula_expression,
    formula_parameter,
    formula_references,
    group_mappings,
    normalized_expression,
    parameter_name,
    section_assessed,
)
from .standards import resolve_organization_profile


_PRODUCT = ProductCode.FAMILY_AUDIT
_VERSION = "1.0.0"


class RFAFormulaMetadataRule(BaseRules):
    """Validate that every extracted Revit formula is bound to a parameter and expression."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.FORMULA.METADATA.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_formula_metadata",
        known_limitations=(
            "The rule checks extracted formula metadata; it does not attempt symbolic algebra or formula execution.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        records = group_mappings(context, "formulas")
        if not records:
            assessed = section_assessed(context, "formulas")
            return self.result(
                RuleStatus.NOT_APPLICABLE if assessed is True else RuleStatus.UNKNOWN,
                evidence_refs=tuple(item.evidence_id for item in context.group("family_identity")),
                observed_value={"formula_count": 0},
                metrics={"section_assessed": assessed},
            )

        invalid: list[dict[str, Any]] = []
        for item, record in records:
            missing = []
            if formula_parameter(record) is None:
                missing.append("parameter")
            if formula_expression(record) is None:
                missing.append("expression")
            if missing:
                invalid.append(
                    {
                        "evidence_id": item.evidence_id,
                        "missing_fields": missing,
                    }
                )

        return self.result(
            RuleStatus.FAIL if invalid else RuleStatus.PASS,
            evidence_refs=tuple(item.evidence_id for item, _ in records),
            observed_value={
                "formula_count": len(records),
                "invalid_formula_count": len(invalid),
            },
            expected_value={"invalid_formula_count": 0},
            metrics={"invalid_formulas": invalid},
        )


class RFAFormulaReferenceRule(BaseRules):
    """Require published formula dependencies to resolve to extracted family parameters."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.FORMULA.REFERENCE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_formula_reference_integrity",
        known_limitations=(
            "Reference validation depends on the native worker publishing a formula references/dependencies array.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        formulas = group_mappings(context, "formulas")
        if not formulas:
            assessed = section_assessed(context, "formulas")
            return self.result(
                RuleStatus.NOT_APPLICABLE if assessed is True else RuleStatus.UNKNOWN,
                evidence_refs=tuple(item.evidence_id for item in context.group("family_identity")),
                metrics={"section_assessed": assessed},
            )

        parameters = group_mappings(context, "parameters")
        parameter_names = {
            canonical_name(name)
            for _, record in parameters
            if (name := parameter_name(record)) is not None
        }

        reference_metadata_present = False
        missing_reference_metadata: list[str] = []
        unresolved: list[dict[str, Any]] = []
        checked_references = 0
        for item, record in formulas:
            references = formula_references(record)
            if references is None:
                missing_reference_metadata.append(item.evidence_id)
                continue
            reference_metadata_present = True
            for reference in references:
                checked_references += 1
                if canonical_name(reference) not in parameter_names:
                    unresolved.append(
                        {
                            "formula_evidence_id": item.evidence_id,
                            "reference": reference,
                        }
                    )

        if not reference_metadata_present:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple(item.evidence_id for item, _ in formulas),
                metrics={"reason": "formula_reference_metadata_not_published"},
            )

        status = (
            RuleStatus.FAIL
            if unresolved
            else RuleStatus.UNKNOWN
            if missing_reference_metadata
            else RuleStatus.PASS
        )
        return self.result(
            status,
            evidence_refs=tuple(
                [
                    *(item.evidence_id for item, _ in formulas),
                    *(item.evidence_id for item, _ in parameters),
                ]
            ),
            observed_value={
                "checked_reference_count": checked_references,
                "unresolved_reference_count": len(unresolved),
            },
            expected_value={"unresolved_reference_count": 0},
            metrics={
                "unresolved_references": unresolved,
                "missing_reference_metadata_evidence_ids": missing_reference_metadata,
            },
        )


class RFAFormulaGovernanceRule(BaseRules):
    """Compare observed formulas against explicit expected organization relationships."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.FORMULA.GOVERNANCE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity", "organization_rules"),
        severity_policy="rfa_formula_governance",
        known_limitations=(
            "Expression comparison normalizes whitespace and case only; algebraically equivalent but textually different formulas are not treated as equal.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        profile, policy_refs, errors = resolve_organization_profile(context)
        formulas = group_mappings(context, "formulas")
        if profile is None:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple(
                    [*(item.evidence_id for item, _ in formulas), *policy_refs]
                ),
                metrics={"profile_errors": list(errors)},
            )

        policy = profile.section("formulas")
        expected = policy.get("expected") if policy else None
        if expected is None or isinstance(expected, (str, bytes, bytearray, Mapping)):
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple(policy_refs),
                metrics={"reason": "expected_formula_policy_not_configured"},
            )

        expected_rows = tuple(item for item in expected if isinstance(item, Mapping))
        if not expected_rows:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple(policy_refs),
                metrics={"reason": "expected_formula_policy_empty"},
            )

        observed_by_parameter: dict[str, tuple[str, str]] = {}
        for item, record in formulas:
            parameter = formula_parameter(record)
            expression = formula_expression(record)
            if parameter and expression:
                observed_by_parameter[canonical_name(parameter)] = (
                    item.evidence_id,
                    expression,
                )

        assessed = section_assessed(context, "formulas")
        if not formulas and assessed is not True:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple(policy_refs),
                metrics={
                    "reason": "formula_section_not_confirmed_assessed",
                    "expected_formula_count": len(expected_rows),
                },
            )

        violations: list[dict[str, Any]] = []
        for requirement in expected_rows:
            parameter = requirement.get("parameter")
            expression = requirement.get("expression")
            if not isinstance(parameter, str) or not parameter.strip():
                continue
            if not isinstance(expression, str) or not expression.strip():
                continue

            observed = observed_by_parameter.get(canonical_name(parameter))
            if observed is None:
                violations.append(
                    {
                        "kind": "formula_missing",
                        "parameter": parameter,
                        "expected": expression,
                    }
                )
                continue

            evidence_id, observed_expression = observed
            if normalized_expression(observed_expression) != normalized_expression(expression):
                violations.append(
                    {
                        "kind": "formula_mismatch",
                        "parameter": parameter,
                        "evidence_id": evidence_id,
                        "observed": observed_expression,
                        "expected": expression,
                    }
                )

        return self.result(
            RuleStatus.FAIL if violations else RuleStatus.PASS,
            evidence_refs=tuple(
                [*(item.evidence_id for item, _ in formulas), *policy_refs]
            ),
            observed_value={"formula_count": len(formulas)},
            expected_value={
                "profile_id": profile.profile_id,
                "profile_version": profile.profile_version,
                "expected_formula_count": len(expected_rows),
            },
            metrics={"violations": violations},
        )


RFA_FORMULA_RULES: tuple[BaseRules, ...] = (
    RFAFormulaMetadataRule(),
    RFAFormulaReferenceRule(),
    RFAFormulaGovernanceRule(),
)


__all__ = [
    "RFAFormulaMetadataRule",
    "RFAFormulaReferenceRule",
    "RFAFormulaGovernanceRule",
    "RFA_FORMULA_RULES",
]
