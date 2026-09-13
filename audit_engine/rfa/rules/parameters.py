"""Parameter-governance rules for BIMAP Revit Family Audit."""

from __future__ import annotations

import re

from collections.abc import Mapping
from typing import Any

from ...context import AuditContext
from ...rules.base import BaseRules, RuleDefinition, RuleResult, RuleStatus
from ....domain.products.models import ProductCode
from ._helpers import (
    canonical_key,
    canonical_name,
    group_mappings,
    parameter_data_type,
    parameter_name,
    parameter_scope,
    parameter_shared,
    section_assessed,
    text_for,
)
from .standards import resolve_organization_profile


_PRODUCT = ProductCode.FAMILY_AUDIT
_VERSION = "1.0.0"


class RFAParameterMetadataRule(BaseRules):
    """Require auditable parameter name, data type, scope, and shared status."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.PARAM.METADATA.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_parameter_metadata",
        known_limitations=(
            "Metadata availability depends on the native Revit worker; parameter values themselves are not required by this rule.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        records = group_mappings(context, "parameters")
        if not records:
            assessed = section_assessed(context, "parameters")
            return self.result(
                RuleStatus.NOT_APPLICABLE if assessed is True else RuleStatus.UNKNOWN,
                evidence_refs=tuple(item.evidence_id for item in context.group("family_identity")),
                observed_value={"parameter_count": 0},
                metrics={"section_assessed": assessed},
            )

        invalid: list[dict[str, Any]] = []
        for item, record in records:
            missing: list[str] = []
            if parameter_name(record) is None:
                missing.append("name")
            if parameter_data_type(record) is None:
                missing.append("data_type")
            if parameter_scope(record) is None:
                missing.append("scope")
            if parameter_shared(record) is None:
                missing.append("shared")
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
                "parameter_count": len(records),
                "invalid_parameter_count": len(invalid),
            },
            expected_value={"invalid_parameter_count": 0},
            metrics={"invalid_parameters": invalid},
        )


class RFADuplicateParameterIdentityRule(BaseRules):
    """Detect duplicate stable parameter identities without conflating type/instance scope."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.PARAM.DUPLICATE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_duplicate_parameter_identity",
        known_limitations=(
            "When native GUID/built-in identifiers are absent, the fallback identity is normalized name plus parameter scope.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        records = group_mappings(context, "parameters")
        if len(records) < 2:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple(item.evidence_id for item, _ in records),
                observed_value={"parameter_count": len(records)},
                expected_value={"duplicate_identity_count": 0},
            )

        seen: dict[str, str] = {}
        duplicates: list[dict[str, str]] = []
        for item, record in records:
            guid = text_for(record, "shared_guid", "sharedGuid", "guid")
            built_in = text_for(
                record,
                "built_in_parameter",
                "builtInParameter",
                "built_in_id",
                "definition_id",
            )
            name = parameter_name(record)
            scope = parameter_scope(record) or "unknown"

            if guid:
                identity = f"shared:{canonical_key(guid)}"
            elif built_in:
                identity = f"builtin:{canonical_key(built_in)}"
            elif name:
                identity = f"name:{canonical_name(name)}:{scope}"
            else:
                continue

            previous = seen.get(identity)
            if previous is None:
                seen[identity] = item.evidence_id
            else:
                duplicates.append(
                    {
                        "identity": identity,
                        "first_evidence_id": previous,
                        "duplicate_evidence_id": item.evidence_id,
                    }
                )

        return self.result(
            RuleStatus.FAIL if duplicates else RuleStatus.PASS,
            evidence_refs=tuple(item.evidence_id for item, _ in records),
            observed_value={"duplicate_identity_count": len(duplicates)},
            expected_value={"duplicate_identity_count": 0},
            metrics={"duplicates": duplicates},
        )


