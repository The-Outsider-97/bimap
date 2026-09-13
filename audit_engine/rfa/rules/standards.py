"""Versioned organization/customer standards for Revit Family Audit."""

from __future__ import annotations

import re

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from ...context import AuditContext
from ...rules.base import BaseRules, RuleDefinition, RuleResult, RuleStatus
from ....domain.products.models import ProductCode
from ._helpers import (
    canonical_name,
    family_category,
    family_name,
    group_items,
    group_mappings,
    primitive_mapping,
    refs,
    section_assessed,
    text_for,
    type_name,
)


_PRODUCT = ProductCode.FAMILY_AUDIT
_VERSION = "1.0.0"
_PROFILE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, slots=True)
class RFAOrganizationProfile:
    """
    Strict minimal view of an explicit versioned customer/organization profile.

    Supported evidence shape::

        {
          "profile_id": "ORG-RFA-2026",
          "profile_version": "1.0.0",
          "family": {
            "identity": {
              "family_name_regex": "...",
              "allowed_categories": ["Generic Models"],
              "type_name_regex": "..."
            },
            "parameters": {
              "name_regex": "...",
              "required": [
                {
                  "name": "Width",
                  "data_type": "length",
                  "scope": "type",
                  "shared": false
                }
              ]
            },
            "formulas": {
              "expected": [
                {"parameter": "top", "expression": "h / 7.5"}
              ]
            },
            "complexity": {
              "metrics": {
                "total_polygons": {"warn_above": 50000, "fail_above": 100000}
              }
            }
          }
        }

    Unknown policy keys are preserved but never interpreted implicitly.
    """

    profile_id: str
    profile_version: str
    family: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        if not isinstance(self.profile_version, str) or not self.profile_version.strip():
            raise ValueError("profile_version must be a non-empty string")
        profile_id = self.profile_id.strip()
        version = self.profile_version.strip()
        if not _PROFILE_VERSION.fullmatch(version):
            raise ValueError("profile_version must be a semantic version")
        if not isinstance(self.family, Mapping):
            raise TypeError("family must be a mapping")

        family = dict(self.family)
        for section in ("identity", "parameters", "formulas", "complexity"):
            value = family.get(section)
            if value is not None and not isinstance(value, Mapping):
                raise TypeError(f"family.{section} must be a mapping")

        identity = family.get("identity")
        if isinstance(identity, Mapping):
            for key in ("family_name_regex", "type_name_regex"):
                pattern = identity.get(key)
                if pattern is not None:
                    if not isinstance(pattern, str) or not pattern.strip():
                        raise ValueError(f"family.identity.{key} must be a non-empty string")
                    re.compile(pattern)

            allowed = identity.get("allowed_categories")
            if allowed is not None:
                if isinstance(allowed, (str, bytes, bytearray, Mapping)):
                    raise TypeError("family.identity.allowed_categories must be a sequence")
                for item in allowed:
                    if not isinstance(item, str) or not item.strip():
                        raise ValueError(
                            "family.identity.allowed_categories must contain non-empty strings"
                        )

        parameters = family.get("parameters")
        if isinstance(parameters, Mapping):
            pattern = parameters.get("name_regex")
            if pattern is not None:
                if not isinstance(pattern, str) or not pattern.strip():
                    raise ValueError("family.parameters.name_regex must be a non-empty string")
                re.compile(pattern)
            required = parameters.get("required")
            if required is not None:
                if isinstance(required, (str, bytes, bytearray, Mapping)):
                    raise TypeError("family.parameters.required must be a sequence")
                for index, item in enumerate(required):
                    if not isinstance(item, Mapping):
                        raise TypeError(
                            f"family.parameters.required[{index}] must be a mapping"
                        )
                    name = item.get("name")
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError(
                            f"family.parameters.required[{index}].name must be non-empty"
                        )
                    data_type = item.get("data_type")
                    if data_type is not None and (
                        not isinstance(data_type, str) or not data_type.strip()
                    ):
                        raise ValueError(
                            f"family.parameters.required[{index}].data_type must be a non-empty string"
                        )
                    scope = item.get("scope")
                    if scope is not None:
                        if not isinstance(scope, str) or scope.strip().casefold() not in {
                            "type",
                            "instance",
                        }:
                            raise ValueError(
                                f"family.parameters.required[{index}].scope must be type or instance"
                            )
                    shared = item.get("shared")
                    if shared is not None and not isinstance(shared, bool):
                        raise TypeError(
                            f"family.parameters.required[{index}].shared must be boolean"
                        )

        formulas = family.get("formulas")
        if isinstance(formulas, Mapping):
            expected = formulas.get("expected")
            if expected is not None:
                if isinstance(expected, (str, bytes, bytearray, Mapping)):
                    raise TypeError("family.formulas.expected must be a sequence")
                for index, item in enumerate(expected):
                    if not isinstance(item, Mapping):
                        raise TypeError(
                            f"family.formulas.expected[{index}] must be a mapping"
                        )
                    parameter = item.get("parameter")
                    expression = item.get("expression")
                    if not isinstance(parameter, str) or not parameter.strip():
                        raise ValueError(
                            f"family.formulas.expected[{index}].parameter must be non-empty"
                        )
                    if not isinstance(expression, str) or not expression.strip():
                        raise ValueError(
                            f"family.formulas.expected[{index}].expression must be non-empty"
                        )

        complexity = family.get("complexity")
        if isinstance(complexity, Mapping):
            metrics = complexity.get("metrics")
            if metrics is not None:
                if not isinstance(metrics, Mapping):
                    raise TypeError("family.complexity.metrics must be a mapping")
                for metric_name, calibration in metrics.items():
                    if not isinstance(metric_name, str) or not metric_name.strip():
                        raise ValueError("complexity metric names must be non-empty strings")
                    if not isinstance(calibration, Mapping):
                        raise TypeError(
                            f"family.complexity.metrics.{metric_name} must be a mapping"
                        )
                    warn = calibration.get("warn_above")
                    fail = calibration.get("fail_above")
                    for key, value in (("warn_above", warn), ("fail_above", fail)):
                        if value is not None and (
                            isinstance(value, bool) or not isinstance(value, (int, float))
                        ):
                            raise TypeError(
                                f"family.complexity.metrics.{metric_name}.{key} must be numeric"
                            )
                    if warn is not None and fail is not None and float(fail) < float(warn):
                        raise ValueError(
                            f"family.complexity.metrics.{metric_name}.fail_above must be >= warn_above"
                        )

        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "profile_version", version)
        object.__setattr__(self, "family", MappingProxyType(family))

    def section(self, name: str) -> Mapping[str, Any]:
        value = self.family.get(name)
        return value if isinstance(value, Mapping) else {}


