"""
Canonical aggregate result returned by the top-level BIMAP AuditEngine.

``AuditResult`` does not replace the product-specific results owned by the RFA,
BIM QA, and Combined Audit coordinators.  Instead, it binds those already-
validated stage outputs to the exact normalized ``AuditContext``, analytical
ingestion manifest(s), and cross-cutting ``CoverageResult`` produced during one
complete engine run.

This distinction prevents a second finding, requirement, evidence, or coverage
model from emerging at the orchestration boundary.  Product-specific semantics
remain in their authoritative modules; ``AuditResult`` adds only cross-stage
integrity and a stable composite view for application-layer consumers.

Governance, SLAI, reporting, persistence, and delivery metadata are intentionally
absent.  Those concerns occur outside deterministic Level-4 execution and must
not be fabricated into an audit result merely because later layers may consume
it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..contracts.finding import FindingContract
from ..contracts.requirement import RequirementContract
from ..domain.products.models import ProductCode
from ..domain.utils.domain_errors import DomainError
from .bim_qa.auditor import BIMQAAuditResult
from .combined.auditor import CombinedAuditResult
from .context import AuditContext
from .ingestion.manifest import EvidenceManifest
from .rfa.auditor import RFAAuditResult
from .rules.base import RuleResult
from .validation.coverage import CoverageResult
from .utils.engine_errors import *
from .utils.engine_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Result")
printer = PrettyPrinter()

_COMPONENT = "audit_result"


def _expected_manifest_kinds(product_code: ProductCode) -> tuple[IngestionKind, ...]:
    """Return the exact ingestion package sequence for one product result."""
    if product_code is ProductCode.FAMILY_AUDIT:
        return (IngestionKind.FAMILY_EVIDENCE,)
    if product_code is ProductCode.BIM_QA:
        return (IngestionKind.PROJECT_EVIDENCE,)
    if product_code is ProductCode.COMBINED_AUDIT:
        return (
            IngestionKind.FAMILY_EVIDENCE,
            IngestionKind.PROJECT_EVIDENCE,
        )
    raise EngineIntegrityError(
        "AuditResult received a ProductCode with no engine result policy.",
        component=_COMPONENT,
        operation="expected_manifest_kinds",
        field="product_code",
        context={"product_code": str(product_code)},
    )


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Immutable composite output of one complete deterministic BIMAP audit.

    Stage presence is product-specific:

    - Family Audit: ``family_audit`` only;
    - BIM QA: ``bim_qa`` only;
    - Combined Audit: ``family_audit``, ``bim_qa``, and ``combined_audit``.

    Combined Audit retains the two source-audit stage results because the current
    Combined Auditor intentionally consumes completed source findings rather than
    rerunning or hiding those source audits.
    """

    product_code: ProductCode | str
    context: AuditContext
    ingestion_manifests: tuple[EvidenceManifest, ...]
    coverage: CoverageResult

    family_audit: RFAAuditResult | None = None
    bim_qa: BIMQAAuditResult | None = None
    combined_audit: CombinedAuditResult | None = None

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit result",
            event="audit_result_validate_start",
            context={"product_code": str(self.product_code)},
        )

        try:
            product_code = ProductCode.parse(self.product_code)
        except DomainError as exc:
            raise EngineValidationError(
                "AuditResult contains an invalid BIMAP product code.",
                component=_COMPONENT,
                operation="validate",
                field="product_code",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if not isinstance(self.context, AuditContext):
            raise UnsupportedEngineInputError(
                "AuditResult requires an AuditContext.",
                component=_COMPONENT,
                operation="validate",
                field="context",
                context={"received_type": type(self.context).__name__},
            )
        if self.context.product_code is not product_code:
            raise EngineIntegrityError(
                "AuditResult product code does not match its AuditContext.",
                component=_COMPONENT,
                operation="validate",
                field="product_code",
                context={
                    "result_product_code": product_code.value,
                    "context_product_code": (
                        self.context.product_code.value
                        if isinstance(self.context.product_code, ProductCode)
                        else self.context.product_code
                    ),
                },
            )
        if not isinstance(self.coverage, CoverageResult):
            raise UnsupportedEngineInputError(
                "AuditResult requires a CoverageResult.",
                component=_COMPONENT,
                operation="validate",
                field="coverage",
                context={"received_type": type(self.coverage).__name__},
            )

        if isinstance(
            self.ingestion_manifests,
            (str, bytes, bytearray, Mapping),
        ):
            raise UnsupportedEngineInputError(
                "ingestion_manifests must be a sequence of EvidenceManifest values.",
                component=_COMPONENT,
                operation="validate",
                field="ingestion_manifests",
                context={
                    "received_type": type(self.ingestion_manifests).__name__,
                },
            )
        try:
            manifests = tuple(self.ingestion_manifests)
        except TypeError as exc:
            raise UnsupportedEngineInputError(
                "ingestion_manifests must be iterable.",
                component=_COMPONENT,
                operation="validate",
                field="ingestion_manifests",
                context={
                    "received_type": type(self.ingestion_manifests).__name__,
                },
                cause=exc,
            ) from exc

        for index, manifest in enumerate(manifests):
            if not isinstance(manifest, EvidenceManifest):
                raise UnsupportedEngineInputError(
                    "AuditResult accepts analytical EvidenceManifest values only.",
                    component=_COMPONENT,
                    operation="validate",
                    field=f"ingestion_manifests[{index}]",
                    context={"received_type": type(manifest).__name__},
                )

        expected_kinds = _expected_manifest_kinds(product_code)
        actual_kinds = tuple(manifest.ingestion_type for manifest in manifests)
        if actual_kinds != expected_kinds:
            raise EngineIntegrityError(
                "AuditResult ingestion manifests do not match product scope.",
                component=_COMPONENT,
                operation="validate",
                field="ingestion_manifests",
                context={
                    "expected": tuple(kind.value for kind in expected_kinds),
                    "received": tuple(kind.value for kind in actual_kinds),
                },
            )

        manifest_evidence_ids = tuple(
            evidence_id
            for manifest in manifests
            for evidence_id in manifest.evidence_ids
        )
        if len(set(manifest_evidence_ids)) != len(manifest_evidence_ids):
            raise EngineIntegrityError(
                "AuditResult ingestion manifests contain colliding evidence identifiers.",
                component=_COMPONENT,
                operation="validate",
                field="ingestion_manifests",
            )
        if manifest_evidence_ids != self.context.evidence_ids:
            raise EngineIntegrityError(
                "AuditResult context is not aligned with its ingestion manifests.",
                component=_COMPONENT,
                operation="validate",
                field="ingestion_manifests",
                context={
                    "manifest_evidence_count": len(manifest_evidence_ids),
                    "context_evidence_count": self.context.evidence_count,
                },
            )

        evidence_summary = self.coverage.evidence_validation.summary
        if evidence_summary.evidence_count != self.context.evidence_count:
            raise EngineIntegrityError(
                "Coverage evidence count does not match AuditContext.",
                component=_COMPONENT,
                operation="validate",
                field="coverage.evidence_count",
                context={
                    "coverage_evidence_count": evidence_summary.evidence_count,
                    "context_evidence_count": self.context.evidence_count,
                },
            )
        if evidence_summary.source_count != self.context.source_count:
            raise EngineIntegrityError(
                "Coverage source count does not match AuditContext.",
                component=_COMPONENT,
                operation="validate",
                field="coverage.source_count",
                context={
                    "coverage_source_count": evidence_summary.source_count,
                    "context_source_count": self.context.source_count,
                },
            )

        expected_findings = self._validate_stage_presence(product_code)
        if self.coverage.findings_validation.findings != expected_findings:
            raise EngineIntegrityError(
                "Coverage findings do not match the product audit output.",
                component=_COMPONENT,
                operation="validate",
                field="coverage.findings",
                context={
                    "coverage_finding_count": (
                        self.coverage.findings_validation.summary.finding_count
                    ),
                    "audit_finding_count": len(expected_findings),
                },
            )

        if product_code is ProductCode.FAMILY_AUDIT:
            if self.coverage.requirements:
                raise EngineIntegrityError(
                    "Family Audit result unexpectedly contains BIM QA requirements.",
                    component=_COMPONENT,
                    operation="validate",
                    field="coverage.requirements",
                    context={
                        "requirement_count": len(self.coverage.requirements),
                    },
                )
        else:
            project_stage = self.bim_qa
            if project_stage is None:
                raise EngineIntegrityError(
                    "Project-scoped AuditResult is missing its BIM QA stage.",
                    component=_COMPONENT,
                    operation="validate",
                    field="bim_qa",
                )
            if self.coverage.requirements != project_stage.requirement_matrix.requirements:
                raise EngineIntegrityError(
                    "Coverage requirements do not match the evaluated BIM QA matrix.",
                    component=_COMPONENT,
                    operation="validate",
                    field="coverage.requirements",
                    context={
                        "coverage_requirement_count": len(self.coverage.requirements),
                        "matrix_requirement_count": (
                            project_stage.requirement_matrix.summary.requirement_count
                        ),
                    },
                )

        object.__setattr__(self, "product_code", product_code)
        object.__setattr__(self, "ingestion_manifests", manifests)

        logger.debug(
            {
                "event": "audit_result_validated",
                "product_code": product_code.value,
                "evidence_count": self.context.evidence_count,
                "source_count": self.context.source_count,
                "requirement_count": len(self.coverage.requirements),
                "finding_count": len(expected_findings),
                "rule_result_count": len(self.rule_results),
            }
        )

    def _validate_stage_presence(
        self,
        product_code: ProductCode,
    ) -> tuple[FindingContract, ...]:
        """Validate product-specific stage composition and cross-stage identity."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit result stages",
            event="audit_result_validate_stages_start",
            context={"product_code": product_code.value},
        )

        if product_code is ProductCode.FAMILY_AUDIT:
            if not isinstance(self.family_audit, RFAAuditResult):
                raise EngineIntegrityError(
                    "Family Audit result requires an RFAAuditResult stage.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="family_audit",
                    context={
                        "received_type": type(self.family_audit).__name__,
                    },
                )
            if self.bim_qa is not None or self.combined_audit is not None:
                raise EngineIntegrityError(
                    "Family Audit result contains unrelated product stages.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="product_stages",
                )
            if self.family_audit.product_code is not ProductCode.FAMILY_AUDIT:
                raise EngineIntegrityError(
                    "RFA stage has an inconsistent product code.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="family_audit.product_code",
                )
            return self.family_audit.findings

        if product_code is ProductCode.BIM_QA:
            if not isinstance(self.bim_qa, BIMQAAuditResult):
                raise EngineIntegrityError(
                    "BIM QA result requires a BIMQAAuditResult stage.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="bim_qa",
                    context={"received_type": type(self.bim_qa).__name__},
                )
            if self.family_audit is not None or self.combined_audit is not None:
                raise EngineIntegrityError(
                    "BIM QA result contains unrelated product stages.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="product_stages",
                )
            if self.bim_qa.product_code is not ProductCode.BIM_QA:
                raise EngineIntegrityError(
                    "BIM QA stage has an inconsistent product code.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="bim_qa.product_code",
                )
            if self.context.project_id != self.bim_qa.project_id:
                raise EngineIntegrityError(
                    "BIM QA stage project identity does not match AuditContext.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="project_id",
                    context={
                        "context_project_id": self.context.project_id,
                        "stage_project_id": self.bim_qa.project_id,
                    },
                )
            return self.bim_qa.findings

        if product_code is ProductCode.COMBINED_AUDIT:
            if not isinstance(self.family_audit, RFAAuditResult):
                raise EngineIntegrityError(
                    "Combined Audit requires the completed Family Audit stage.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="family_audit",
                    context={
                        "received_type": type(self.family_audit).__name__,
                    },
                )
            if not isinstance(self.bim_qa, BIMQAAuditResult):
                raise EngineIntegrityError(
                    "Combined Audit requires the completed BIM QA stage.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="bim_qa",
                    context={"received_type": type(self.bim_qa).__name__},
                )
            if not isinstance(self.combined_audit, CombinedAuditResult):
                raise EngineIntegrityError(
                    "Combined Audit requires a CombinedAuditResult stage.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="combined_audit",
                    context={
                        "received_type": type(self.combined_audit).__name__,
                    },
                )

            if self.family_audit.product_code is not ProductCode.FAMILY_AUDIT:
                raise EngineIntegrityError(
                    "Combined Audit Family source stage has an invalid product code.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="family_audit.product_code",
                )
            if self.bim_qa.product_code is not ProductCode.BIM_QA:
                raise EngineIntegrityError(
                    "Combined Audit BIM QA source stage has an invalid product code.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="bim_qa.product_code",
                )
            if self.combined_audit.product_code is not ProductCode.COMBINED_AUDIT:
                raise EngineIntegrityError(
                    "Combined Audit stage has an invalid product code.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="combined_audit.product_code",
                )
            if self.context.project_id != self.bim_qa.project_id:
                raise EngineIntegrityError(
                    "Combined Audit BIM QA source project identity is inconsistent.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="project_id",
                    context={
                        "context_project_id": self.context.project_id,
                        "bim_qa_project_id": self.bim_qa.project_id,
                    },
                )
            if self.context.project_id != self.combined_audit.project_id:
                raise EngineIntegrityError(
                    "Combined Audit project identity does not match AuditContext.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="project_id",
                    context={
                        "context_project_id": self.context.project_id,
                        "combined_project_id": self.combined_audit.project_id,
                    },
                )
            if self.combined_audit.family_findings != self.family_audit.findings:
                raise EngineIntegrityError(
                    "Combined Audit family findings differ from the completed source audit.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="combined_audit.family_findings",
                )
            if self.combined_audit.project_findings != self.bim_qa.findings:
                raise EngineIntegrityError(
                    "Combined Audit project findings differ from the completed source audit.",
                    component=_COMPONENT,
                    operation="validate_stages",
                    field="combined_audit.project_findings",
                )
            return (
                *self.combined_audit.family_findings,
                *self.combined_audit.project_findings,
                *self.combined_audit.cross_scope_findings,
            )

        raise EngineIntegrityError(
            "AuditResult has no stage policy for its product code.",
            component=_COMPONENT,
            operation="validate_stages",
            field="product_code",
            context={"product_code": product_code.value},
        )

    @property
    def findings(self) -> tuple[FindingContract, ...]:
        """Return all final findings represented by this product result."""
        if self.product_code is ProductCode.FAMILY_AUDIT and self.family_audit is not None:
            return self.family_audit.findings
        if self.product_code is ProductCode.BIM_QA and self.bim_qa is not None:
            return self.bim_qa.findings
        if (
            self.product_code is ProductCode.COMBINED_AUDIT
            and self.combined_audit is not None
        ):
            return (
                *self.combined_audit.family_findings,
                *self.combined_audit.project_findings,
                *self.combined_audit.cross_scope_findings,
            )
        return ()

    @property
    def rule_results(self) -> tuple[RuleResult, ...]:
        """Return deterministic source-rule results represented by this audit.

        Combined Audit returns the concatenated Family and BIM QA source-rule
        results.  Cross-scope correlation is not represented as ``RuleResult``
        because the current Combined Auditor uses its separate correlator model.
        """
        if self.product_code is ProductCode.FAMILY_AUDIT and self.family_audit is not None:
            return self.family_audit.rule_results
        if self.product_code is ProductCode.BIM_QA and self.bim_qa is not None:
            return self.bim_qa.rule_results
        if self.product_code is ProductCode.COMBINED_AUDIT:
            family_results = (
                () if self.family_audit is None else self.family_audit.rule_results
            )
            project_results = () if self.bim_qa is None else self.bim_qa.rule_results
            return (*family_results, *project_results)
        return ()

    @property
    def requirements(self) -> tuple[RequirementContract, ...]:
        """Return validated BIM QA requirement rows represented by the result."""
        return self.coverage.requirements

    @property
    def evidence_count(self) -> int:
        """Return normalized evidence count."""
        return self.context.evidence_count

    @property
    def source_count(self) -> int:
        """Return normalized source count."""
        return self.context.source_count

    @property
    def requirement_count(self) -> int:
        """Return validated requirement count."""
        return len(self.coverage.requirements)

    @property
    def finding_count(self) -> int:
        """Return final finding count."""
        return len(self.findings)

    @property
    def rule_result_count(self) -> int:
        """Return deterministic source-rule result count."""
        return len(self.rule_results)

    def to_dict(self) -> dict[str, Any]:
        """Return the complete deterministic engine result as JSON-safe data.

        This is an internal analytical representation rather than a customer
        report contract.  Reporting remains responsible for customer-facing
        artifact selection, serialization, manifests, and layout.
        """
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing audit result",
            event="audit_result_to_dict_start",
            context={
                "product_code": self.product_code,
                "evidence_count": self.evidence_count,
                "requirement_count": self.requirement_count,
                "finding_count": self.finding_count,
            },
        )
        payload = {
            "product_code": self.product_code,
            "context": self.context.to_grounded_dict(),
            "ingestion_manifests": [
                manifest.to_dict() for manifest in self.ingestion_manifests
            ],
            "coverage": self.coverage.to_dict(),
            "family_audit": (
                None if self.family_audit is None else self.family_audit.to_dict()
            ),
            "bim_qa": None if self.bim_qa is None else self.bim_qa.to_dict(),
            "combined_audit": (
                None
                if self.combined_audit is None
                else self.combined_audit.to_dict()
            ),
        }
        primitive = to_engine_primitive(payload, field="audit_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "AuditResult did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="audit_result",
            )
        return primitive


__all__ = ["AuditResult"]


if __name__ == "__main__":
    from .validation.coverage import ValidationCoverage

    print("\n=== Running Audit Result Self-Test ===\n")
    printer.status("TEST", "Audit result module initialized", "info")

    context = AuditContext(product_code=ProductCode.FAMILY_AUDIT)
    manifest = EvidenceManifest(
        ingestion_type=IngestionKind.FAMILY_EVIDENCE,
        schema_version="1.0.0",
        evidence_ids=(),
        sources=(),
        source_manifest={},
    )
    coverage = ValidationCoverage().calculate(context)
    family_stage = RFAAuditResult(
        product_code=ProductCode.FAMILY_AUDIT,
        rule_results=(),
        findings=(),
        rule_status_counts={},
        executed_rule_versions={},
    )
    result = AuditResult(
        product_code=ProductCode.FAMILY_AUDIT,
        context=context,
        ingestion_manifests=(manifest,),
        coverage=coverage,
        family_audit=family_stage,
    )
    assert result.evidence_count == 0
    assert result.finding_count == 0
    assert result.to_dict()["product_code"] == ProductCode.FAMILY_AUDIT.value
    printer.status("PASS", "Family Audit result composition", "success")

    print("\n=== Test ran successfully ===\n")