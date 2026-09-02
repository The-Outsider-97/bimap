"""
Deterministic Requirement-Evidence Matrix logic for BIMAP BIM QA.

The Requirement-Evidence Matrix is the core analytical artifact of BIM QA.  At
this engine layer it is responsible for validating and indexing already-formed,
versioned :class:`RequirementContract` assessments against one normalized
:class:`AuditContext`.

It deliberately does *not*:
- parse raw customer files;
- invent requirement identifiers or requirement text;
- convert generic rule results into requirement assessments without an explicit
  requirement-specific mapping policy;
- serialize CSV/Excel/report artifacts (that belongs to ``reporting/``);
- collapse pass/warn/fail/unknown/not_applicable into an opaque quality score;
- call SLAI or any outer application/infrastructure service.

This separation is important because the current public RequirementContract is
already authoritative for the matrix row semantics, while the repository's
``domain/requirements/models.py`` is still a scaffold.  Using the stable contract
here avoids duplicating or fabricating an unstabilized requirement domain model.

Dependency direction
--------------------
contracts.requirement + audit_engine.context
                ↓
audit_engine.bim_qa.requirement_matrix
                ↓
audit_engine.bim_qa.auditor / reporting serializers
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...contracts.requirement import *
from ...contracts.utils.contracts_errors import ContractError
from ...domain.products.models import ProductCode
from ..context import AuditContext
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP BIM QA Requirement Matrix")
printer = PrettyPrinter()

_COMPONENT = "bim_qa_requirement_matrix"
_EXPECTED_PRODUCT = ProductCode.BIM_QA


def _require_project_context(context: AuditContext, *, operation: str) -> AuditContext:
    """Require a project-capable AuditContext for BIM QA matrix evaluation."""
    if not isinstance(context, AuditContext):
        raise UnsupportedEngineInputError(
            "Requirement-Evidence Matrix evaluation requires an AuditContext.",
            component=_COMPONENT,
            operation=operation,
            field="context",
            context={"received_type": type(context).__name__},
        )

    if context.product_code is not _EXPECTED_PRODUCT:
        raise EngineValidationError(
            "Requirement-Evidence Matrix evaluation requires BIM QA product scope.",
            component=_COMPONENT,
            operation=operation,
            field="product_code",
            context={
                "received": str(getattr(context.product_code, "value", context.product_code)),
                "expected": _EXPECTED_PRODUCT.value,
            },
        )
    if context.project_id is None:
        raise EngineValidationError(
            "Requirement-Evidence Matrix evaluation requires project identity in AuditContext.",
            component=_COMPONENT,
            operation=operation,
            field="project_id",
            context={"product_code": context.product_code.value},
        )
    return context


def _normalize_requirements(
    requirements: Iterable[RequirementContract],
    *,
    operation: str,
) -> tuple[RequirementContract, ...]:
    """Normalize matrix rows and enforce stable requirement identity uniqueness."""
    if isinstance(requirements, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            "requirements must be an iterable of RequirementContract values.",
            component=_COMPONENT,
            operation=operation,
            field="requirements",
            context={"received_type": type(requirements).__name__},
        )

    try:
        items = tuple(requirements)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            "requirements must be iterable.",
            component=_COMPONENT,
            operation=operation,
            field="requirements",
            context={"received_type": type(requirements).__name__},
            cause=exc,
        ) from exc

    seen: set[str] = set()
    for index, requirement in enumerate(items):
        if not isinstance(requirement, RequirementContract):
            raise UnsupportedEngineInputError(
                "Requirement-Evidence Matrix accepts RequirementContract values only.",
                component=_COMPONENT,
                operation=operation,
                field=f"requirements[{index}]",
                context={"received_type": type(requirement).__name__},
            )

        if requirement.requirement_id in seen:
            raise EngineIntegrityError(
                "Requirement-Evidence Matrix contains duplicate requirement identifiers.",
                component=_COMPONENT,
                operation=operation,
                field="requirement_id",
                context={"requirement_id": requirement.requirement_id},
            )
        seen.add(requirement.requirement_id)

    return items


def _parse_status(value: AssessmentStatus | str) -> AssessmentStatus:
    """Translate contract validation failures into the audit-engine error surface."""
    try:
        return AssessmentStatus.parse(value)
    except ContractError as exc:
        raise EngineValidationError(
            "Invalid requirement assessment status.",
            component=_COMPONENT,
            operation="parse_status",
            field="assessment",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc


@dataclass(frozen=True, slots=True)
class RequirementMatrixSummary:
    """Non-scoring summary of one evaluated Requirement-Evidence Matrix.

    ``assessment_coverage`` is the proportion of applicable requirements that
    have a resolved pass/warn/fail state.  It is explicitly a completeness
    measure, not a BIM quality score.  ``None`` is returned when there are no
    applicable requirements, avoiding a mathematically misleading denominator.
    """

    requirement_count: int
    applicable_count: int
    resolved_count: int
    unknown_count: int
    not_applicable_count: int
    referenced_requirement_count: int
    referenced_evidence_count: int
    evidence_link_count: int
    assessment_coverage: float | None
    status_counts: Mapping[str, int]
    automation_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic primitive summary data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing requirement matrix summary",
            event="requirement_matrix_summary_to_dict_start",
        )
        return {
            "requirement_count": self.requirement_count,
            "applicable_count": self.applicable_count,
            "resolved_count": self.resolved_count,
            "unknown_count": self.unknown_count,
            "not_applicable_count": self.not_applicable_count,
            "referenced_requirement_count": self.referenced_requirement_count,
            "referenced_evidence_count": self.referenced_evidence_count,
            "evidence_link_count": self.evidence_link_count,
            "assessment_coverage": self.assessment_coverage,
            "status_counts": dict(self.status_counts),
            "automation_counts": dict(self.automation_counts),
        }


@dataclass(frozen=True, slots=True)
class RequirementMatrixResult:
    """Immutable evaluated matrix with deterministic evidence reverse-indexing."""

    requirements: tuple[RequirementContract, ...]
    summary: RequirementMatrixSummary
    evidence_to_requirements: Mapping[str, tuple[str, ...]]

    def get(self, requirement_id: str) -> RequirementContract | None:
        """Resolve one matrix row by stable requirement identifier."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving requirement matrix row",
            event="requirement_matrix_result_get_start",
        )
        target = require_engine_text(
            requirement_id,
            field="requirement_id",
            error_type=EngineValidationError,
        )
        for requirement in self.requirements:
            if requirement.requirement_id == target:
                return requirement
        return None

    def require(self, requirement_id: str) -> RequirementContract:
        """Resolve one required matrix row or fail with an integrity error."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Requiring requirement matrix row",
            event="requirement_matrix_result_require_start",
        )
        requirement = self.get(requirement_id)
        if requirement is not None:
            return requirement
        raise EngineIntegrityError(
            "Requirement identifier is absent from the evaluated matrix.",
            component=_COMPONENT,
            operation="require",
            field="requirement_id",
            context={"requirement_id": str(requirement_id).strip()},
        )

    def for_status(self, status: AssessmentStatus | str) -> tuple[RequirementContract, ...]:
        """Return rows with one canonical assessment state in matrix order."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Filtering requirement matrix by status",
            event="requirement_matrix_result_for_status_start",
        )
        target = _parse_status(status)
        return tuple(
            requirement
            for requirement in self.requirements
            if requirement.assessment is target
        )

    def for_evidence(self, evidence_id: str) -> tuple[RequirementContract, ...]:
        """Return rows that reference one normalized evidence identifier."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Filtering requirement matrix by evidence reference",
            event="requirement_matrix_result_for_evidence_start",
        )
        target = require_engine_text(
            evidence_id,
            field="evidence_id",
            error_type=EngineValidationError,
        )
        requirement_ids = self.evidence_to_requirements.get(target, ())
        if not requirement_ids:
            return ()
        index = {item.requirement_id: item for item in self.requirements}
        return tuple(index[requirement_id] for requirement_id in requirement_ids)

    def to_dict(self) -> dict[str, Any]:
        """Return engine-level JSON-safe matrix data; reporting owns CSV/layout."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing evaluated requirement matrix",
            event="requirement_matrix_result_to_dict_start",
            context={"requirement_count": len(self.requirements)},
        )
        payload = {
            "requirements": [item.to_dict() for item in self.requirements],
            "summary": self.summary.to_dict(),
            "evidence_to_requirements": {
                evidence_id: list(requirement_ids)
                for evidence_id, requirement_ids in self.evidence_to_requirements.items()
            },
        }
        primitive = to_engine_primitive(payload, field="requirement_matrix")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "Requirement matrix did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="requirement_matrix",
            )
        return primitive


