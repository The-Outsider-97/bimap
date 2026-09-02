"""
Top-level deterministic audit orchestration for BIMAP Level 4.

``AuditEngine`` composes the already-separated audit-engine subsystems without
reimplementing their schemas or analytical policy:

    ingestion dispatcher
            ↓
    evidence / requirement normalization
            ↓
    AuditContext construction
            ↓
    RFA / BIM QA / Combined product coordinators
            ↓
    validation coverage
            ↓
    AuditResult

Architectural boundary
----------------------
The engine is an internal deterministic orchestration boundary.  It consumes the
versioned BIMAP contracts and canonical domain evidence through the existing
Level-4 ingestion/normalization services.  It does not import or invoke SLAI,
reporting, API routes, application commands, workers, persistence adapters, or
object-storage implementations.

Product policy remains explicit
-------------------------------
The current RFA and BIM QA auditors require configured ``RulesExecutor``
instances, while the Combined Auditor requires an explicit algorithm version and
optional correlation policy.  ``AuditEngine`` therefore accepts those auditors
through dependency injection instead of constructing empty rule registries,
inventing ruleset versions, or silently enabling unspecified finding/correlation
policy.

Combined Audit execution
------------------------
Combined Audit deliberately executes Family Audit and BIM QA once, then passes
their completed findings to ``CombinedAuditor``.  This preserves the existing
Combined Auditor invariant that it must not rerun source auditors internally.
The combined context contains family evidence first and project evidence second,
with the canonical evidence-group names preserved.  If future contract section
names collide, execution fails closed rather than silently renaming groups.

Package-level extractor manifests are not merged into a fabricated combined
manifest schema.  They remain preserved independently in the final
``AuditResult.ingestion_manifests``.  Canonical per-evidence provenance remains
present in the combined ``AuditContext``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields as dataclass_fields
from typing import Any, TypeVar

from ..contracts.evidence import EvidenceContract
from ..contracts.family_evidence import FamilyEvidence as FamilyEvidenceContract
from ..contracts.project_evidence import ProjectEvidence as ProjectEvidenceContract
from ..contracts.requirement import RequirementContract
from ..domain.products.models import ProductCode
from .bim_qa.auditor import BIMQAAuditor
from .combined.auditor import CombinedAuditor
from .context import AuditContext
from .ingestion.dispatcher import DispatchInput, Dispatcher
from .ingestion.manifest import EvidenceManifest
from .normalization.evidence_normalizer import EvidenceNormalizer
from .normalization.family_normalizer import FamilyNormalizer
from .normalization.schema_export import (
    RequirementInput,
    SchemaExporter as RequirementSchemaNormalizer,
)
from .result import AuditResult
from .rfa.auditor import RFAAuditor
from .rules.versions import RuleVersion
from .utils.engine_errors import (
    EngineConfigurationError,
    EngineIntegrityError,
)
from .utils.engine_helpers import IngestionKind, announce_engine_action
from .validation.coverage import ValidationCoverage
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Engine")
printer = PrettyPrinter()

_COMPONENT = "audit_engine"

EngineEvidenceInput = DispatchInput
RequirementPayload = RequirementInput | Iterable[RequirementInput]
RuleSelection = Iterable[str] | None
RuleVersionSelection = Mapping[str, RuleVersion | str] | None

_T = TypeVar("_T")


def _project_section_names(contract: ProjectEvidenceContract) -> tuple[str, ...]:
    """Derive canonical project evidence sections from the authoritative DTO.

    The Project Evidence contract intentionally keeps its section-name tuple
    private.  The engine therefore follows the same dataclass-reflection approach
    already used by ``FamilyNormalizer`` instead of copying that private schema
    into a second module.
    """
    announce_engine_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Resolving Project Evidence sections",
        event="audit_engine_project_sections_start",
    )

    excluded = {
        "project_id",
        "family_evidence_refs",
        "source_manifest",
        "schema_version",
    }
    section_names: list[str] = []
    for definition in dataclass_fields(contract):
        if definition.name in excluded:
            continue
        value = getattr(contract, definition.name)
        if not isinstance(value, tuple):
            continue
        if value and not all(isinstance(item, EvidenceContract) for item in value):
            continue
        section_names.append(definition.name)
    return tuple(section_names)


class AuditEngine:
    """Coordinate one complete deterministic BIMAP product audit.

    Lower-level parsing, provenance validation, normalization, rule execution,
    finding mapping, requirement evaluation, cross-scope correlation, and
    coverage each remain owned by their existing subsystem.  The engine's job is
    to connect those services in the correct order and enforce cross-stage
    integrity.
    """

    def __init__(
        self,
        *,
        dispatcher: Dispatcher | None = None,
        evidence_normalizer: EvidenceNormalizer | None = None,
        family_normalizer: FamilyNormalizer | None = None,
        requirement_normalizer: RequirementSchemaNormalizer | None = None,
        validation_coverage: ValidationCoverage | None = None,
        rfa_auditor: RFAAuditor | None = None,
        bim_qa_auditor: BIMQAAuditor | None = None,
        combined_auditor: CombinedAuditor | None = None,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing audit engine",
            event="audit_engine_init_start",
        )

        optional_dependencies: tuple[tuple[str, Any, type[Any]], ...] = (
            ("dispatcher", dispatcher, Dispatcher),
            ("evidence_normalizer", evidence_normalizer, EvidenceNormalizer),
            ("family_normalizer", family_normalizer, FamilyNormalizer),
            (
                "requirement_normalizer",
                requirement_normalizer,
                RequirementSchemaNormalizer,
            ),
            ("validation_coverage", validation_coverage, ValidationCoverage),
            ("rfa_auditor", rfa_auditor, RFAAuditor),
            ("bim_qa_auditor", bim_qa_auditor, BIMQAAuditor),
            ("combined_auditor", combined_auditor, CombinedAuditor),
        )
        for field, value, expected_type in optional_dependencies:
            if value is not None and not isinstance(value, expected_type):
                raise EngineConfigurationError(
                    f"{field} has an unsupported audit-engine dependency type.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=field,
                    context={
                        "expected_type": expected_type.__name__,
                        "received_type": type(value).__name__,
                    },
                )

        evidence_service = evidence_normalizer or EvidenceNormalizer()

        self._dispatcher = dispatcher or Dispatcher()
        self._evidence_normalizer = evidence_service
        self._family_normalizer = family_normalizer or FamilyNormalizer(
            evidence_normalizer=evidence_service
        )
        self._requirement_normalizer = (
            requirement_normalizer or RequirementSchemaNormalizer()
        )
        self._validation_coverage = validation_coverage or ValidationCoverage()
        self._rfa_auditor = rfa_auditor
        self._bim_qa_auditor = bim_qa_auditor
        self._combined_auditor = combined_auditor

        logger.info(
            {
                "event": "audit_engine_initialized",
                "rfa_configured": rfa_auditor is not None,
                "bim_qa_configured": bim_qa_auditor is not None,
                "combined_configured": combined_auditor is not None,
            }
        )

    @property
    def dispatcher(self) -> Dispatcher:
        """Return the ingestion dispatcher used by this engine."""
        return self._dispatcher

    @property
    def evidence_normalizer(self) -> EvidenceNormalizer:
        """Return the canonical single-evidence normalizer."""
        return self._evidence_normalizer

    @property
    def family_normalizer(self) -> FamilyNormalizer:
        """Return the Family Evidence normalizer."""
        return self._family_normalizer

    @property
    def requirement_normalizer(self) -> RequirementSchemaNormalizer:
        """Return the structured requirement-row normalizer."""
        return self._requirement_normalizer

    @property
    def validation_coverage(self) -> ValidationCoverage:
        """Return the cross-cutting validation/coverage service."""
        return self._validation_coverage

    @property
    def rfa_auditor(self) -> RFAAuditor | None:
        """Return the configured Family Audit coordinator, if available."""
        return self._rfa_auditor

    @property
    def bim_qa_auditor(self) -> BIMQAAuditor | None:
        """Return the configured BIM QA coordinator, if available."""
        return self._bim_qa_auditor

    @property
    def combined_auditor(self) -> CombinedAuditor | None:
        """Return the configured Combined Audit coordinator, if available."""
        return self._combined_auditor

    def _require_component(
        self,
        value: _T | None,
        expected_type: type[_T],
        *,
        field: str,
    ) -> _T:
        """Require an explicitly configured product coordinator."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action=f"Requiring configured {field}",
            event="audit_engine_require_component_start",
            context={"component_field": field},
        )
        if value is None:
            raise EngineConfigurationError(
                f"{field} must be configured before this audit can run.",
                component=_COMPONENT,
                operation="require_component",
                field=field,
                context={"expected_type": expected_type.__name__},
            )
        if not isinstance(value, expected_type):
            # Constructor validation should make this unreachable.  Retaining the
            # guard keeps the runtime boundary explicit if object state is ever
            # constructed through an alternate mechanism.
            raise EngineConfigurationError(
                f"{field} is not a supported product coordinator.",
                component=_COMPONENT,
                operation="require_component",
                field=field,
                context={
                    "expected_type": expected_type.__name__,
                    "received_type": type(value).__name__,
                },
            )
        return value

    def _require_manifest_alignment(
        self,
        manifest: EvidenceManifest,
        context: AuditContext,
    ) -> None:
        """Require ingestion and normalized context to contain the same evidence."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Checking ingestion-to-context alignment",
            event="audit_engine_manifest_alignment_start",
            context={
                "ingestion_type": manifest.ingestion_type.value,
                "manifest_evidence_count": manifest.evidence_count,
                "context_evidence_count": context.evidence_count,
            },
        )
        if manifest.evidence_ids != context.evidence_ids:
            raise EngineIntegrityError(
                "Normalization changed the accepted evidence identity/order.",
                component=_COMPONENT,
                operation="require_manifest_alignment",
                field="evidence_ids",
                context={
                    "manifest_evidence_count": manifest.evidence_count,
                    "context_evidence_count": context.evidence_count,
                },
            )
        if manifest.source_count != context.source_count:
            raise EngineIntegrityError(
                "Normalization changed the accepted evidence source count.",
                component=_COMPONENT,
                operation="require_manifest_alignment",
                field="source_count",
                context={
                    "manifest_source_count": manifest.source_count,
                    "context_source_count": context.source_count,
                },
            )

    def _prepare_family_context(
        self,
        payload: EngineEvidenceInput,
        *,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[AuditContext, EvidenceManifest]:
        """Ingest and normalize one Family Evidence package exactly once."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Preparing Family Audit context",
            event="audit_engine_prepare_family_start",
            context={"input_type": type(payload).__name__},
        )

        dispatched = self._dispatcher.dispatch(
            payload,
            declared_type=IngestionKind.FAMILY_EVIDENCE,
        )
        if not isinstance(dispatched.contract, FamilyEvidenceContract):
            raise EngineIntegrityError(
                "Family dispatch returned a non-family evidence contract.",
                component=_COMPONENT,
                operation="prepare_family_context",
                field="contract",
                context={"received_type": type(dispatched.contract).__name__},
            )

        normalized = self._family_normalizer.normalize(dispatched.contract)
        context = normalized.to_context(
            product_code=ProductCode.FAMILY_AUDIT,
            metadata=metadata if metadata is not None else {},
        )
        self._require_manifest_alignment(dispatched.manifest, context)
        return context, dispatched.manifest

    def _prepare_project_context(
        self,
        payload: EngineEvidenceInput,
        *,
        metadata: Mapping[str, Any] | None,
    ) -> tuple[AuditContext, EvidenceManifest]:
        """Ingest and normalize one Project Evidence package exactly once."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Preparing BIM QA context",
            event="audit_engine_prepare_project_start",
            context={"input_type": type(payload).__name__},
        )

        dispatched = self._dispatcher.dispatch(
            payload,
            declared_type=IngestionKind.PROJECT_EVIDENCE,
        )
        if not isinstance(dispatched.contract, ProjectEvidenceContract):
            raise EngineIntegrityError(
                "Project dispatch returned a non-project evidence contract.",
                component=_COMPONENT,
                operation="prepare_project_context",
                field="contract",
                context={"received_type": type(dispatched.contract).__name__},
            )

        contract = dispatched.contract
        evidence_items = self._evidence_normalizer.normalize_many(
            contract.all_evidence()
        )
        section_names = _project_section_names(contract)
        evidence_groups = {
            name: tuple(item.evidence_id for item in contract.section(name))
            for name in section_names
        }

        context = AuditContext(
            product_code=ProductCode.BIM_QA,
            project_id=contract.project_id,
            evidence_items=evidence_items,
            evidence_groups=evidence_groups,
            family_evidence_refs=contract.family_evidence_refs,
            source_manifest=contract.source_manifest,
            metadata=metadata if metadata is not None else {},
        )
        self._require_manifest_alignment(dispatched.manifest, context)
        return context, dispatched.manifest

    def _prepare_combined_context(
        self,
        family_context: AuditContext,
        project_context: AuditContext,
        *,
        metadata: Mapping[str, Any] | None,
    ) -> AuditContext:
        """Create one deterministic cross-scope context without merging schemas."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Preparing Combined Audit context",
            event="audit_engine_prepare_combined_start",
            context={
                "family_evidence_count": family_context.evidence_count,
                "project_evidence_count": project_context.evidence_count,
            },
        )

        if family_context.product_code is not ProductCode.FAMILY_AUDIT:
            raise EngineIntegrityError(
                "Combined Audit received an invalid family source context.",
                component=_COMPONENT,
                operation="prepare_combined_context",
                field="family_context.product_code",
            )
        if project_context.product_code is not ProductCode.BIM_QA:
            raise EngineIntegrityError(
                "Combined Audit received an invalid project source context.",
                component=_COMPONENT,
                operation="prepare_combined_context",
                field="project_context.product_code",
            )
        if project_context.project_id is None:
            raise EngineIntegrityError(
                "Combined Audit project source context has no project identity.",
                component=_COMPONENT,
                operation="prepare_combined_context",
                field="project_id",
            )

        family_groups = dict(family_context.evidence_groups)
        project_groups = dict(project_context.evidence_groups)
        collisions = tuple(
            sorted(set(family_groups).intersection(project_groups))
        )
        if collisions:
            raise EngineIntegrityError(
                "Family and Project evidence groups collide in Combined Audit.",
                component=_COMPONENT,
                operation="prepare_combined_context",
                field="evidence_groups",
                context={"colliding_groups": collisions},
            )

        # Extractor-specific package manifests are intentionally not flattened or
        # namespaced here because the contracts do not define a canonical
        # combined source-manifest schema.  AuditResult preserves both analytical
        # ingestion manifests independently, while EvidenceItem provenance remains
        # authoritative inside this cross-scope context.
        combined = AuditContext(
            product_code=ProductCode.COMBINED_AUDIT,
            project_id=project_context.project_id,
            evidence_items=(
                *family_context.evidence_items,
                *project_context.evidence_items,
            ),
            evidence_groups={**family_groups, **project_groups},
            family_evidence_refs=project_context.family_evidence_refs,
            source_manifest={},
            metadata=metadata if metadata is not None else {},
        )
        return combined

    def _normalize_requirements(
        self,
        payloads: RequirementPayload,
        *,
        context: AuditContext,
    ) -> tuple[RequirementContract, ...]:
        """Normalize structured requirement rows against project evidence IDs."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Normalizing audit requirements",
            event="audit_engine_normalize_requirements_start",
            context={"project_id": context.project_id},
        )
        normalized = self._requirement_normalizer.normalize_many(
            payloads,
            known_evidence_ids=context.evidence_ids,
            allow_empty=True,
        )
        return normalized.requirements

    def audit_family(
        self,
        payload: EngineEvidenceInput,
        *,
        rule_ids: RuleSelection = None,
        versions: RuleVersionSelection = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditResult:
        """Run one complete deterministic R3D Family Audit."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running complete Family Audit",
            event="audit_engine_family_start",
            context={"input_type": type(payload).__name__},
        )
        auditor = self._require_component(
            self._rfa_auditor,
            RFAAuditor,
            field="rfa_auditor",
        )
        context, manifest = self._prepare_family_context(
            payload,
            metadata=metadata,
        )
        family_result = auditor.audit(
            context,
            rule_ids=rule_ids,
            versions=versions,
        )
        coverage = self._validation_coverage.calculate(
            context,
            findings=family_result.findings,
            requirements=(),
            rule_results=family_result.rule_results,
        )
        result = AuditResult(
            product_code=ProductCode.FAMILY_AUDIT,
            context=context,
            ingestion_manifests=(manifest,),
            coverage=coverage,
            family_audit=family_result,
        )
        logger.info(
            {
                "event": "audit_engine_family_completed",
                "evidence_count": result.evidence_count,
                "rule_result_count": result.rule_result_count,
                "finding_count": result.finding_count,
            }
        )
        return result

    def audit_bim_qa(
        self,
        payload: EngineEvidenceInput,
        requirements: RequirementPayload = (),
        *,
        rule_ids: RuleSelection = None,
        versions: RuleVersionSelection = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditResult:
        """Run one complete deterministic R3D BIM QA audit."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running complete BIM QA audit",
            event="audit_engine_bim_qa_start",
            context={"input_type": type(payload).__name__},
        )
        auditor = self._require_component(
            self._bim_qa_auditor,
            BIMQAAuditor,
            field="bim_qa_auditor",
        )
        context, manifest = self._prepare_project_context(
            payload,
            metadata=metadata,
        )
        requirement_items = self._normalize_requirements(
            requirements,
            context=context,
        )
        project_result = auditor.audit(
            context,
            requirement_items,
            rule_ids=rule_ids,
            versions=versions,
        )
        coverage = self._validation_coverage.calculate(
            context,
            findings=project_result.findings,
            requirements=requirement_items,
            rule_results=project_result.rule_results,
        )
        result = AuditResult(
            product_code=ProductCode.BIM_QA,
            context=context,
            ingestion_manifests=(manifest,),
            coverage=coverage,
            bim_qa=project_result,
        )
        logger.info(
            {
                "event": "audit_engine_bim_qa_completed",
                "project_id": result.context.project_id,
                "evidence_count": result.evidence_count,
                "requirement_count": result.requirement_count,
                "rule_result_count": result.rule_result_count,
                "finding_count": result.finding_count,
            }
        )
        return result

    def audit_combined(
        self,
        family_payload: EngineEvidenceInput,
        project_payload: EngineEvidenceInput,
        requirements: RequirementPayload = (),
        *,
        family_rule_ids: RuleSelection = None,
        family_versions: RuleVersionSelection = None,
        project_rule_ids: RuleSelection = None,
        project_versions: RuleVersionSelection = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditResult:
        """Run Family Audit + BIM QA once, then correlate them as Combined Audit."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running complete Combined Audit",
            event="audit_engine_combined_start",
            context={
                "family_input_type": type(family_payload).__name__,
                "project_input_type": type(project_payload).__name__,
            },
        )

        family_auditor = self._require_component(
            self._rfa_auditor,
            RFAAuditor,
            field="rfa_auditor",
        )
        project_auditor = self._require_component(
            self._bim_qa_auditor,
            BIMQAAuditor,
            field="bim_qa_auditor",
        )
        combined_auditor = self._require_component(
            self._combined_auditor,
            CombinedAuditor,
            field="combined_auditor",
        )

        family_context, family_manifest = self._prepare_family_context(
            family_payload,
            metadata=metadata,
        )
        project_context, project_manifest = self._prepare_project_context(
            project_payload,
            metadata=metadata,
        )
        combined_context = self._prepare_combined_context(
            family_context,
            project_context,
            metadata=metadata,
        )

        requirement_items = self._normalize_requirements(
            requirements,
            context=project_context,
        )
        family_result = family_auditor.audit(
            family_context,
            rule_ids=family_rule_ids,
            versions=family_versions,
        )
        project_result = project_auditor.audit(
            project_context,
            requirement_items,
            rule_ids=project_rule_ids,
            versions=project_versions,
        )
        combined_result = combined_auditor.audit(
            combined_context,
            family_result.findings,
            project_result.findings,
        )

        combined_findings = (
            *combined_result.family_findings,
            *combined_result.project_findings,
            *combined_result.cross_scope_findings,
        )
        # Source auditors already validate deterministic source findings against
        # their own RuleResult sets.  Cross-scope findings are produced by the
        # Combined correlator rather than one generic RulesExecutor.  Passing a
        # merged RuleResult index here could be ambiguous if family/project rules
        # share an ID, so combined coverage intentionally performs evidence and
        # finding grounding without a fabricated cross-product rule index.
        coverage = self._validation_coverage.calculate(
            combined_context,
            findings=combined_findings,
            requirements=requirement_items,
            rule_results=None,
        )

        result = AuditResult(
            product_code=ProductCode.COMBINED_AUDIT,
            context=combined_context,
            ingestion_manifests=(family_manifest, project_manifest),
            coverage=coverage,
            family_audit=family_result,
            bim_qa=project_result,
            combined_audit=combined_result,
        )
        logger.info(
            {
                "event": "audit_engine_combined_completed",
                "project_id": result.context.project_id,
                "evidence_count": result.evidence_count,
                "requirement_count": result.requirement_count,
                "rule_result_count": result.rule_result_count,
                "finding_count": result.finding_count,
                "cross_scope_finding_count": len(
                    combined_result.cross_scope_findings
                ),
            }
        )
        return result


__all__ = [
    "EngineEvidenceInput",
    "RequirementPayload",
    "RuleSelection",
    "RuleVersionSelection",
    "AuditEngine",
]


if __name__ == "__main__":
    print("\n=== Running Audit Engine Self-Test ===\n")
    printer.status("TEST", "Audit engine module initialized", "info")

    engine = AuditEngine()
    assert engine.rfa_auditor is None
    assert engine.bim_qa_auditor is None
    assert engine.combined_auditor is None

    try:
        engine.audit_family({"schema_version": "1.0.0", "source_manifest": {}})
    except EngineConfigurationError:
        printer.status(
            "PASS",
            "Unconfigured product policy fails closed",
            "success",
        )
    else:
        raise AssertionError("AuditEngine accepted an unconfigured RFA auditor.")

    print("\n=== Test ran successfully ===\n")