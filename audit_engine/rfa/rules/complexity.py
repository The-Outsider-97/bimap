"""Calibrated geometry/complexity rules for BIMAP Revit Family Audit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...context import AuditContext
from ...rules.base import BaseRules, RuleDefinition, RuleResult, RuleStatus
from ....domain.products.models import ProductCode
from ._helpers import (
    canonical_key,
    group_mappings,
    metric_name,
    metric_value,
    section_assessed,
)
from .standards import resolve_organization_profile


_PRODUCT = ProductCode.FAMILY_AUDIT
_VERSION = "1.0.0"


class RFAComplexityMetricIntegrityRule(BaseRules):
    """Validate that published complexity records identify a metric and numeric value."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.COMPLEXITY.METRIC.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_complexity_metric_integrity",
        known_limitations=(
            "The rule validates metric observability only; it does not impose uncalibrated complexity thresholds.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        records = group_mappings(context, "geometry_metrics")
        if not records:
            assessed = section_assessed(context, "geometry_metrics")
            return self.result(
                RuleStatus.NOT_APPLICABLE if assessed is True else RuleStatus.UNKNOWN,
                evidence_refs=tuple(item.evidence_id for item in context.group("family_identity")),
                observed_value={"metric_count": 0},
                metrics={"section_assessed": assessed},
            )

        invalid: list[dict[str, Any]] = []
        seen: dict[str, str] = {}
        duplicates: list[dict[str, str]] = []
        for item, record in records:
            name = metric_name(record)
            value = metric_value(record)
            if name is None or value is None:
                invalid.append(
                    {
                        "evidence_id": item.evidence_id,
                        "missing_name": name is None,
                        "missing_numeric_value": value is None,
                    }
                )
                continue
            key = canonical_key(name)
            previous = seen.get(key)
            if previous is None:
                seen[key] = item.evidence_id
            else:
                duplicates.append(
                    {
                        "metric": name,
                        "first_evidence_id": previous,
                        "duplicate_evidence_id": item.evidence_id,
                    }
                )

        status = RuleStatus.FAIL if invalid or duplicates else RuleStatus.PASS
        return self.result(
            status,
            evidence_refs=tuple(item.evidence_id for item, _ in records),
            observed_value={
                "metric_count": len(records),
                "invalid_metric_count": len(invalid),
                "duplicate_metric_count": len(duplicates),
            },
            expected_value={
                "invalid_metric_count": 0,
                "duplicate_metric_count": 0,
            },
            metrics={"invalid_metrics": invalid, "duplicates": duplicates},
        )


class RFACalibratedComplexityRule(BaseRules):
    """
    Apply complexity thresholds only when supplied by an explicit versioned
    organization profile. BIMAP does not manufacture generic polygon, vertex,
    volume, family-size, nested-component, or connector thresholds.
    """

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.COMPLEXITY.CALIBRATED.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity", "organization_rules"),
        severity_policy="rfa_calibrated_complexity",
        known_limitations=(
            "Thresholds are profile-specific and comparison-only; metric semantics/units must be defined by the extraction/profile contract.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        profile, policy_refs, errors = resolve_organization_profile(context)
        records = group_mappings(context, "geometry_metrics")
        evidence_refs = tuple(
            [*(item.evidence_id for item, _ in records), *policy_refs]
        )
        if profile is None:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=evidence_refs,
                metrics={"profile_errors": list(errors)},
            )

        complexity = profile.section("complexity")
        calibrations = complexity.get("metrics") if complexity else None
        if not isinstance(calibrations, Mapping) or not calibrations:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=evidence_refs,
                metrics={"reason": "complexity_calibration_not_configured"},
            )

        observed: dict[str, tuple[str, float, str | None]] = {}
        for item, record in records:
            name = metric_name(record)
            value = metric_value(record)
            if name is None or value is None:
                continue
            unit = record.get("unit")
            observed[canonical_key(name)] = (
                item.evidence_id,
                value,
                str(unit).strip() if isinstance(unit, str) and unit.strip() else None,
            )

        evaluations: list[dict[str, Any]] = []
        missing: list[str] = []
        has_fail = False
        has_warn = False

        for raw_name, raw_calibration in calibrations.items():
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            if not isinstance(raw_calibration, Mapping):
                continue

            key = canonical_key(raw_name)
            record = observed.get(key)
            if record is None:
                missing.append(raw_name)
                continue

            evidence_id, value, observed_unit = record
            warn_above = raw_calibration.get("warn_above")
            fail_above = raw_calibration.get("fail_above")
            expected_unit = raw_calibration.get("unit")

            if isinstance(expected_unit, str) and expected_unit.strip():
                if observed_unit is None:
                    missing.append(f"{raw_name}:unit_unavailable")
                    continue
                if canonical_key(expected_unit) != canonical_key(observed_unit):
                    missing.append(f"{raw_name}:unit_mismatch")
                    continue

            disposition = "pass"
            if (
                isinstance(fail_above, (int, float))
                and not isinstance(fail_above, bool)
                and value > float(fail_above)
            ):
                disposition = "fail"
                has_fail = True
            elif (
                isinstance(warn_above, (int, float))
                and not isinstance(warn_above, bool)
                and value > float(warn_above)
            ):
                disposition = "warn"
                has_warn = True

            evaluations.append(
                {
                    "metric": raw_name,
                    "evidence_id": evidence_id,
                    "value": value,
                    "unit": observed_unit,
                    "warn_above": warn_above,
                    "fail_above": fail_above,
                    "disposition": disposition,
                }
            )

        if has_fail:
            status = RuleStatus.FAIL
        elif missing:
            status = RuleStatus.UNKNOWN
        elif has_warn:
            status = RuleStatus.WARN
        else:
            status = RuleStatus.PASS

        return self.result(
            status,
            evidence_refs=evidence_refs,
            observed_value={"available_metric_count": len(observed)},
            expected_value={
                "profile_id": profile.profile_id,
                "profile_version": profile.profile_version,
                "calibrated_metric_count": len(calibrations),
            },
            metrics={
                "evaluations": evaluations,
                "missing_calibrated_metrics": missing,
            },
        )


RFA_COMPLEXITY_RULES: tuple[BaseRules, ...] = (
    RFAComplexityMetricIntegrityRule(),
    RFACalibratedComplexityRule(),
)


__all__ = [
    "RFAComplexityMetricIntegrityRule",
    "RFACalibratedComplexityRule",
    "RFA_COMPLEXITY_RULES",
]