def _profile_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = record.get("profile")
    return nested if isinstance(nested, Mapping) else record


def resolve_organization_profile(
    context: AuditContext,
) -> tuple[RFAOrganizationProfile | None, tuple[str, ...], tuple[str, ...]]:
    """Resolve exactly one explicit profile without raising on invalid evidence."""
    items = group_items(context, "organization_rules")
    evidence_refs = tuple(item.evidence_id for item in items)
    if not items:
        return None, (), ("organization_profile_missing",)
    if len(items) != 1:
        return None, evidence_refs, ("multiple_organization_profiles",)

    record = primitive_mapping(items[0])
    if record is None:
        return None, evidence_refs, ("invalid_profile:payload_not_object",)
    payload = _profile_payload(record)
    try:
        profile = RFAOrganizationProfile(
            profile_id=payload.get("profile_id"),
            profile_version=payload.get("profile_version", payload.get("version")),
            family=(payload.get("family") or {}),
        )
    except (TypeError, ValueError, re.error) as exc:
        return None, evidence_refs, (f"invalid_profile:{type(exc).__name__}",)
    return profile, evidence_refs, ()


class RFAOrganizationProfileValidityRule(BaseRules):
    """Validate the explicit versioned customer/organization rule profile."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.STANDARD.PROFILE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("organization_rules",),
        severity_policy="rfa_organization_profile_validity",
        known_limitations=(
            "Validates only the profile subset currently interpreted by BIMAP Family Audit rules.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        profile, evidence_refs, errors = resolve_organization_profile(context)
        return self.result(
            RuleStatus.PASS if profile is not None and not errors else RuleStatus.FAIL,
            evidence_refs=evidence_refs,
            observed_value=(
                None
                if profile is None
                else {
                    "profile_id": profile.profile_id,
                    "profile_version": profile.profile_version,
                }
            ),
            expected_value={"valid_profile_count": 1},
            metrics={"validation_errors": list(errors)},
        )


class RFAIdentityStandardsRule(BaseRules):
    """Compare family identity/type names only with explicitly configured policy."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.STANDARD.IDENTITY.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity", "organization_rules"),
        severity_policy="rfa_identity_standard_comparison",
        known_limitations=(
            "Regex and allowed-category checks are exact profile comparisons; BIMAP does not infer naming conventions.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        profile, policy_refs, errors = resolve_organization_profile(context)
        identity_items = context.group("family_identity")
        if profile is None:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple([*refs(identity_items), *policy_refs]),
                metrics={"profile_errors": list(errors)},
            )

        policy = profile.section("identity")
        if not policy:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple([*refs(identity_items), *policy_refs]),
                metrics={"reason": "identity_policy_not_configured"},
            )

        violations: list[dict[str, Any]] = []
        checks = 0
        observed_name = family_name(context)
        observed_category = family_category(context)

        family_pattern = policy.get("family_name_regex")
        if isinstance(family_pattern, str):
            checks += 1
            if observed_name is None or re.fullmatch(family_pattern, observed_name) is None:
                violations.append(
                    {
                        "field": "family_name",
                        "observed": observed_name,
                        "policy": family_pattern,
                    }
                )

        allowed_categories = policy.get("allowed_categories")
        if allowed_categories is not None and not isinstance(
            allowed_categories, (str, bytes, bytearray, Mapping)
        ):
            allowed = tuple(str(item).strip() for item in allowed_categories)
            checks += 1
            if (
                observed_category is None
                or canonical_name(observed_category)
                not in {canonical_name(item) for item in allowed}
            ):
                violations.append(
                    {
                        "field": "category",
                        "observed": observed_category,
                        "allowed": list(allowed),
                    }
                )

        type_pattern = policy.get("type_name_regex")
        type_records = group_mappings(context, "type_catalog")
        if (
            isinstance(type_pattern, str)
            and not type_records
            and section_assessed(context, "type_catalog") is not True
        ):
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=tuple([*refs(identity_items), *policy_refs]),
                observed_value={
                    "family_name": observed_name,
                    "category": observed_category,
                    "type_count": 0,
                },
                expected_value={
                    "profile_id": profile.profile_id,
                    "profile_version": profile.profile_version,
                },
                metrics={"reason": "type_catalog_not_confirmed_assessed"},
            )

        if isinstance(type_pattern, str):
            for item, record in type_records:
                checks += 1
                name = type_name(record)
                if name is None or re.fullmatch(type_pattern, name) is None:
                    violations.append(
                        {
                            "field": "type_name",
                            "evidence_id": item.evidence_id,
                            "observed": name,
                            "policy": type_pattern,
                        }
                    )

        if checks == 0:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple([*refs(identity_items), *policy_refs]),
                metrics={"reason": "no_supported_identity_comparisons_configured"},
            )

        evidence_refs = tuple(
            [
                *refs(identity_items),
                *(item.evidence_id for item, _ in type_records),
                *policy_refs,
            ]
        )
        return self.result(
            RuleStatus.FAIL if violations else RuleStatus.PASS,
            evidence_refs=evidence_refs,
            observed_value={
                "family_name": observed_name,
                "category": observed_category,
                "type_count": len(type_records),
            },
            expected_value={
                "profile_id": profile.profile_id,
                "profile_version": profile.profile_version,
            },
            metrics={"checks": checks, "violations": violations},
        )


RFA_STANDARDS_RULES: tuple[BaseRules, ...] = (
    RFAOrganizationProfileValidityRule(),
    RFAIdentityStandardsRule(),
)


__all__ = [
    "RFAOrganizationProfile",
    "resolve_organization_profile",
    "RFAOrganizationProfileValidityRule",
    "RFAIdentityStandardsRule",
    "RFA_STANDARDS_RULES",
]
