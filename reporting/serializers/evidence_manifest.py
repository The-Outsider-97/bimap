"""
Serializes evidence data to JSON format.
"""

from __future__ import annotations

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...domain.evidence.models import *
from ...domain.evidence.provenance import *
from ...contracts.evidence import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Evidence Manifest")
printer = PrettyPrinter()


class EvidenceManifest:
    def __init__(self) -> None:
        logger(f"Evidence Manifest successfully initialized")
        pass

    def validate(self, evidence_package: EvidencePackage) -> None:
        """
        Validates the evidence package manifest before semantic processing begins.
        """
        logger(f"Validating evidence package manifest")
        if not evidence_package.manifest:
            raise EvidenceManifestError("Evidence package manifest is missing.")
        if not isinstance(evidence_package.manifest, dict):
            raise EvidenceManifestError("Evidence package manifest is not a dictionary.")

        return self.validate_provenance(evidence_package)

    def validate_provenance(self, evidence_package: EvidencePackage) -> None:
        """
        Validates the evidence package provenance before semantic processing begins.
        """
        logger(f"Validating evidence package provenance")
        if not evidence_package.provenance:
            raise EvidenceProvenanceError("Evidence package provenance is missing.")
        if not isinstance(evidence_package.provenance, dict):
            raise EvidenceProvenanceError("Evidence package provenance is not a dictionary.")

        return self.validate_evidence(evidence_package)

    def validate_evidence(self, evidence_package: EvidencePackage) -> None:
        """
        Validates the evidence package evidence before semantic processing begins.
        """
        logger(f"Validating evidence package evidence")
        if not evidence_package.evidence:
            raise EvidenceError("Evidence package evidence is missing.")
        if not isinstance(evidence_package.evidence, list):
            raise EvidenceError("Evidence package evidence is not a list.")
    

__all__ = ["EvidenceManifest"]

