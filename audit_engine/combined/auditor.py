"""
BIMAP Combined Audit deterministic cross-scope correlation coordinator.

The Combined Auditor consumes already-produced Family Audit and BIM QA findings.
It MUST NOT import or invoke ``rfa/auditor.py`` or ``bim_qa/auditor.py``. The
outer engine/application orchestration owns execution order:

    RFA Auditor -----\
                     +--> CombinedAuditor
    BIM QA Auditor --/

This prevents duplicate product-audit execution and preserves one authoritative
execution path.

The current repository does not yet define a canonical cross-scope correlation
policy that can safely manufacture title/category/severity/confidence/
remediation semantics. Consequently, this module does not invent those values.
An optional injected ``correlator`` owns product-specific cross-scope policy.
The coordinator validates every correlator-produced FindingContract against:
- CROSS_SCOPE finding scope;
- the accepted Combined Audit context;
- evidence already cited by completed source findings; and
- evidence coverage from both Family and Project finding scopes.

Without a correlator, the evidence graph and source findings are still returned
as a complete deterministic structural result, while ``cross_scope_findings``
remains empty.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from ...contracts.finding import *
from ...contracts.utils.contracts_errors import ContractError
from ...domain.products.models import ProductCode
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ..context import AuditContext
from .evidence_graph import *
from .versions import AuditVersion
from logs.logger import PrettyPrinter, get_logger  # type: ignore

logger = get_logger("BIMAP Combined Audit Auditor")
printer = PrettyPrinter()


_COMPONENT = "combined_auditor"
_EXPECTED_PRODUCT = ProductCode.COMBINED_AUDIT

CombinedCorrelator = Callable[
    [EvidenceGraphResult, AuditContext],
    Iterable[FindingContract],
]


def _require_combined_context(context: AuditContext, *, operation: str) -> AuditContext:
    """Require a normalized Combined Audit context with project identity."""
    if not isinstance(context, AuditContext):
        raise UnsupportedEngineInputError(
            "Combined auditing requires an AuditContext.",
            component=_COMPONENT,
            operation=operation,
            field="context",
            context={"received_type": type(context).__name__},
        )
    if context.product_code is not _EXPECTED_PRODUCT:
        raise EngineValidationError(
            "CombinedAuditor requires Combined Audit product scope.",
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
            "Combined Audit requires project identity because BIM QA is project-scoped.",
            component=_COMPONENT,
            operation=operation,
            field="project_id",
            context={"product_code": context.product_code.value},
        )
    return context


def _normalize_source_findings(
    findings: Iterable[FindingContract],
    *,
    expected_scope: FindingScope,
    field: str,
) -> tuple[FindingContract, ...]:
    """Validate one completed source-audit finding collection."""
    announce_engine_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating Combined Audit {field}",
        event="combined_auditor_validate_source_findings_start",
        context={"expected_scope": expected_scope.value},
    )
    if isinstance(findings, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            f"{field} must be an iterable of FindingContract values.",
            component=_COMPONENT,
            operation="normalize_source_findings",
            field=field,
            context={"received_type": type(findings).__name__},
        )
    try:
        normalized = tuple(findings)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            f"{field} must be iterable.",
            component=_COMPONENT,
            operation="normalize_source_findings",
            field=field,
            context={"received_type": type(findings).__name__},
            cause=exc,
        ) from exc

    seen_ids: set[str] = set()
    for index, finding in enumerate(normalized):
        if not isinstance(finding, FindingContract):
            raise UnsupportedEngineInputError(
                "Combined Audit accepts completed FindingContract values only.",
                component=_COMPONENT,
                operation="normalize_source_findings",
                field=f"{field}[{index}]",
                context={"received_type": type(finding).__name__},
            )
        if finding.scope is not expected_scope:
            raise EngineValidationError(
                "Completed source finding has the wrong scope for its Combined Audit input.",
                component=_COMPONENT,
                operation="normalize_source_findings",
                field=f"{field}[{index}].scope",
                context={
                    "finding_id": finding.finding_id,
                    "expected_scope": expected_scope.value,
                    "received_scope": finding.scope.value,
                },
            )
        if finding.finding_id in seen_ids:
            raise EngineIntegrityError(
                "Completed source audit contains duplicate finding identifiers.",
                component=_COMPONENT,
                operation="normalize_source_findings",
                field=field,
                context={"finding_id": finding.finding_id},
            )
        seen_ids.add(finding.finding_id)
    return normalized


@dataclass(frozen=True, slots=True)
class CombinedAuditResult:
    """Immutable traceable output of one Combined Audit correlation run."""

    product_code: ProductCode
    project_id: str
    audit_version: AuditVersion
    family_findings: tuple[FindingContract, ...]
    project_findings: tuple[FindingContract, ...]
    cross_scope_findings: tuple[FindingContract, ...]
    evidence_graph: EvidenceGraphResult
    source_scope_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating Combined Audit result",
            event="combined_audit_result_validate_start",
        )
        if self.product_code is not ProductCode.COMBINED_AUDIT:
            raise EngineIntegrityError(
                "CombinedAuditResult must use the Combined Audit product code.",
                component=_COMPONENT,
                operation="validate_result",
                field="product_code",
            )
        if not isinstance(self.audit_version, AuditVersion):
            raise EngineIntegrityError(
                "CombinedAuditResult requires an AuditVersion.",
                component=_COMPONENT,
                operation="validate_result",
                field="audit_version",
                context={"received_type": type(self.audit_version).__name__},
            )
        if not isinstance(self.evidence_graph, EvidenceGraphResult):
            raise EngineIntegrityError(
                "CombinedAuditResult requires an EvidenceGraphResult.",
                component=_COMPONENT,
                operation="validate_result",
                field="evidence_graph",
                context={"received_type": type(self.evidence_graph).__name__},
            )
        object.__setattr__(self, "source_scope_counts", MappingProxyType(dict(self.source_scope_counts)))

    @property
    def finding_count(self) -> int:
        """Return source plus cross-scope finding count."""
        return len(self.family_findings) + len(self.project_findings) + len(self.cross_scope_findings)

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe Combined Audit output."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing Combined Audit result",
            event="combined_audit_result_to_dict_start",
            context={
                "family_finding_count": len(self.family_findings),
                "project_finding_count": len(self.project_findings),
                "cross_scope_finding_count": len(self.cross_scope_findings),
            },
        )
        payload = {
            "product_code": self.product_code.value,
            "project_id": self.project_id,
            "audit_version": str(self.audit_version),
            "family_findings": [finding.to_dict() for finding in self.family_findings],
            "project_findings": [finding.to_dict() for finding in self.project_findings],
            "cross_scope_findings": [finding.to_dict() for finding in self.cross_scope_findings],
            "source_scope_counts": dict(self.source_scope_counts),
            "evidence_graph": self.evidence_graph.to_dict(),
        }
        primitive = to_engine_primitive(payload, field="combined_audit_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "Combined Audit result did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="combined_audit_result",
            )
        return primitive


class CombinedAuditor:
    """Correlate completed family/project findings without re-running auditors."""

    def __init__(
        self,
        version: AuditVersion | str,
        *,
        evidence_graph: EvidenceGraph | None = None,
        correlator: CombinedCorrelator | None = None,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing Combined Audit auditor",
            event="combined_auditor_init_start",
        )
        try:
            normalized_version = AuditVersion.parse(version)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineConfigurationError(
                "CombinedAuditor received an invalid audit version.",
                component=_COMPONENT,
                operation="initialize",
                field="version",
                context={"received_type": type(version).__name__},
                cause=exc,
            ) from exc

        if evidence_graph is not None and not isinstance(evidence_graph, EvidenceGraph):
            raise EngineConfigurationError(
                "evidence_graph must be an EvidenceGraph or None.",
                component=_COMPONENT,
                operation="initialize",
                field="evidence_graph",
                context={"received_type": type(evidence_graph).__name__},
            )
        if correlator is not None and not callable(correlator):
            raise EngineConfigurationError(
                "correlator must be callable or None.",
                component=_COMPONENT,
                operation="initialize",
                field="correlator",
                context={"received_type": type(correlator).__name__},
            )

        self._version = normalized_version
        self._evidence_graph = evidence_graph or EvidenceGraph()
        self._correlator = correlator
        logger.info(
            {
                "event": "combined_auditor_initialized",
                "audit_version": str(self._version),
                "has_correlator": correlator is not None,
            }
        )

    @property
    def version(self) -> AuditVersion:
        """Return the exact Combined Audit algorithm version for this auditor."""
        return self._version

    @property
    def evidence_graph(self) -> EvidenceGraph:
        """Return the structural evidence-graph service used by this auditor."""
        return self._evidence_graph

    @property
    def has_correlator(self) -> bool:
        """Return whether explicit cross-scope finding policy is configured."""
        return self._correlator is not None

    def _validate_cross_scope_finding(
        self,
        finding: FindingContract,
        *,
        graph: EvidenceGraphResult,
        source_finding_ids: set[str],
    ) -> FindingContract:
        """Require one correlation finding to remain genuinely cross-scope grounded."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating Combined Audit cross-scope finding",
            event="combined_auditor_validate_cross_scope_finding_start",
            context={"finding_id": getattr(finding, "finding_id", None)},
        )
        if not isinstance(finding, FindingContract):
            raise EngineIntegrityError(
                "Combined Audit correlator returned an unsupported object type.",
                component=_COMPONENT,
                operation="validate_cross_scope_finding",
                field="finding",
                context={"received_type": type(finding).__name__},
            )
        if finding.scope is not FindingScope.CROSS_SCOPE:
            raise EngineIntegrityError(
                "Combined Audit correlation findings must use cross-scope scope.",
                component=_COMPONENT,
                operation="validate_cross_scope_finding",
                field="scope",
                context={"finding_id": finding.finding_id, "received_scope": finding.scope.value},
            )
        if finding.finding_id in source_finding_ids:
            raise EngineIntegrityError(
                "Cross-scope finding identity collides with a completed source finding.",
                component=_COMPONENT,
                operation="validate_cross_scope_finding",
                field="finding_id",
                context={"finding_id": finding.finding_id},
            )

        graph_evidence_ids = set(graph.evidence_nodes)
        unresolved = tuple(
            evidence_id for evidence_id in finding.evidence_refs
            if evidence_id not in graph_evidence_ids
        )
        if unresolved:
            raise EngineIntegrityError(
                "Cross-scope finding references evidence absent from Combined Audit context.",
                component=_COMPONENT,
                operation="validate_cross_scope_finding",
                field="evidence_refs",
                context={"finding_id": finding.finding_id, "unresolved_evidence_refs": unresolved},
            )

        source_scopes = graph.source_scopes_for_evidence_refs(finding.evidence_refs)
        required_scopes = {FindingScope.FAMILY, FindingScope.PROJECT}
        if not required_scopes.issubset(source_scopes):
            raise EngineIntegrityError(
                "Cross-scope finding must be grounded in evidence cited by both family and project findings.",
                component=_COMPONENT,
                operation="validate_cross_scope_finding",
                field="evidence_refs",
                context={
                    "finding_id": finding.finding_id,
                    "source_scopes": tuple(sorted(scope.value for scope in source_scopes)),
                },
            )
        return finding

    def _correlate(
        self,
        graph: EvidenceGraphResult,
        context: AuditContext,
        *,
        source_finding_ids: set[str],
    ) -> tuple[FindingContract, ...]:
        """Invoke explicit cross-scope policy and validate every produced finding."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running Combined Audit cross-scope correlation",
            event="combined_auditor_correlate_start",
            context={"source_finding_count": graph.finding_count, "graph_edge_count": graph.edge_count},
        )
        correlator = self._correlator
        if correlator is None:
            logger.debug(
                {
                    "event": "combined_correlation_skipped",
                    "reason": "correlator_not_configured",
                    "source_finding_count": graph.finding_count,
                }
            )
            return ()

        try:
            produced = correlator(graph, context)
        except EngineError:
            raise
        except ContractError as exc:
            raise EngineValidationError(
                "Combined Audit correlator produced an invalid finding contract.",
                component=_COMPONENT,
                operation="correlate",
                field="correlator",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        except Exception as exc:
            raise EngineError(
                "Combined Audit correlator failed with an unhandled implementation error.",
                component=_COMPONENT,
                operation="correlate",
                field="correlator",
                context={"error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        if isinstance(produced, (str, bytes, bytearray, Mapping)):
            raise EngineIntegrityError(
                "Combined Audit correlator must return an iterable of FindingContract values.",
                component=_COMPONENT,
                operation="correlate",
                field="correlator",
                context={"received_type": type(produced).__name__},
            )
        try:
            candidates = tuple(produced)
        except TypeError as exc:
            raise EngineIntegrityError(
                "Combined Audit correlator returned a non-iterable value.",
                component=_COMPONENT,
                operation="correlate",
                field="correlator",
                context={"received_type": type(produced).__name__},
                cause=exc,
            ) from exc

        findings: list[FindingContract] = []
        seen_ids: set[str] = set()
        for candidate in candidates:
            validated = self._validate_cross_scope_finding(
                candidate,
                graph=graph,
                source_finding_ids=source_finding_ids,
            )
            if validated.finding_id in seen_ids:
                raise EngineIntegrityError(
                    "Combined Audit correlator produced duplicate finding identifiers.",
                    component=_COMPONENT,
                    operation="correlate",
                    field="finding_id",
                    context={"finding_id": validated.finding_id},
                )
            seen_ids.add(validated.finding_id)
            findings.append(validated)
        return tuple(findings)

    def audit(
        self,
        context: AuditContext,
        family_findings: Iterable[FindingContract],
        project_findings: Iterable[FindingContract],
    ) -> CombinedAuditResult:
        """Correlate completed Family Audit and BIM QA findings."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running Combined Audit",
            event="combined_auditor_audit_start",
        )
        audit_context = _require_combined_context(context, operation="audit")
        family = _normalize_source_findings(
            family_findings,
            expected_scope=FindingScope.FAMILY,
            field="family_findings",
        )
        project = _normalize_source_findings(
            project_findings,
            expected_scope=FindingScope.PROJECT,
            field="project_findings",
        )

        family_ids = {finding.finding_id for finding in family}
        project_ids = {finding.finding_id for finding in project}
        collisions = tuple(sorted(family_ids.intersection(project_ids)))
        if collisions:
            raise EngineIntegrityError(
                "Family and project audit inputs contain colliding finding identifiers.",
                component=_COMPONENT,
                operation="audit",
                field="finding_id",
                context={"colliding_finding_ids": collisions},
            )

        graph = self._evidence_graph.build(audit_context, (*family, *project))
        cross_scope = self._correlate(
            graph,
            audit_context,
            source_finding_ids=family_ids | project_ids,
        )

        result = CombinedAuditResult(
            product_code=cast(ProductCode, audit_context.product_code),
            project_id=cast(str, audit_context.project_id),
            audit_version=self._version,
            family_findings=family,
            project_findings=project,
            cross_scope_findings=cross_scope,
            evidence_graph=graph,
            source_scope_counts=MappingProxyType(
                {
                    FindingScope.FAMILY.value: len(family),
                    FindingScope.PROJECT.value: len(project),
                    FindingScope.CROSS_SCOPE.value: len(cross_scope),
                }
            ),
        )
        logger.info(
            {
                "event": "combined_audit_completed",
                "product_code": result.product_code.value,
                "audit_version": str(result.audit_version),
                "family_finding_count": len(family),
                "project_finding_count": len(project),
                "cross_scope_finding_count": len(cross_scope),
                "graph_evidence_count": graph.evidence_count,
                "graph_edge_count": graph.edge_count,
            }
        )
        return result


__all__ = [
    "CombinedCorrelator",
    "CombinedAuditResult",
    "CombinedAuditor",
]


if __name__ == "__main__":
    print("\n=== Running Combined Audit Auditor Self-Test ===\n")
    printer.status("TEST", "Combined Audit auditor module initialized", "info")
    auditor = CombinedAuditor("1.0.0")
    context = AuditContext(product_code=ProductCode.COMBINED_AUDIT, project_id="PROJECT-TEST")
    result = auditor.audit(context, (), ())
    assert result.audit_version == AuditVersion(1, 0, 0)
    assert not result.family_findings
    assert not result.project_findings
    assert not result.cross_scope_findings
    assert result.evidence_graph.edge_count == 0
    printer.status("PASS", "Empty Combined Audit orchestration", "success")
    print("\n=== Test ran successfully ===\n")