"""

"""

import json
import hashlib
import os

from typing import Any, Dict, List, Optional

class ReportingError(Exception):
    """Base class for reporting errors."""
    pass

class ManifestValidationError(ReportingError):
    """Raised when the manifest validation fails."""
    pass

class EvidenceError(ReportingError):
    """Raised when an error occurs while processing evidence."""
    pass
class EvidenceManifestError(ReportingError):
    """Raised when the evidence manifest validation fails."""
    pass

class EvidenceProvenanceError(ReportingError):
    """Raised when the evidence provenance validation fails."""
    pass

class RequirementMatrixError(ReportingError):
    """Raised when the requirement matrix validation fails."""
    pass

class ArtifactManifestError(ReportingError):
    """Raised when the artifact manifest validation fails."""
    pass

class PackageBuilderError(ReportingError):
    """Raised when the package builder validation fails."""
    pass

class ReportBuilderError(ReportingError):   
    """Raised when the report builder validation fails."""
    pass

class FindingJSONError(ReportingError):
    """Raised when the finding JSON serialization fails."""
    pass

class RemediationCSVError(ReportingError):
    """Raised when the remediation CSV generation fails."""
    pass

class ReportTemplateError(ReportingError):
    """Raised when the report template rendering fails."""
    pass

class ReportManifestError(ReportingError):
    """Raised when the report manifest validation fails."""
    pass

class ReportManifestValidationError(ReportingError):
    """Raised when the report manifest validation fails."""
    pass