class RequirementMatrix:
    """Validate, index, and summarize BIM QA requirement assessments.

    The matrix consumes authoritative RequirementContract rows.  It does not
    guess assessment state from arbitrary evidence or from a generic RuleResult.
    Requirement-specific assessment logic must construct a RequirementContract
    explicitly before this matrix accepts it.
    """

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing BIM QA requirement matrix",
            event="requirement_matrix_init_start",
        )
        logger.debug({"event": "requirement_matrix_initialized"})

    def validate(
        self,
        requirements: Iterable[RequirementContract],
        *,
        context: AuditContext,
    ) -> tuple[RequirementContract, ...]:
        """Validate row identity and require every evidence reference to resolve."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating Requirement-Evidence Matrix",
            event="requirement_matrix_validate_start",
        )
        audit_context = _require_project_context(context, operation="validate")
        items = _normalize_requirements(requirements, operation="validate")
        evidence_ids = set(audit_context.evidence_ids)

        for requirement in items:
            unresolved = tuple(
                evidence_id
                for evidence_id in requirement.evidence_refs
                if evidence_id not in evidence_ids
            )
            if unresolved:
                raise EngineIntegrityError(
                    "Requirement assessment references evidence absent from AuditContext.",
                    component=_COMPONENT,
                    operation="validate",
                    field="evidence_refs",
                    context={
                        "requirement_id": requirement.requirement_id,
                        "unresolved_evidence_refs": unresolved,
                    },
                )

        logger.debug(
            {
                "event": "requirement_matrix_validated",
                "requirement_count": len(items),
                "context_evidence_count": audit_context.evidence_count,
            }
        )
        return items

    def evaluate(
        self,
        requirements: Iterable[RequirementContract],
        *,
        context: AuditContext,
    ) -> RequirementMatrixResult:
        """Build the deterministic matrix index and non-scoring coverage summary."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Evaluating Requirement-Evidence Matrix",
            event="requirement_matrix_evaluate_start",
        )
        items = self.validate(requirements, context=context)

        status_counts_counter = Counter(item.assessment.value for item in items)
        automation_counts_counter = Counter(item.automation_type.value for item in items)

        not_applicable_count = status_counts_counter.get(
            AssessmentStatus.NOT_APPLICABLE.value,
            0,
        )
        unknown_count = status_counts_counter.get(AssessmentStatus.UNKNOWN.value, 0)
        applicable_count = len(items) - not_applicable_count
        resolved_count = sum(
            status_counts_counter.get(status.value, 0)
            for status in (
                AssessmentStatus.PASS,
                AssessmentStatus.WARN,
                AssessmentStatus.FAIL,
            )
        )
        assessment_coverage = (
            None
            if applicable_count == 0
            else resolved_count / applicable_count
        )

        evidence_index: dict[str, list[str]] = {}
        evidence_link_count = 0
        referenced_requirement_count = 0
        for requirement in items:
            if requirement.evidence_refs:
                referenced_requirement_count += 1
            for evidence_id in requirement.evidence_refs:
                evidence_link_count += 1
                evidence_index.setdefault(evidence_id, []).append(requirement.requirement_id)

        frozen_index = MappingProxyType(
            {
                evidence_id: tuple(requirement_ids)
                for evidence_id, requirement_ids in evidence_index.items()
            }
        )
        status_counts = MappingProxyType(dict(sorted(status_counts_counter.items())))
        automation_counts = MappingProxyType(
            dict(sorted(automation_counts_counter.items()))
        )

        summary = RequirementMatrixSummary(
            requirement_count=len(items),
            applicable_count=applicable_count,
            resolved_count=resolved_count,
            unknown_count=unknown_count,
            not_applicable_count=not_applicable_count,
            referenced_requirement_count=referenced_requirement_count,
            referenced_evidence_count=len(frozen_index),
            evidence_link_count=evidence_link_count,
            assessment_coverage=assessment_coverage,
            status_counts=status_counts,
            automation_counts=automation_counts,
        )
        result = RequirementMatrixResult(
            requirements=items,
            summary=summary,
            evidence_to_requirements=frozen_index,
        )

        logger.info(
            {
                "event": "requirement_matrix_evaluated",
                "requirement_count": len(items),
                "applicable_count": applicable_count,
                "resolved_count": resolved_count,
                "unknown_count": unknown_count,
                "not_applicable_count": not_applicable_count,
                "referenced_evidence_count": len(frozen_index),
            }
        )
        return result


__all__ = [
    "RequirementMatrixSummary",
    "RequirementMatrixResult",
    "RequirementMatrix",
]


if __name__ == "__main__":
    print("\n=== Running BIM QA Requirement Matrix Self-Test ===\n")
    printer.status("TEST", "Requirement matrix module initialized", "info")

    context = AuditContext(product_code=ProductCode.BIM_QA, project_id="PROJECT-TEST")
    result = RequirementMatrix().evaluate((), context=context)
    assert result.summary.requirement_count == 0
    assert result.summary.assessment_coverage is None
    printer.status("PASS", "Empty requirement matrix evaluation", "success")

    print("\n=== Test ran successfully ===\n")