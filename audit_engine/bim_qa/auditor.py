"""
BIM QA deterministic audit coordinator.

``BIMQAAuditor`` composes three already-separated concerns:

1. a normalized :class:`AuditContext` containing project-capable evidence;
2. a frozen :class:`RulesExecutor` for deterministic rule execution; and
3. :class:`RequirementMatrix` validation/indexing for authoritative
   RequirementContract assessments.

The auditor remains an orchestration boundary inside ``audit_engine``.  It does
not parse uploads, normalize raw evidence, serialize reports, persist results,
invoke SLAI, or invent requirement/finding semantics that are absent from the
current contracts and rule result model.

Grounded finding construction
-----------------------------
The current generic ``RuleResult`` intentionally does not contain all fields
required by ``FindingContract`` (title, category, severity, explanation,
remediation, verification method, and calibrated confidence).  Synthesizing
those fields here would duplicate or fabricate rule-specific policy.  Therefore
finding construction is supported through an explicit injected ``finding_mapper``
callable.  When present, every returned FindingContract is checked against the
executed RuleResult and AuditContext so the mapping cannot silently detach a
finding from its deterministic evidence.

If no mapper is configured, deterministic rule results and the complete
Requirement-Evidence Matrix are still returned, while ``findings`` remains
empty.  This is intentional until a repository-level rule-to-finding mapping
policy is implemented.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from ...contracts.finding import FindingContract, FindingScope
from ...contracts.requirement import AutomationType, RequirementContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.products.models import ProductCode
from ..context import AuditContext
from ..rules.base import RuleResult
from ..rules.executor import RulesExecutor
from ..rules.versions import RuleVersion
from ..utils.engine_errors import (
    EngineConfigurationError,
    EngineError,
    EngineIntegrityError,
    EngineValidationError,
    UnsupportedEngineInputError,
)
from ..utils.engine_helpers import (
    announce_engine_action,
    lower_error_context,
    to_engine_primitive,
)
from .requirement_matrix import RequirementMatrix, RequirementMatrixResult
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP BIM QA Auditor")
printer = PrettyPrinter()

_COMPONENT = "bim_qa_auditor"
_EXPECTED_PRODUCT = ProductCode.BIM_QA

FindingMapper = Callable[[RuleResult, AuditContext], FindingContract | None]


def _require_bim_qa_context(context: AuditContext, *, operation: str) -> AuditContext:
    """Require an AuditContext capable of carrying project-level BIM QA scope."""
    if not isinstance(context, AuditContext):
        raise UnsupportedEngineInputError(
            "BIM QA auditing requires an AuditContext.",
            component=_COMPONENT,
            operation=operation,
            field="context",
            context={"received_type": type(context).__name__},
        )

    if context.product_code is not _EXPECTED_PRODUCT:
        raise EngineValidationError(
            "BIM QA auditor requires BIM QA product scope.",
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
            "BIM QA auditing requires project identity in AuditContext.",
            component=_COMPONENT,
            operation=operation,
            field="project_id",
            context={"product_code": context.product_code.value},
        )
    return context


def _result_status_value(result: RuleResult) -> str:
    return str(getattr(result.status, "value", result.status))


@dataclass(frozen=True, slots=True)
class BIMQAAuditResult:
    """Immutable grounded output of one deterministic BIM QA coordinator run."""

    product_code: ProductCode
    project_id: str
    rule_results: tuple[RuleResult, ...]
    requirement_matrix: RequirementMatrixResult
    findings: tuple[FindingContract, ...]
    rule_status_counts: Mapping[str, int]
    executed_rule_versions: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe audit output for later result/report layers."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing BIM QA audit result",
            event="bim_qa_result_to_dict_start",
            context={
                "rule_count": len(self.rule_results),
                "requirement_count": self.requirement_matrix.summary.requirement_count,
                "finding_count": len(self.findings),
            },
        )
        payload = {
            "product_code": self.product_code.value,
            "project_id": self.project_id,
            "rule_results": [result.to_dict() for result in self.rule_results],
            "requirement_matrix": self.requirement_matrix.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "rule_status_counts": dict(self.rule_status_counts),
            "executed_rule_versions": dict(self.executed_rule_versions),
        }
        primitive = to_engine_primitive(payload, field="bim_qa_audit_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "BIM QA audit result did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="bim_qa_audit_result",
            )
        return primitive


class BIMQAAuditor:
    """Coordinate deterministic BIM QA rule execution and requirement assessment data."""

    def __init__(
        self,
        executor: RulesExecutor,
        *,
        requirement_matrix: RequirementMatrix | None = None,
        finding_mapper: FindingMapper | None = None,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing BIM QA auditor",
            event="bim_qa_auditor_init_start",
        )
        if not isinstance(executor, RulesExecutor):
            raise EngineConfigurationError(
                "BIMQAAuditor requires a RulesExecutor.",
                component=_COMPONENT,
                operation="initialize",
                field="executor",
                context={"received_type": type(executor).__name__},
            )
        if requirement_matrix is not None and not isinstance(
            requirement_matrix,
            RequirementMatrix,
        ):
            raise EngineConfigurationError(
                "requirement_matrix must be a RequirementMatrix or None.",
                component=_COMPONENT,
                operation="initialize",
                field="requirement_matrix",
                context={"received_type": type(requirement_matrix).__name__},
            )
        if finding_mapper is not None and not callable(finding_mapper):
            raise EngineConfigurationError(
                "finding_mapper must be callable or None.",
                component=_COMPONENT,
                operation="initialize",
                field="finding_mapper",
                context={"received_type": type(finding_mapper).__name__},
            )

        self._executor = executor
        self._requirement_matrix = requirement_matrix or RequirementMatrix()
        self._finding_mapper = finding_mapper

        logger.info(
            {
                "event": "bim_qa_auditor_initialized",
                "has_finding_mapper": finding_mapper is not None,
                "registered_rule_count": len(executor.registry),
            }
        )

    @property
    def executor(self) -> RulesExecutor:
        """Return the frozen deterministic rule executor used by this auditor."""
        return self._executor

    @property
    def requirement_matrix(self) -> RequirementMatrix:
        """Return the Requirement-Evidence Matrix service used by this auditor."""
        return self._requirement_matrix

    @property
    def has_finding_mapper(self) -> bool:
        """Return whether rule-specific grounded finding mapping is configured."""
        return self._finding_mapper is not None

    def _execute(
        self,
        context: AuditContext,
        *,
        rule_ids: Iterable[str] | None,
        versions: Mapping[str, RuleVersion | str] | None,
    ) -> tuple[RuleResult, ...]:
        """Execute one current selection or an exact version-pinned replay."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing BIM QA deterministic rules",
            event="bim_qa_auditor_execute_start",
        )
        if rule_ids is not None and versions is not None:
            raise EngineValidationError(
                "rule_ids and versions are mutually exclusive audit selections.",
                component=_COMPONENT,
                operation="execute",
                field="rule_selection",
            )
        if versions is not None:
            return self._executor.execute_versioned(context, versions)
        return self._executor.execute(context, rule_ids=rule_ids)

    def _validate_mapped_finding(
        self,
        finding: FindingContract,
        *,
        result: RuleResult,
        context: AuditContext,
    ) -> FindingContract:
        """Require a mapper-produced finding to remain grounded in its rule result."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating grounded BIM QA finding",
            event="bim_qa_auditor_validate_finding_start",
            context={"rule_id": result.rule_id},
        )
        if not isinstance(finding, FindingContract):
            raise EngineIntegrityError(
                "finding_mapper returned an unsupported object type.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="finding",
                context={
                    "rule_id": result.rule_id,
                    "received_type": type(finding).__name__,
                },
            )
        finding_scope = str(getattr(finding.scope, "value", finding.scope))
        expected_scope = str(getattr(FindingScope.PROJECT, "value", FindingScope.PROJECT))
        if finding_scope != expected_scope:
            raise EngineIntegrityError(
                "BIM QA deterministic findings must use project scope.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="scope",
                context={
                    "rule_id": result.rule_id,
                    "received_scope": finding_scope,
                },
            )
        if finding.rule_id != result.rule_id:
            raise EngineIntegrityError(
                "Mapped finding rule identity does not match the executed rule result.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="rule_id",
                context={
                    "expected_rule_id": result.rule_id,
                    "received_rule_id": finding.rule_id,
                },
            )
        finding_status = str(getattr(finding.status, "value", finding.status))
        if finding_status != _result_status_value(result):
            raise EngineIntegrityError(
                "Mapped finding status does not match the executed rule result.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="status",
                context={
                    "rule_id": result.rule_id,
                    "expected_status": _result_status_value(result),
                    "received_status": finding_status,
                },
            )
        finding_automation_type = str(
            getattr(finding.automation_type, "value", finding.automation_type)
        )
        expected_automation_type = str(
            getattr(AutomationType.DETERMINISTIC, "value", AutomationType.DETERMINISTIC)
        )
        if finding_automation_type != expected_automation_type:
            raise EngineIntegrityError(
                "Findings mapped directly from RulesExecutor results must remain deterministic.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="automation_type",
                context={
                    "rule_id": result.rule_id,
                    "received": finding_automation_type,
                },
            )

        expected_observed = to_engine_primitive(
            result.observed_value,
            field="rule_result.observed_value",
        )
        expected_expected = to_engine_primitive(
            result.expected_value,
            field="rule_result.expected_value",
        )
        if finding.observed_value != expected_observed:
            raise EngineIntegrityError(
                "Mapped finding observed_value does not match its RuleResult.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="observed_value",
                context={"rule_id": result.rule_id},
            )
        if finding.expected_value != expected_expected:
            raise EngineIntegrityError(
                "Mapped finding expected_value does not match its RuleResult.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="expected_value",
                context={"rule_id": result.rule_id},
            )

        result_refs = set(result.evidence_refs)
        unresolved_from_result = tuple(
            evidence_id
            for evidence_id in finding.evidence_refs
            if evidence_id not in result_refs
        )
        if unresolved_from_result:
            raise EngineIntegrityError(
                "Mapped finding references evidence not cited by its RuleResult.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="evidence_refs",
                context={
                    "rule_id": result.rule_id,
                    "unresolved_evidence_refs": unresolved_from_result,
                },
            )

        context_ids = set(context.evidence_ids)
        unresolved_from_context = tuple(
            evidence_id
            for evidence_id in finding.evidence_refs
            if evidence_id not in context_ids
        )
        if unresolved_from_context:
            raise EngineIntegrityError(
                "Mapped BIM QA finding references evidence absent from AuditContext.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="evidence_refs",
                context={
                    "rule_id": result.rule_id,
                    "unresolved_evidence_refs": unresolved_from_context,
                },
            )
        return finding

    def _map_findings(
        self,
        results: tuple[RuleResult, ...],
        context: AuditContext,
    ) -> tuple[FindingContract, ...]:
        """Map deterministic rule results through the explicitly configured policy."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Mapping BIM QA rule results to grounded findings",
            event="bim_qa_auditor_map_findings_start",
            context={"rule_result_count": len(results)},
        )
        mapper = self._finding_mapper
        if mapper is None:
            logger.debug(
                {
                    "event": "bim_qa_finding_mapping_skipped",
                    "reason": "finding_mapper_not_configured",
                    "rule_result_count": len(results),
                }
            )
            return ()

        findings: list[FindingContract] = []
        seen_ids: set[str] = set()
        for result in results:
            try:
                finding = mapper(result, context)
            except EngineError:
                raise
            except ContractError as exc:
                raise EngineValidationError(
                    "BIM QA finding mapper produced an invalid FindingContract.",
                    component=_COMPONENT,
                    operation="map_findings",
                    field="finding_mapper",
                    context={"rule_id": result.rule_id, **lower_error_context(exc)},
                    cause=exc,
                ) from exc
            except Exception as exc:
                raise EngineError(
                    "BIM QA finding mapper failed with an unhandled implementation error.",
                    component=_COMPONENT,
                    operation="map_findings",
                    field="finding_mapper",
                    context={
                        "rule_id": result.rule_id,
                        "error_type": type(exc).__name__,
                    },
                    cause=exc,
                ) from exc

            if finding is None:
                continue
            validated = self._validate_mapped_finding(
                finding,
                result=result,
                context=context,
            )
            if validated.finding_id in seen_ids:
                raise EngineIntegrityError(
                    "BIM QA finding mapper produced duplicate finding identifiers.",
                    component=_COMPONENT,
                    operation="map_findings",
                    field="finding_id",
                    context={"finding_id": validated.finding_id},
                )
            seen_ids.add(validated.finding_id)
            findings.append(validated)

        return tuple(findings)

    def audit(
        self,
        context: AuditContext,
        requirements: Iterable[RequirementContract],
        *,
        rule_ids: Iterable[str] | None = None,
        versions: Mapping[str, RuleVersion | str] | None = None,
    ) -> BIMQAAuditResult:
        """Run one deterministic BIM QA audit over normalized project evidence.

        ``requirements`` are authoritative assessment rows rather than raw
        requirement prose.  The current generic RuleResult does not carry the
        impact/recommended-action/confidence semantics required to manufacture
        those rows safely, so this coordinator validates and emits them without
        inventing an implicit rule-to-requirement mapping.
        """
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running BIM QA audit",
            event="bim_qa_auditor_audit_start",
        )
        audit_context = _require_bim_qa_context(context, operation="audit")

        # Validate the analytical matrix before doing potentially expensive rule
        # execution. This also fails closed on unresolved evidence references.
        matrix_result = self._requirement_matrix.evaluate(
            requirements,
            context=audit_context,
        )
        rule_results = self._execute(
            audit_context,
            rule_ids=rule_ids,
            versions=versions,
        )
        findings = self._map_findings(rule_results, audit_context)
        status_counts = MappingProxyType(self._executor.status_counts(rule_results))
        executed_versions = MappingProxyType(
            {
                result.rule_id: str(result.rule_version)
                for result in rule_results
            }
        )
        # The context contract allows a string product code, but the BIM QA
        # boundary above guarantees the canonical ProductCode value.
        product_code = cast(ProductCode, audit_context.product_code)

        result = BIMQAAuditResult(
            product_code=product_code,
            project_id=cast(str, audit_context.project_id),
            rule_results=rule_results,
            requirement_matrix=matrix_result,
            findings=findings,
            rule_status_counts=status_counts,
            executed_rule_versions=executed_versions,
        )
        logger.info(
            {
                "event": "bim_qa_audit_completed",
                "product_code": product_code.value,
                "rule_count": len(rule_results),
                "requirement_count": matrix_result.summary.requirement_count,
                "finding_count": len(findings),
                "status_counts": dict(status_counts),
            }
        )
        return result


# Backward compatibility with the original scaffold while making the canonical
# product-specific name explicit.
BIMAuditor = BIMQAAuditor


__all__ = [
    "FindingMapper",
    "BIMQAAuditResult",
    "BIMQAAuditor",
    "BIMAuditor",
]


if __name__ == "__main__":
    from ..rules.registry import RulesRegistry

    print("\n=== Running BIM QA Auditor Self-Test ===\n")
    printer.status("TEST", "BIM QA auditor module initialized", "info")

    registry = RulesRegistry(freeze=True)
    auditor = BIMQAAuditor(RulesExecutor(registry))
    context = AuditContext(product_code=ProductCode.BIM_QA, project_id="PROJECT-TEST")
    result = auditor.audit(context, ())
    assert result.project_id == "PROJECT-TEST"
    assert not result.rule_results
    assert not result.findings
    printer.status("PASS", "Empty BIM QA orchestration", "success")

    print("\n=== Test ran successfully ===\n")