class RFAParameterGovernanceRule(BaseRules):
    """
    Compare observed parameter metadata with explicit organization policy.

    No parameter is considered required and no naming/scope/shared convention is
    assumed unless the versioned organization profile declares it.
    """

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.PARAM.GOVERNANCE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity", "organization_rules"),
        severity_policy="rfa_parameter_governance",
        known_limitations=(
            "Data-type comparison is canonical-text based; organization profiles should use the extractor's published Revit/spec-type vocabulary.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        profile, policy_refs, errors = resolve_organization_profile(context)
        records = group_mappings(context, "parameters")
        if profile is None:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple(
                    [*(item.evidence_id for item, _ in records), *policy_refs]
                ),
                metrics={"profile_errors": list(errors)},
            )

        policy = profile.section("parameters")
        if not policy:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple(policy_refs),
                metrics={"reason": "parameter_policy_not_configured"},
            )

        if not records and section_assessed(context, "parameters") is not True:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple(policy_refs),
                metrics={
                    "reason": "parameter_section_not_confirmed_assessed",
                    "profile_id": profile.profile_id,
                    "profile_version": profile.profile_version,
                },
            )

        by_name: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        for item, record in records:
            name = parameter_name(record)
            if name is None:
                continue
            by_name.setdefault(canonical_name(name), []).append((item.evidence_id, record))

        violations: list[dict[str, Any]] = []
        checks = 0

        name_regex = policy.get("name_regex")
        if isinstance(name_regex, str):
            for item, record in records:
                name = parameter_name(record)
                checks += 1
                if name is None or re.fullmatch(name_regex, name) is None:
                    violations.append(
                        {
                            "kind": "parameter_name",
                            "evidence_id": item.evidence_id,
                            "observed": name,
                            "expected_regex": name_regex,
                        }
                    )

        required = policy.get("required")
        if required is not None and not isinstance(
            required, (str, bytes, bytearray, Mapping)
        ):
            for requirement in required:
                if not isinstance(requirement, Mapping):
                    continue
                expected_name = requirement.get("name")
                if not isinstance(expected_name, str) or not expected_name.strip():
                    continue
                checks += 1
                matches = by_name.get(canonical_name(expected_name), [])
                if not matches:
                    violations.append(
                        {
                            "kind": "required_parameter_missing",
                            "parameter": expected_name,
                        }
                    )
                    continue

                expected_scope = requirement.get("scope")
                selected_matches = list(matches)
                if isinstance(expected_scope, str) and expected_scope.strip():
                    normalized_scope = canonical_key(expected_scope)
                    selected_matches = [
                        (evidence_id, record)
                        for evidence_id, record in matches
                        if canonical_key(parameter_scope(record)) == normalized_scope
                    ]
                    if not selected_matches:
                        violations.append(
                            {
                                "kind": "required_parameter_scope_missing",
                                "parameter": expected_name,
                                "expected_scope": expected_scope,
                                "observed_scopes": [
                                    parameter_scope(record)
                                    for _, record in matches
                                ],
                            }
                        )
                        continue

                # A requirement is checked only against matching name/scope
                # candidates. Duplicate stable identities are assessed by the
                # dedicated duplicate-parameter rule.
                for evidence_id, record in selected_matches:
                    expected_type = requirement.get("data_type")
                    if isinstance(expected_type, str) and expected_type.strip():
                        actual_type = parameter_data_type(record)
                        if canonical_key(actual_type) != canonical_key(expected_type):
                            violations.append(
                                {
                                    "kind": "data_type",
                                    "parameter": expected_name,
                                    "evidence_id": evidence_id,
                                    "observed": actual_type,
                                    "expected": expected_type,
                                }
                            )

                    expected_shared = requirement.get("shared")
                    if isinstance(expected_shared, bool):
                        actual_shared = parameter_shared(record)
                        if actual_shared is not expected_shared:
                            violations.append(
                                {
                                    "kind": "shared",
                                    "parameter": expected_name,
                                    "evidence_id": evidence_id,
                                    "observed": actual_shared,
                                    "expected": expected_shared,
                                }
                            )

        if checks == 0:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple(
                    [*(item.evidence_id for item, _ in records), *policy_refs]
                ),
                metrics={"reason": "no_supported_parameter_policy_configured"},
            )

        return self.result(
            RuleStatus.FAIL if violations else RuleStatus.PASS,
            evidence_refs=tuple(
                [*(item.evidence_id for item, _ in records), *policy_refs]
            ),
            observed_value={"parameter_count": len(records)},
            expected_value={
                "profile_id": profile.profile_id,
                "profile_version": profile.profile_version,
            },
            metrics={"checks": checks, "violations": violations},
        )


RFA_PARAMETER_RULES: tuple[BaseRules, ...] = (
    RFAParameterMetadataRule(),
    RFADuplicateParameterIdentityRule(),
    RFAParameterGovernanceRule(),
)


__all__ = [
    "RFAParameterMetadataRule",
    "RFADuplicateParameterIdentityRule",
    "RFAParameterGovernanceRule",
    "RFA_PARAMETER_RULES",
]
