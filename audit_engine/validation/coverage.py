"""
Cross-cutting, non-scoring requirement/evidence coverage for BIMAP validation.

Coverage is treated as a *completeness and traceability measure*, not as a proxy
for BIM quality or compliance.  This module therefore does not collapse
pass/warn/fail/unknown/not-applicable states, finding severity, confidence, or
metadata richness into one opaque score.

The current ``domain/reports/coverage.py`` and ``domain/requirements/models.py``
remain scaffolds.  This module consequently uses the already-stable
``RequirementContract`` and ``FindingContract`` surfaces plus ``AuditContext``
instead of inventing semantics for those unfinished domain types.

Metrics are intentionally explicit:

``assessment_coverage``
    resolved pass/warn/fail requirements divided by applicable requirements
    (all requirements except ``not_applicable``). ``unknown`` remains unresolved.

``evidence_reference_coverage``
    accepted evidence records referenced by at least one validated requirement or
    finding divided by all accepted evidence records.

``source_reference_coverage``
    accepted source identities represented by at least one referenced evidence
    record divided by all accepted source identities.

The latter two describe *traceability use*, not evidential sufficiency.  An
unreferenced evidence record is not automatically wrong; it may simply be outside
current rule/finding/requirement scope.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...contracts.finding import FindingContract
from ...contracts.requirement import *
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ..context import AuditContext
from ..rules.base import RuleResult
from .evidence import EvidenceValidation, EvidenceValidationResult
from .findings import FindingsValidation, FindingsValidationResult
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Validation Coverage")
printer = PrettyPrinter()

_COMPONENT = "validation_coverage"


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _normalize_requirements(
    requirements: Iterable[RequirementContract],
) -> tuple[RequirementContract, ...]:
    """Normalize requirement contracts and enforce stable identity uniqueness."""
    if isinstance(requirements, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            "requirements must be an iterable of RequirementContract values.",
            component=_COMPONENT,
            operation="normalize_requirements",
            field="requirements",
            context={"received_type": type(requirements).__name__},
        )
    try:
        items = tuple(requirements)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            "requirements must be iterable.",
            component=_COMPONENT,
            operation="normalize_requirements",
            field="requirements",
            context={"received_type": type(requirements).__name__},
            cause=exc,
        ) from exc

    seen_ids: set[str] = set()
    for index, requirement in enumerate(items):
        if not isinstance(requirement, RequirementContract):
            raise UnsupportedEngineInputError(
                "Coverage calculation accepts RequirementContract values only.",
                component=_COMPONENT,
                operation="normalize_requirements",
                field=f"requirements[{index}]",
                context={"received_type": type(requirement).__name__},
            )
        if requirement.requirement_id in seen_ids:
            raise EngineIntegrityError(
                "Coverage input contains duplicate requirement identifiers.",
                component=_COMPONENT,
                operation="normalize_requirements",
                field="requirement_id",
                context={"requirement_id": requirement.requirement_id},
            )
        seen_ids.add(requirement.requirement_id)
    return items


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    """Explicit non-scoring coverage metrics for one validated audit context."""

    evidence_count: int
    source_count: int
    finding_count: int
    requirement_count: int
    applicable_requirement_count: int
    resolved_requirement_count: int
    unknown_requirement_count: int
    not_applicable_requirement_count: int
    findings_with_evidence_count: int
    requirements_with_evidence_count: int
    finding_evidence_link_count: int
    requirement_evidence_link_count: int
    referenced_evidence_count: int
    unreferenced_evidence_count: int
    referenced_source_count: int
    unreferenced_source_count: int
    assessment_coverage: float | None
    evidence_reference_coverage: float | None
    source_reference_coverage: float | None
    requirement_status_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating coverage summary",
            event="validation_coverage_summary_validate_start",
        )
        integer_fields = (
            "evidence_count",
            "source_count",
            "finding_count",
            "requirement_count",
            "applicable_requirement_count",
            "resolved_requirement_count",
            "unknown_requirement_count",
            "not_applicable_requirement_count",
            "findings_with_evidence_count",
            "requirements_with_evidence_count",
            "finding_evidence_link_count",
            "requirement_evidence_link_count",
            "referenced_evidence_count",
            "unreferenced_evidence_count",
            "referenced_source_count",
            "unreferenced_source_count",
        )
        for field in integer_fields:
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EngineIntegrityError(
                    "Coverage summary contains an invalid count.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={"received_type": type(value).__name__},
                )

        if self.referenced_evidence_count + self.unreferenced_evidence_count != self.evidence_count:
            raise EngineIntegrityError(
                "Coverage summary does not partition accepted evidence.",
                component=_COMPONENT,
                operation="validate_summary",
                field="evidence_count",
            )
        if self.referenced_source_count + self.unreferenced_source_count != self.source_count:
            raise EngineIntegrityError(
                "Coverage summary does not partition accepted sources.",
                component=_COMPONENT,
                operation="validate_summary",
                field="source_count",
            )
        if self.applicable_requirement_count + self.not_applicable_requirement_count != self.requirement_count:
            raise EngineIntegrityError(
                "Coverage summary does not partition applicable requirements.",
                component=_COMPONENT,
                operation="validate_summary",
                field="requirement_count",
            )
        if self.resolved_requirement_count + self.unknown_requirement_count != self.applicable_requirement_count:
            raise EngineIntegrityError(
                "Coverage summary does not partition applicable requirement assessment states.",
                component=_COMPONENT,
                operation="validate_summary",
                field="applicable_requirement_count",
            )
        if self.findings_with_evidence_count > self.finding_count:
            raise EngineIntegrityError(
                "Coverage summary has more evidence-linked findings than findings.",
                component=_COMPONENT,
                operation="validate_summary",
                field="findings_with_evidence_count",
            )
        if self.requirements_with_evidence_count > self.requirement_count:
            raise EngineIntegrityError(
                "Coverage summary has more evidence-linked requirements than requirements.",
                component=_COMPONENT,
                operation="validate_summary",
                field="requirements_with_evidence_count",
            )

        expected_ratios = {
            "assessment_coverage": _ratio(
                self.resolved_requirement_count,
                self.applicable_requirement_count,
            ),
            "evidence_reference_coverage": _ratio(
                self.referenced_evidence_count,
                self.evidence_count,
            ),
            "source_reference_coverage": _ratio(
                self.referenced_source_count,
                self.source_count,
            ),
        }
        for field, expected in expected_ratios.items():
            actual = getattr(self, field)
            if actual != expected:
                raise EngineIntegrityError(
                    "Coverage ratio is inconsistent with its explicit counts.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={"expected": expected, "received": actual},
                )

        if not isinstance(self.requirement_status_counts, Mapping):
            raise EngineIntegrityError(
                "requirement_status_counts must be a mapping.",
                component=_COMPONENT,
                operation="validate_summary",
                field="requirement_status_counts",
                context={"received_type": type(self.requirement_status_counts).__name__},
            )
        normalized_status_counts = dict(self.requirement_status_counts)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in normalized_status_counts.values()
        ) or sum(normalized_status_counts.values()) != self.requirement_count:
            raise EngineIntegrityError(
                "Requirement status counts do not partition the requirement collection.",
                component=_COMPONENT,
                operation="validate_summary",
                field="requirement_status_counts",
            )
        object.__setattr__(
            self,
            "requirement_status_counts",
            MappingProxyType(normalized_status_counts),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic primitive coverage data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing validation coverage summary",
            event="validation_coverage_summary_to_dict_start",
        )
        return {
            "evidence_count": self.evidence_count,
            "source_count": self.source_count,
            "finding_count": self.finding_count,
            "requirement_count": self.requirement_count,
            "applicable_requirement_count": self.applicable_requirement_count,
            "resolved_requirement_count": self.resolved_requirement_count,
            "unknown_requirement_count": self.unknown_requirement_count,
            "not_applicable_requirement_count": self.not_applicable_requirement_count,
            "findings_with_evidence_count": self.findings_with_evidence_count,
            "requirements_with_evidence_count": self.requirements_with_evidence_count,
            "finding_evidence_link_count": self.finding_evidence_link_count,
            "requirement_evidence_link_count": self.requirement_evidence_link_count,
            "referenced_evidence_count": self.referenced_evidence_count,
            "unreferenced_evidence_count": self.unreferenced_evidence_count,
            "referenced_source_count": self.referenced_source_count,
            "unreferenced_source_count": self.unreferenced_source_count,
            "assessment_coverage": self.assessment_coverage,
            "evidence_reference_coverage": self.evidence_reference_coverage,
            "source_reference_coverage": self.source_reference_coverage,
            "requirement_status_counts": dict(self.requirement_status_counts),
        }


@dataclass(frozen=True, slots=True)
class CoverageResult:
    """Immutable coverage summary with deterministic referenced/unreferenced sets."""

    summary: CoverageSummary
    evidence_validation: EvidenceValidationResult
    findings_validation: FindingsValidationResult
    requirements: tuple[RequirementContract, ...]
    referenced_evidence_ids: tuple[str, ...]
    unreferenced_evidence_ids: tuple[str, ...]
    referenced_source_ids: tuple[str, ...]
    unreferenced_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating coverage result",
            event="validation_coverage_result_validate_start",
        )
        if not isinstance(self.summary, CoverageSummary):
            raise EngineIntegrityError(
                "CoverageResult requires CoverageSummary.",
                component=_COMPONENT,
                operation="validate_result",
                field="summary",
                context={"received_type": type(self.summary).__name__},
            )
        if not isinstance(self.evidence_validation, EvidenceValidationResult):
            raise EngineIntegrityError(
                "CoverageResult requires EvidenceValidationResult.",
                component=_COMPONENT,
                operation="validate_result",
                field="evidence_validation",
                context={"received_type": type(self.evidence_validation).__name__},
            )
        if not isinstance(self.findings_validation, FindingsValidationResult):
            raise EngineIntegrityError(
                "CoverageResult requires FindingsValidationResult.",
                component=_COMPONENT,
                operation="validate_result",
                field="findings_validation",
                context={"received_type": type(self.findings_validation).__name__},
            )
        if len(self.requirements) != self.summary.requirement_count:
            raise EngineIntegrityError(
                "Coverage requirement count does not match summary.",
                component=_COMPONENT,
                operation="validate_result",
                field="requirements",
            )
        if len(self.referenced_evidence_ids) != self.summary.referenced_evidence_count or len(self.unreferenced_evidence_ids) != self.summary.unreferenced_evidence_count:
            raise EngineIntegrityError(
                "Coverage evidence ID partitions do not match summary counts.",
                component=_COMPONENT,
                operation="validate_result",
                field="referenced_evidence_ids",
            )
        if set(self.referenced_evidence_ids).intersection(self.unreferenced_evidence_ids):
            raise EngineIntegrityError(
                "Referenced and unreferenced evidence partitions overlap.",
                component=_COMPONENT,
                operation="validate_result",
                field="referenced_evidence_ids",
            )
        if len(self.referenced_source_ids) != self.summary.referenced_source_count or len(self.unreferenced_source_ids) != self.summary.unreferenced_source_count:
            raise EngineIntegrityError(
                "Coverage source ID partitions do not match summary counts.",
                component=_COMPONENT,
                operation="validate_result",
                field="referenced_source_ids",
            )
        if set(self.referenced_source_ids).intersection(self.unreferenced_source_ids):
            raise EngineIntegrityError(
                "Referenced and unreferenced source partitions overlap.",
                component=_COMPONENT,
                operation="validate_result",
                field="referenced_source_ids",
            )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe coverage output."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing validation coverage result",
            event="validation_coverage_result_to_dict_start",
        )
        payload = {
            "summary": self.summary.to_dict(),
            "evidence_validation": self.evidence_validation.to_dict(),
            "findings_validation": self.findings_validation.to_dict(),
            "requirements": [requirement.to_dict() for requirement in self.requirements],
            "referenced_evidence_ids": list(self.referenced_evidence_ids),
            "unreferenced_evidence_ids": list(self.unreferenced_evidence_ids),
            "referenced_source_ids": list(self.referenced_source_ids),
            "unreferenced_source_ids": list(self.unreferenced_source_ids),
        }
        primitive = to_engine_primitive(payload, field="validation_coverage_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "Validation coverage result did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="validation_coverage_result",
            )
        return primitive


class ValidationCoverage:
    """Calculate explicit evidence/requirement/finding traceability coverage."""

    def __init__(
        self,
        *,
        evidence_validation: EvidenceValidation | None = None,
        findings_validation: FindingsValidation | None = None,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing validation coverage",
            event="validation_coverage_init_start",
        )
        if evidence_validation is not None and not isinstance(
            evidence_validation,
            EvidenceValidation,
        ):
            raise EngineConfigurationError(
                "evidence_validation must be EvidenceValidation or None.",
                component=_COMPONENT,
                operation="initialize",
                field="evidence_validation",
                context={"received_type": type(evidence_validation).__name__},
            )
        if findings_validation is not None and not isinstance(
            findings_validation,
            FindingsValidation,
        ):
            raise EngineConfigurationError(
                "findings_validation must be FindingsValidation or None.",
                component=_COMPONENT,
                operation="initialize",
                field="findings_validation",
                context={"received_type": type(findings_validation).__name__},
            )

        evidence_service = evidence_validation or EvidenceValidation()
        findings_service = findings_validation or FindingsValidation(
            evidence_validation=evidence_service
        )
        self._evidence_validation = evidence_service
        self._findings_validation = findings_service
        logger.debug({"event": "validation_coverage_initialized"})

    @property
    def evidence_validation(self) -> EvidenceValidation:
        """Return the evidence validation dependency."""
        return self._evidence_validation

    @property
    def findings_validation(self) -> FindingsValidation:
        """Return the findings validation dependency."""
        return self._findings_validation

    def calculate(
        self,
        context: AuditContext,
        *,
        findings: Iterable[FindingContract] = (),
        requirements: Iterable[RequirementContract] = (),
        rule_results: Iterable[RuleResult] | None = None,
    ) -> CoverageResult:
        """Calculate non-scoring traceability coverage for one accepted context."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Calculating validation coverage",
            event="validation_coverage_calculate_start",
        )
        if not isinstance(context, AuditContext):
            raise UnsupportedEngineInputError(
                "Coverage calculation requires an AuditContext.",
                component=_COMPONENT,
                operation="calculate",
                field="context",
                context={"received_type": type(context).__name__},
            )

        evidence_result = self._evidence_validation.validate(context)
        findings_result = self._findings_validation.validate(
            findings,
            context=context,
            rule_results=rule_results,
        )
        requirement_items = _normalize_requirements(requirements)

        requirement_referenced_evidence_ids: set[str] = set()
        requirement_evidence_link_count = 0
        requirements_with_evidence_count = 0
        for requirement in requirement_items:
            resolved_refs = self._evidence_validation.resolve_references(
                requirement.evidence_refs,
                context=context,
                field=f"requirements.{requirement.requirement_id}.evidence_refs",
                allow_empty=True,
            )
            if resolved_refs:
                requirements_with_evidence_count += 1
            for evidence_id in resolved_refs:
                requirement_referenced_evidence_ids.add(evidence_id)
                requirement_evidence_link_count += 1

        status_counts_counter = Counter(
            requirement.assessment.value for requirement in requirement_items
        )
        not_applicable_count = status_counts_counter.get(
            AssessmentStatus.NOT_APPLICABLE.value,
            0,
        )
        unknown_count = status_counts_counter.get(AssessmentStatus.UNKNOWN.value, 0)
        applicable_count = len(requirement_items) - not_applicable_count
        resolved_count = sum(
            status_counts_counter.get(status.value, 0)
            for status in (
                AssessmentStatus.PASS,
                AssessmentStatus.WARN,
                AssessmentStatus.FAIL,
            )
        )

        referenced_ids = set(requirement_referenced_evidence_ids)
        referenced_ids.update(findings_result.evidence_to_findings)

        referenced_evidence_ids = tuple(
            evidence_id
            for evidence_id in context.evidence_ids
            if evidence_id in referenced_ids
        )
        unreferenced_evidence_ids = tuple(
            evidence_id
            for evidence_id in context.evidence_ids
            if evidence_id not in referenced_ids
        )

        referenced_source_set = {
            item.source_file_id
            for item in context.evidence_items
            if item.evidence_id in referenced_ids
        }
        source_order = tuple(evidence_result.source_to_evidence)
        referenced_source_ids = tuple(
            source_id for source_id in source_order if source_id in referenced_source_set
        )
        unreferenced_source_ids = tuple(
            source_id for source_id in source_order if source_id not in referenced_source_set
        )

        summary = CoverageSummary(
            evidence_count=evidence_result.summary.evidence_count,
            source_count=evidence_result.summary.source_count,
            finding_count=findings_result.summary.finding_count,
            requirement_count=len(requirement_items),
            applicable_requirement_count=applicable_count,
            resolved_requirement_count=resolved_count,
            unknown_requirement_count=unknown_count,
            not_applicable_requirement_count=not_applicable_count,
            findings_with_evidence_count=findings_result.summary.findings_with_evidence_count,
            requirements_with_evidence_count=requirements_with_evidence_count,
            finding_evidence_link_count=findings_result.summary.evidence_link_count,
            requirement_evidence_link_count=requirement_evidence_link_count,
            referenced_evidence_count=len(referenced_evidence_ids),
            unreferenced_evidence_count=len(unreferenced_evidence_ids),
            referenced_source_count=len(referenced_source_ids),
            unreferenced_source_count=len(unreferenced_source_ids),
            assessment_coverage=_ratio(resolved_count, applicable_count),
            evidence_reference_coverage=_ratio(
                len(referenced_evidence_ids),
                evidence_result.summary.evidence_count,
            ),
            source_reference_coverage=_ratio(
                len(referenced_source_ids),
                evidence_result.summary.source_count,
            ),
            requirement_status_counts=MappingProxyType(
                dict(sorted(status_counts_counter.items()))
            ),
        )
        result = CoverageResult(
            summary=summary,
            evidence_validation=evidence_result,
            findings_validation=findings_result,
            requirements=requirement_items,
            referenced_evidence_ids=referenced_evidence_ids,
            unreferenced_evidence_ids=unreferenced_evidence_ids,
            referenced_source_ids=referenced_source_ids,
            unreferenced_source_ids=unreferenced_source_ids,
        )
        logger.info(
            {
                "event": "validation_coverage_calculated",
                "evidence_count": summary.evidence_count,
                "source_count": summary.source_count,
                "finding_count": summary.finding_count,
                "requirement_count": summary.requirement_count,
                "referenced_evidence_count": summary.referenced_evidence_count,
                "assessment_coverage": summary.assessment_coverage,
            }
        )
        return result


__all__ = [
    "CoverageSummary",
    "CoverageResult",
    "ValidationCoverage",
]


if __name__ == "__main__":
    from ...domain.products.models import ProductCode

    print("\n=== Running Validation Coverage Self-Test ===\n")
    printer.status("TEST", "Validation coverage module initialized", "info")

    context = AuditContext(product_code=ProductCode.FAMILY_AUDIT)
    result = ValidationCoverage().calculate(context)
    assert result.summary.evidence_count == 0
    assert result.summary.assessment_coverage is None
    assert result.summary.evidence_reference_coverage is None
    printer.status("PASS", "Empty-context coverage calculation", "success")

    print("\n=== Test ran successfully ===\n")