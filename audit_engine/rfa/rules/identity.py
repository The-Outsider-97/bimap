"""Semantic family-identity rules for BIMAP Revit Family Audit."""

from __future__ import annotations

from ...context import AuditContext
from ...rules.base import BaseRules, RuleDefinition, RuleResult, RuleStatus
from ....domain.products.models import ProductCode
from ._helpers import (
    canonical_key,
    canonical_name,
    family_category,
    family_identity_payload,
    family_name,
    group_mappings,
    refs,
    section_assessed,
    text_for,
    type_name,
)


_PRODUCT = ProductCode.FAMILY_AUDIT
_VERSION = "1.0.0"


class RFAFamilyIdentityPresenceRule(BaseRules):
    """Require extracted family name and category to be observable and non-empty."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.IDENTITY.PRESENCE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_family_identity_presence",
        known_limitations=(
            "Checks extracted identity presence only; it does not impose an organization naming convention.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        items = context.group("family_identity")
        name = family_name(context)
        category = family_category(context)
        missing = tuple(
            field
            for field, value in (("family_name", name), ("category", category))
            if not value
        )
        assessed = section_assessed(context, "identity")
        status = (
            RuleStatus.PASS
            if not missing
            else RuleStatus.FAIL
            if assessed is True
            else RuleStatus.UNKNOWN
        )
        return self.result(
            status,
            evidence_refs=refs(items),
            observed_value={"family_name": name, "category": category},
            expected_value={"family_name": "non-empty", "category": "non-empty"},
            metrics={
                "missing_fields": list(missing),
                "section_assessed": assessed,
            },
        )


class RFATypeNameIntegrityRule(BaseRules):
    """Require every extracted family type to expose a non-empty type name."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.IDENTITY.TYPE_NAME.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_type_name_integrity",
        known_limitations=(
            "Validates extracted type-name presence only; naming-policy conformance is evaluated separately.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        records = group_mappings(context, "type_catalog")
        if not records:
            assessed = section_assessed(context, "type_catalog")
            status = RuleStatus.FAIL if assessed is True else RuleStatus.UNKNOWN
            identity_items = context.group("family_identity")
            return self.result(
                status,
                evidence_refs=refs(identity_items),
                observed_value={"type_count": 0, "unnamed_type_count": 0},
                expected_value={"minimum_type_count": 1, "unnamed_type_count": 0},
                metrics={"section_assessed": assessed},
            )

        unnamed = [
            item.evidence_id
            for item, record in records
            if type_name(record) is None
        ]
        return self.result(
            RuleStatus.PASS if not unnamed else RuleStatus.FAIL,
            evidence_refs=tuple(item.evidence_id for item, _ in records),
            observed_value={
                "type_count": len(records),
                "unnamed_type_count": len(unnamed),
            },
            expected_value={"minimum_type_count": 1, "unnamed_type_count": 0},
            metrics={"unnamed_evidence_ids": unnamed},
        )


class RFADuplicateTypeIdentityRule(BaseRules):
    """Detect duplicate extracted type identities after conservative name normalization."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.IDENTITY.DUPLICATE_TYPE.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_duplicate_type_identity",
        known_limitations=(
            "Duplicate detection is name-based when no stable native type identifier is supplied.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        records = group_mappings(context, "type_catalog")
        if len(records) < 2:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=tuple(item.evidence_id for item, _ in records),
                observed_value={"type_count": len(records)},
                expected_value={"duplicate_identity_count": 0},
            )

        seen: dict[str, str] = {}
        duplicates: list[dict[str, str]] = []
        for item, record in records:
            stable = text_for(record, "unique_id", "uniqueId", "type_id", "typeId", "element_id")
            name = type_name(record)
            identity = (
                f"id:{canonical_key(stable)}"
                if stable
                else f"name:{canonical_name(name)}"
            )
            if identity in {"id:", "name:"}:
                continue
            previous = seen.get(identity)
            if previous is None:
                seen[identity] = item.evidence_id
            else:
                duplicates.append(
                    {
                        "identity": identity,
                        "first_evidence_id": previous,
                        "duplicate_evidence_id": item.evidence_id,
                    }
                )

        return self.result(
            RuleStatus.FAIL if duplicates else RuleStatus.PASS,
            evidence_refs=tuple(item.evidence_id for item, _ in records),
            observed_value={"duplicate_identity_count": len(duplicates)},
            expected_value={"duplicate_identity_count": 0},
            metrics={"duplicates": duplicates},
        )


class RFAFamilyCategoryConsistencyRule(BaseRules):
    """Check that type records do not contradict the extracted family category."""

    DEFINITION = RuleDefinition(
        rule_id="R3D.RFA.IDENTITY.CATEGORY_CONSISTENCY.001",
        version=_VERSION,
        applicable_products=(_PRODUCT,),
        required_evidence_groups=("family_identity",),
        severity_policy="rfa_family_category_consistency",
        known_limitations=(
            "Only type records that explicitly expose category metadata participate in this comparison.",
        ),
    )

    def _evaluate(self, context: AuditContext) -> RuleResult:
        identity_item, _ = family_identity_payload(context)
        expected_category = family_category(context)
        if expected_category is None:
            return self.result(
                RuleStatus.UNKNOWN,
                evidence_refs=(() if identity_item is None else (identity_item.evidence_id,)),
                metrics={"reason": "family_category_unavailable"},
            )

        records = group_mappings(context, "type_catalog")
        compared = []
        mismatches = []
        for item, record in records:
            category = text_for(record, "category", "category_name", "family_category")
            if category is None:
                continue
            compared.append(item.evidence_id)
            if canonical_name(category) != canonical_name(expected_category):
                mismatches.append(
                    {
                        "evidence_id": item.evidence_id,
                        "category": category,
                    }
                )

        if not compared:
            return self.result(
                RuleStatus.NOT_APPLICABLE,
                evidence_refs=(() if identity_item is None else (identity_item.evidence_id,)),
                observed_value={"family_category": expected_category},
                metrics={"reason": "type_records_do_not_publish_category"},
            )

        evidence_refs = tuple(
            [
                *(() if identity_item is None else (identity_item.evidence_id,)),
                *compared,
            ]
        )
        return self.result(
            RuleStatus.FAIL if mismatches else RuleStatus.PASS,
            evidence_refs=evidence_refs,
            observed_value={
                "family_category": expected_category,
                "mismatch_count": len(mismatches),
            },
            expected_value={"mismatch_count": 0},
            metrics={"mismatches": mismatches},
        )


RFA_IDENTITY_RULES: tuple[BaseRules, ...] = (
    RFAFamilyIdentityPresenceRule(),
    RFATypeNameIntegrityRule(),
    RFADuplicateTypeIdentityRule(),
    RFAFamilyCategoryConsistencyRule(),
)


__all__ = [
    "RFAFamilyIdentityPresenceRule",
    "RFATypeNameIntegrityRule",
    "RFADuplicateTypeIdentityRule",
    "RFAFamilyCategoryConsistencyRule",
    "RFA_IDENTITY_RULES",
]
