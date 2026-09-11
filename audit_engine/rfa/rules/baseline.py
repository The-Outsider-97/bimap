"""
Canonical baseline deterministic rules for BIMAP Revit Family Audit.

Location
--------
applications/bimap/audit_engine/rfa/rules/baseline.py

Scope
-----
These rules validate only facts already supported by BIMAP's current contracts
and extraction pipeline:

- Family Audit is backed by one RFA source;
- family-identity evidence is internally consistent;
- populated canonical extraction sections contain structured evidence at the
  canonical logical paths produced by AuditInputService.

They intentionally do not invent organization-specific Revit standards such as
naming conventions, mandatory shared parameters, geometry thresholds,
classification systems, connector policy, or material standards.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from ...context import AuditContext
from ...rules.base import BaseRules, RuleDefinition, RuleResult, RuleStatus
from ...utils.engine_helpers import to_engine_primitive
from ....domain.evidence.models import EvidenceItem
from ....domain.products.models import ProductCode


_PRODUCT = ProductCode.FAMILY_AUDIT
_VERSION = "1.0.0"


def _refs(items: tuple[EvidenceItem, ...]) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in items)


def _primitive_mapping(item: EvidenceItem) -> dict[str, object] | None:
    primitive = to_engine_primitive(
        item.extracted_value,
        field=f"evidence.{item.evidence_id}.extracted_value",
    )
    return primitive if isinstance(primitive, dict) else None


class RFASourceIntegrityRule(BaseRules):
    """Require a Family Audit context to represent exactly one RFA source."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.SOURCE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_source_integrity",
        known_limitations=(
            "Validates normalized source identity only; "
            "it does not validate Revit authoring quality.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        identity = context.group("family_identity")

        source_ids = tuple(
            sorted({item.source_file_id for item in context.evidence_items})
        )
        source_types = tuple(
            sorted({item.source_type.casefold() for item in context.evidence_items})
        )

        valid = len(source_ids) == 1 and source_types == ("rfa",)

        return self.result(
            RuleStatus.PASS if valid else RuleStatus.FAIL,
            evidence_refs=_refs(identity),
            observed_value={
                "source_count": len(source_ids),
                "source_types": list(source_types),
            },
            expected_value={
                "source_count": 1,
                "source_types": ["rfa"],
            },
            metrics={
                "context_evidence_count": context.evidence_count,
            },
        )


class RFAFamilyIdentityIntegrityRule(BaseRules):
    """
    Validate the canonical identity payload emitted by AuditInputService.

    Only DataSourceInspection fields already owned by BIMAP are checked.
    """

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.IDENTITY.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_identity_integrity",
        known_limitations=(
            "Does not impose organization-specific family naming "
            "or classification policy.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        items = context.group("family_identity")

        invalid_ids: list[str] = []
        product_counts: list[int] = []

        for item in items:
            payload = _primitive_mapping(item)

            if payload is None:
                invalid_ids.append(item.evidence_id)
                continue

            inspection = payload.get("inspection")

            if not isinstance(inspection, Mapping):
                invalid_ids.append(item.evidence_id)
                continue

            source_format = str(
                inspection.get("source_format", "")
            ).casefold()
            schema = inspection.get("schema")
            product_count = inspection.get("product_count")

            if (
                source_format != "rfa"
                or not isinstance(schema, str)
                or not schema.strip()
                or isinstance(product_count, bool)
                or not isinstance(product_count, int)
                or product_count <= 0
            ):
                invalid_ids.append(item.evidence_id)
                continue

            product_counts.append(product_count)

        valid = not invalid_ids

        return self.result(
            RuleStatus.PASS if valid else RuleStatus.FAIL,
            evidence_refs=tuple(invalid_ids) if invalid_ids else _refs(items),
            observed_value={
                "identity_record_count": len(items),
                "invalid_identity_count": len(invalid_ids),
                "product_counts": product_counts,
            },
            expected_value={
                "invalid_identity_count": 0,
                "minimum_product_count": 1,
                "source_format": "rfa",
            },
            metrics={
                "invalid_evidence_ids": invalid_ids,
            },
        )


class _StructuredSectionRule(BaseRules):
    """
    Shared structural validation for canonical extracted FamilyEvidence groups.

    Subclasses provide only the canonical group/path and rule definition.
    """

    GROUP: ClassVar[str]
    PATH_PREFIX: ClassVar[str]

    def _evaluate(self, context: AuditContext) -> RuleResult:
        items = context.group(self.GROUP)

        invalid_ids: list[str] = []

        for item in items:
            payload = _primitive_mapping(item)
            logical_path = (
                item.logical_location.path
                if item.logical_location is not None
                else None
            )

            if (
                payload is None
                or logical_path is None
                or not logical_path.startswith(self.PATH_PREFIX)
            ):
                invalid_ids.append(item.evidence_id)

        valid = not invalid_ids

        return self.result(
            RuleStatus.PASS if valid else RuleStatus.FAIL,
            evidence_refs=tuple(invalid_ids) if invalid_ids else _refs(items),
            observed_value={
                "section": self.GROUP,
                "record_count": len(items),
                "invalid_record_count": len(invalid_ids),
            },
            expected_value={
                "section": self.GROUP,
                "invalid_record_count": 0,
                "logical_path_prefix": self.PATH_PREFIX,
            },
            metrics={
                "invalid_evidence_ids": invalid_ids,
            },
        )


class RFATypeCatalogIntegrityRule(_StructuredSectionRule):
    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.EVIDENCE.TYPE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("type_catalog",),
        severity_policy="rfa_type_catalog_integrity",
        known_limitations=(
            "Checks canonical type-catalog evidence structure only; "
            "it does not impose type naming conventions.",
        ),
    )

    GROUP = "type_catalog"
    PATH_PREFIX = "datasets.elements["


class RFAParameterEvidenceIntegrityRule(_StructuredSectionRule):
    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.EVIDENCE.PARAM.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("parameters",),
        severity_policy="rfa_parameter_evidence_integrity",
        known_limitations=(
            "Checks parameter evidence structure only; it does not "
            "invent mandatory parameter names or values.",
        ),
    )

    GROUP = "parameters"
    PATH_PREFIX = "datasets.properties["


