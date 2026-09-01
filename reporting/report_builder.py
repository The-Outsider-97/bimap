"""
Produces the human-readable BIMAP audit report.
"""

from __future__ import annotations

from .utils.reporting_errors import *
from .utils.reporting_helpers import *
from ..domain.findings.models import *
from ..domain.governance.review import *
from ..contracts.report_manifest import *
from .serializers.findings_json import *
from .serializers.remediation_csv import *
from .serializers.evidence_manifest import *
from .serializers.requirement_matrix import *
from .templates import * # Consumes templates from the templates package
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Report Builder")
printer = PrettyPrinter()


class ReportBuilder:
    def __init__(self) -> None:
        self.finding_json_serializer = FindingJSON()
        self.remediation_csv_serializer = RemediationCSV()
        self.evidence_manifest_serializer = EvidenceManifest()
        self.requirement_matrix_serializer = RequirementMatrix()
        self.report_template = ReportTemplate()
        logger(f"Report Builder successfully initialized")
        pass

    def build_report(self, findings: list[Finding], review: Review) -> None:
        """
        Builds the BIMAP audit report based on the provided findings and review.
        """
        logger(f"Building report with {len(findings)} findings and review {review.review_id}")
        # Serialize findings to JSON
        if not findings:
            logger(f"No findings provided, skipping findings serialization")
            serialized_findings = []
        else:
            serialized_findings = [self.finding_json_serializer.serialize(finding) for finding in findings]
            logger(f"Serialized {len(serialized_findings)} findings to JSON")

        # Generate remediation CSV
        if not findings:
            logger(f"No findings provided, skipping remediation CSV generation")
            remediation_csv = ""
        else:
            remediation_csv = self.remediation_csv_serializer.generate_csv(findings)
            logger(f"Generated remediation CSV with {len(remediation_csv.splitlines())} lines")

        # Generate evidence manifest
        if not findings:
            logger(f"No findings provided, skipping evidence manifest generation")
            evidence_manifest = {}
        else:
            evidence_manifest = self.evidence_manifest_serializer.generate_manifest(findings)
            logger(f"Generated evidence manifest with {len(evidence_manifest)} entries")

        # Generate requirement matrix
        if not findings:
            logger(f"No findings provided, skipping requirement matrix generation")
            requirement_matrix = []
        else:
            requirement_matrix = self.requirement_matrix_serializer.generate_matrix(findings)
            logger(f"Generated requirement matrix with {len(requirement_matrix)} entries")

        # Compile the report using templates
        report_content = self.report_template.render(
            findings=serialized_findings,
            review=review,
            remediation_csv=remediation_csv,
            evidence_manifest=evidence_manifest,
            requirement_matrix=requirement_matrix
        )
        logger(f"Compiled report content with length {len(report_content)} characters")

__all__ = ["ReportBuilder"]