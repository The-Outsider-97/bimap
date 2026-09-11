"""
Approved canonical baseline rules for BIMAP Revit Family Audit.

Explicit exports are used instead of runtime discovery or filesystem scanning.
"""

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


RFA_RULES = RFA_BASELINE_RULES


__all__ = [
    "RFA_RULES",
    "RFA_BASELINE_RULES",
    "RFASourceIntegrityRule",
    "RFAFamilyIdentityIntegrityRule",
    "RFATypeCatalogIntegrityRule",
    "RFAParameterEvidenceIntegrityRule",
    "RFAMaterialEvidenceIntegrityRule",
    "RFAGeometryEvidenceIntegrityRule",
]
