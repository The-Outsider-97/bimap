"""Approved deterministic rule set for BIMAP Revit Family Audit."""

from __future__ import annotations

from .baseline import (
    RFA_BASELINE_RULES,
    RFAFamilyIdentityIntegrityRule,
    RFAGeometryEvidenceIntegrityRule,
    RFAMaterialEvidenceIntegrityRule,
    RFAParameterEvidenceIntegrityRule,
    RFASourceIntegrityRule,
    RFATypeCatalogIntegrityRule,
)
from .identity import (
    RFA_IDENTITY_RULES,
    RFAFamilyCategoryConsistencyRule,
    RFAFamilyIdentityPresenceRule,
    RFADuplicateTypeIdentityRule,
    RFATypeNameIntegrityRule,
)
from .parameters import (
    RFA_PARAMETER_RULES,
    RFADuplicateParameterIdentityRule,
    RFAParameterGovernanceRule,
    RFAParameterMetadataRule,
)
from .formulas import (
    RFA_FORMULA_RULES,
    RFAFormulaGovernanceRule,
    RFAFormulaMetadataRule,
    RFAFormulaReferenceRule,
)
from .standards import (
    RFA_STANDARDS_RULES,
    RFAIdentityStandardsRule,
    RFAOrganizationProfile,
    RFAOrganizationProfileValidityRule,
    resolve_organization_profile,
)
from .complexity import (
    RFA_COMPLEXITY_RULES,
    RFACalibratedComplexityRule,
    RFAComplexityMetricIntegrityRule,
)


# Explicit registration order is part of reproducibility. Baseline evidence
# integrity runs first, followed by semantic and policy-dependent assessments.
RFA_RULES = (
    *RFA_BASELINE_RULES,
    *RFA_IDENTITY_RULES,
    *RFA_PARAMETER_RULES,
    *RFA_FORMULA_RULES,
    *RFA_STANDARDS_RULES,
    *RFA_COMPLEXITY_RULES,
)


__all__ = [
    "RFA_RULES",
    "RFA_BASELINE_RULES",
    "RFA_IDENTITY_RULES",
    "RFA_PARAMETER_RULES",
    "RFA_FORMULA_RULES",
    "RFA_STANDARDS_RULES",
    "RFA_COMPLEXITY_RULES",
    "RFASourceIntegrityRule",
    "RFAFamilyIdentityIntegrityRule",
    "RFATypeCatalogIntegrityRule",
    "RFAParameterEvidenceIntegrityRule",
    "RFAMaterialEvidenceIntegrityRule",
    "RFAGeometryEvidenceIntegrityRule",
    "RFAFamilyIdentityPresenceRule",
    "RFATypeNameIntegrityRule",
    "RFADuplicateTypeIdentityRule",
    "RFAFamilyCategoryConsistencyRule",
    "RFAParameterMetadataRule",
    "RFADuplicateParameterIdentityRule",
    "RFAParameterGovernanceRule",
    "RFAFormulaMetadataRule",
    "RFAFormulaReferenceRule",
    "RFAFormulaGovernanceRule",
    "RFAOrganizationProfile",
    "resolve_organization_profile",
    "RFAOrganizationProfileValidityRule",
    "RFAIdentityStandardsRule",
    "RFAComplexityMetricIntegrityRule",
    "RFACalibratedComplexityRule",
]