class RFAMaterialEvidenceIntegrityRule(_StructuredSectionRule):
    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.EVIDENCE.MATERIAL.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("materials",),
        severity_policy="rfa_material_evidence_integrity",
        known_limitations=(
            "Checks material evidence structure only; it does not "
            "impose an organization material standard.",
        ),
    )

    GROUP = "materials"
    PATH_PREFIX = "datasets.materials["


class RFAGeometryEvidenceIntegrityRule(_StructuredSectionRule):
    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.EVIDENCE.GEOMETRY.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("geometry_metrics",),
        severity_policy="rfa_geometry_evidence_integrity",
        known_limitations=(
            "Checks geometry-metric evidence structure only; it does "
            "not impose geometric thresholds or modeling conventions.",
        ),
    )

    GROUP = "geometry_metrics"
    PATH_PREFIX = "datasets.quantities["


RFA_BASELINE_RULES: tuple[BaseRules, ...] = (
    RFASourceIntegrityRule(),
    RFAFamilyIdentityIntegrityRule(),
    RFATypeCatalogIntegrityRule(),
    RFAParameterEvidenceIntegrityRule(),
    RFAMaterialEvidenceIntegrityRule(),
    RFAGeometryEvidenceIntegrityRule(),
)


__all__ = [
    "RFASourceIntegrityRule",
    "RFAFamilyIdentityIntegrityRule",
    "RFATypeCatalogIntegrityRule",
    "RFAParameterEvidenceIntegrityRule",
    "RFAMaterialEvidenceIntegrityRule",
    "RFAGeometryEvidenceIntegrityRule",
    "RFA_BASELINE_RULES",
]
