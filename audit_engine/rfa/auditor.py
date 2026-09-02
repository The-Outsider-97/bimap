"""
Revit Family Audit (RFA) deterministic audit coordinator.

``RFAAuditor`` is the product-specific orchestration boundary for deterministic
Family Audit execution.  It consumes an already-normalized :class:`AuditContext`
and a frozen :class:`RulesExecutor`, then returns version-traceable rule results
and, when an explicit mapping policy is supplied, grounded external
:class:`FindingContract` values.

Architectural boundary
----------------------
normalization / AuditContext
            ↓
rules registry + executor
            ↓
audit_engine.rfa.auditor
            ↓
audit_engine.result / reporting / application service

The auditor does not parse uploads, normalize evidence, load organization rule
configuration, render reports, persist state, call SLAI, or infer missing
finding-policy fields.  Those responsibilities remain in their owning layers.

Grounded finding construction
-----------------------------
The generic ``RuleResult`` intentionally contains rule identity/version,
assessment status, observed/expected values, evidence references and metrics.
The public ``FindingContract`` additionally requires rule-specific policy such
as title, category, severity, confidence, explanation, remediation and
verification method.  Those values cannot be derived safely by this generic
coordinator without fabricating semantics.

An optional injected ``finding_mapper`` therefore owns that product/rule policy.
Every mapper-produced finding is checked here for scope, rule/status identity,
deterministic classification, observed/expected state and evidence grounding.
If no mapper is configured, rule results are still complete deterministic audit
outputs and ``findings`` remains empty until the repository's rule-to-finding
mapping policy is implemented explicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from ...contracts.finding import FindingContract, FindingScope
from ...contracts.requirement import AutomationType
from ...contracts.utils.contracts_errors import ContractError
from ...domain.products.models import ProductCode
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ..context import AuditContext
from ..rules.base import RuleResult
from ..rules.executor import RulesExecutor
from ..rules.versions import RuleVersion
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP RFA Auditor")
printer = PrettyPrinter()

_COMPONENT = "rfa_auditor"
_EXPECTED_PRODUCT = ProductCode.FAMILY_AUDIT

RFAFindingMapper = Callable[[RuleResult, AuditContext], FindingContract | None]


def _require_rfa_context(context: AuditContext, *, operation: str) -> AuditContext:
    """Require an AuditContext capable of carrying Family Audit scope."""
    if not isinstance(context, AuditContext):
        raise UnsupportedEngineInputError(
            "RFA auditing requires an AuditContext.",
            component=_COMPONENT,
            operation=operation,
            field="context",
            context={"received_type": type(context).__name__},
        )

    if context.product_code is not _EXPECTED_PRODUCT:
        raise EngineValidationError(
            "RFA auditor requires Family Audit product scope.",
            component=_COMPONENT,
            operation=operation,
            field="product_code",
            context={
                "received": str(getattr(context.product_code, "value", context.product_code)),
                "expected": _EXPECTED_PRODUCT.value,
            },
        )
    return context


def _result_status_value(result: RuleResult) -> str:
    """Return the canonical string value of a deterministic rule status."""
    return str(getattr(result.status, "value", result.status))


@dataclass(frozen=True, slots=True)
class RFAAuditResult:
    """Immutable grounded output of one deterministic Family Audit run."""

    product_code: ProductCode
    rule_results: tuple[RuleResult, ...]
    findings: tuple[FindingContract, ...]
    rule_status_counts: Mapping[str, int]
    executed_rule_versions: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe data for later result/report layers."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing RFA audit result",
            event="rfa_result_to_dict_start",
            context={
                "rule_count": len(self.rule_results),
                "finding_count": len(self.findings),
            },
        )
        payload = {
            "product_code": self.product_code.value,
            "rule_results": [result.to_dict() for result in self.rule_results],
            "findings": [finding.to_dict() for finding in self.findings],
            "rule_status_counts": dict(self.rule_status_counts),
            "executed_rule_versions": dict(self.executed_rule_versions),
        }
        primitive = to_engine_primitive(payload, field="rfa_audit_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "RFA audit result did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="rfa_audit_result",
            )
        return primitive


class RFAAuditor:
    """Coordinate deterministic Revit-family rule execution and grounded outputs."""

    def __init__(
        self,
        executor: RulesExecutor,
        *,
        finding_mapper: RFAFindingMapper | None = None,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing RFA auditor",
            event="rfa_auditor_init_start",
        )
        if not isinstance(executor, RulesExecutor):
            raise EngineConfigurationError(
                "RFAAuditor requires a RulesExecutor.",
                component=_COMPONENT,
                operation="initialize",
                field="executor",
                context={"received_type": type(executor).__name__},
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
        self._finding_mapper = finding_mapper

        logger.info(
            {
                "event": "rfa_auditor_initialized",
                "has_finding_mapper": finding_mapper is not None,
                "registered_rule_count": len(executor.registry),
            }
        )

    @property
    def executor(self) -> RulesExecutor:
        """Return the frozen deterministic rule executor used by this auditor."""
        return self._executor

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
        """Execute current rules or an exact version-pinned historical replay."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing RFA deterministic rules",
            event="rfa_auditor_execute_start",
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
        """Require a mapper-produced RFA finding to remain rule/evidence grounded."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating grounded RFA finding",
            event="rfa_auditor_validate_finding_start",
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
        if finding.scope is not FindingScope.FAMILY:
            raise EngineIntegrityError(
                "RFA deterministic findings must use family scope.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="scope",
                context={
                    "rule_id": result.rule_id,
                    "received_scope": str(getattr(finding.scope, "value", finding.scope)),
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
        if str(getattr(finding.status, "value", finding.status)) != _result_status_value(result):
            raise EngineIntegrityError(
                "Mapped finding status does not match the executed rule result.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="status",
                context={
                    "rule_id": result.rule_id,
                    "expected_status": _result_status_value(result),
                    "received_status": str(getattr(finding.status, "value", finding.status)),
                },
            )
        if finding.automation_type is not AutomationType.DETERMINISTIC:
            raise EngineIntegrityError(
                "Findings mapped directly from RulesExecutor results must remain deterministic.",
                component=_COMPONENT,
                operation="validate_mapped_finding",
                field="automation_type",
                context={
                    "rule_id": result.rule_id,
                    "received": str(
                        getattr(finding.automation_type, "value", finding.automation_type)
                    ),
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
                "Mapped RFA finding references evidence not cited by its RuleResult.",
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
                "Mapped RFA finding references evidence absent from AuditContext.",
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
        """Map rule results through the explicitly configured RFA finding policy."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Mapping RFA rule results to grounded findings",
            event="rfa_auditor_map_findings_start",
            context={"rule_result_count": len(results)},
        )
        mapper = self._finding_mapper
        if mapper is None:
            logger.debug(
                {
                    "event": "rfa_finding_mapping_skipped",
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
                    "RFA finding mapper produced an invalid FindingContract.",
                    component=_COMPONENT,
                    operation="map_findings",
                    field="finding_mapper",
                    context={"rule_id": result.rule_id, **lower_error_context(exc)},
                    cause=exc,
                ) from exc
            except Exception as exc:
                raise EngineError(
                    "RFA finding mapper failed with an unhandled implementation error.",
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
                    "RFA finding mapper produced duplicate finding identifiers.",
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
        *,
        rule_ids: Iterable[str] | None = None,
        versions: Mapping[str, RuleVersion | str] | None = None,
    ) -> RFAAuditResult:
        """Run one deterministic Family Audit over normalized family evidence."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running RFA audit",
            event="rfa_auditor_audit_start",
        )
        audit_context = _require_rfa_context(context, operation="audit")
        product_code = cast(ProductCode, audit_context.product_code)
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

        result = RFAAuditResult(
            product_code=product_code,
            rule_results=rule_results,
            findings=findings,
            rule_status_counts=status_counts,
            executed_rule_versions=executed_versions,
        )
        logger.info(
            {
                "event": "rfa_audit_completed",
                "product_code": product_code.value,
                "rule_count": len(rule_results),
                "finding_count": len(findings),
                "status_counts": dict(status_counts),
            }
        )
        return result


__all__ = [
    "RFAFindingMapper",
    "RFAAuditResult",
    "RFAAuditor",
]


if __name__ == "__main__":
    from ..rules.registry import RulesRegistry

    print("\n=== Running RFA Auditor Self-Test ===\n")
    printer.status("TEST", "RFA auditor module initialized", "info")

    registry = RulesRegistry(freeze=True)
    auditor = RFAAuditor(RulesExecutor(registry))
    context = AuditContext(product_code=ProductCode.FAMILY_AUDIT)
    result = auditor.audit(context)
    assert not result.rule_results
    assert not result.findings
    printer.status("PASS", "Empty RFA orchestration", "success")

    print("\n=== Test ran successfully ===\n")