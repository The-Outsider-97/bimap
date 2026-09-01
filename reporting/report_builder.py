"""
Assemble BIMAP report deliverables from validated reporting contracts.

``ReportBuilder`` coordinates the four structured serializers, an optional
human-readable report renderer, and ``ArtifactManifest``. It does not decide
which findings are allowed to be released, perform audit reasoning, persist
artifacts, or package them into ZIP. Those concerns belong respectively to
application/governance policy, the audit engine/SLAI boundary, storage ports,
and ``PackageBuilder``.

The current repository does not yet contain a concrete report template or PDF
renderer under ``reporting/templates``. A renderer is therefore injected via a
small protocol. This avoids fabricating a template implementation while keeping
``R3D_Audit_Report.pdf`` a first-class, explicitly required artifact whenever
``include_pdf=True``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ..contracts.utils.contracts_errors import ContractError
from .artifact_manifest import ArtifactManifest
from .serializers.evidence_manifest import EvidenceManifest
from .serializers.findings_json import FindingJSON
from .serializers.remediation_csv import RemediationCSV
from .serializers.requirement_matrix import RequirementMatrix
from .utils.reporting_errors import *
from .utils.reporting_helpers import announce_reporting_action
from ..contracts.evidence import EvidenceContract
from ..contracts.finding import FindingContract
from ..contracts.report_manifest import ReportManifest
from ..contracts.requirement import RequirementContract
from ..contracts.versions import REPORT_MANIFEST_SCHEMA_VERSION
from ..domain.evidence.models import EvidenceItem
from ..domain.governance.review import Review
from ..domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Report Builder")
printer = PrettyPrinter()

_COMPONENT = "report_builder"

_REPORT_FILENAME = "R3D_Audit_Report.pdf"
_FINDINGS_FILENAME = "findings.json"
_REMEDIATION_FILENAME = "remediation.csv"
_EVIDENCE_FILENAME = "evidence_manifest.json"
_REQUIREMENT_MATRIX_FILENAME = "requirement_matrix.csv"


@runtime_checkable
class ReportRenderer(Protocol):
    """Minimal renderer boundary for the human-readable BIMAP report."""

    def render(self, *, context: Mapping[str, Any]) -> bytes:
        """Render a complete human-readable report as immutable bytes."""
        ...


@dataclass(frozen=True, slots=True)
class ReportBuildResult:
    """Immutable result of one report-build operation."""

    manifest: ReportManifest
    artifacts: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ReportManifest):
            raise TypeError("manifest must be a ReportManifest instance.")
        if not isinstance(self.artifacts, Mapping):
            raise TypeError("artifacts must be a mapping.")

        normalized: dict[str, bytes] = {}
        for filename, payload in self.artifacts.items():
            if not isinstance(filename, str) or not filename:
                raise TypeError("artifact filenames must be non-empty strings.")
            if not isinstance(payload, bytes):
                raise TypeError("ReportBuildResult artifact payloads must be bytes.")
            normalized[filename] = payload
        object.__setattr__(self, "artifacts", MappingProxyType(normalized))

    def artifact(self, filename: str) -> bytes | None:
        """Return artifact bytes by exact generated filename."""
        return self.artifacts.get(filename)

    def manifest_json(self, *, pretty: bool = True) -> str:
        """Return the canonical external report-manifest JSON."""
        return self.manifest.to_json(pretty=pretty)


class ReportBuilder:
    """Coordinate structured artifact generation and optional PDF rendering."""

    def __init__(
        self,
        *,
        renderer: ReportRenderer | None = None,
        finding_serializer: FindingJSON | None = None,
        remediation_serializer: RemediationCSV | None = None,
        evidence_serializer: EvidenceManifest | None = None,
        requirement_serializer: RequirementMatrix | None = None,
        artifact_manifest: ArtifactManifest | None = None,
    ) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing report builder",
            event="report_builder_init",
            context={"renderer_configured": renderer is not None},
        )

        if renderer is not None and not isinstance(renderer, ReportRenderer):
            raise ReportingValidationError(
                "renderer must implement ReportRenderer.render(context=...).",
                component=_COMPONENT,
                field="renderer",
                context={"received_type": type(renderer).__name__},
            )

        self.renderer = renderer
        self.finding_json_serializer = finding_serializer or FindingJSON()
        self.remediation_csv_serializer = remediation_serializer or RemediationCSV()
        self.evidence_manifest_serializer = evidence_serializer or EvidenceManifest()
        self.requirement_matrix_serializer = requirement_serializer or RequirementMatrix()
        self.artifact_manifest = artifact_manifest or ArtifactManifest()

        expected_components = (
            (self.finding_json_serializer, FindingJSON, "finding_serializer"),
            (self.remediation_csv_serializer, RemediationCSV, "remediation_serializer"),
            (self.evidence_manifest_serializer, EvidenceManifest, "evidence_serializer"),
            (self.requirement_matrix_serializer, RequirementMatrix, "requirement_serializer"),
            (self.artifact_manifest, ArtifactManifest, "artifact_manifest"),
        )
        for component, expected_type, field in expected_components:
            if not isinstance(component, expected_type):
                raise ReportingValidationError(
                    f"{field} must be a {expected_type.__name__} instance.",
                    component=_COMPONENT,
                    field=field,
                    context={"received_type": type(component).__name__},
                )

        logger.info({"event": "report_builder_initialized"})

    @staticmethod
    def _validate_reviews(reviews: Iterable[Review]) -> tuple[Review, ...]:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating governance review context",
            event="report_builder_review_validate_start",
        )

        if isinstance(reviews, (str, bytes, bytearray, Mapping)):
            raise ReportingValidationError(
                "reviews must be an iterable of Review instances.",
                component=_COMPONENT,
                field="reviews",
                context={"received_type": type(reviews).__name__},
            )
        try:
            result = tuple(reviews)
        except TypeError as exc:
            raise ReportingValidationError(
                "reviews must be iterable.",
                component=_COMPONENT,
                field="reviews",
                context={"received_type": type(reviews).__name__},
                cause=exc,
            ) from exc

        seen: set[str] = set()
        for index, review in enumerate(result):
            if not isinstance(review, Review):
                raise ReportingValidationError(
                    "reviews contains a non-Review value.",
                    component=_COMPONENT,
                    field=f"reviews[{index}]",
                    context={"received_type": type(review).__name__},
                )
            if review.review_id in seen:
                raise ReportingValidationError(
                    "reviews contains duplicate review identifiers.",
                    component=_COMPONENT,
                    field="reviews",
                    context={"review_id": review.review_id},
                )
            seen.add(review.review_id)
        return result

    @staticmethod
    def _single_contract_version(records: Iterable[Any], *, field: str) -> str | None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving report contract schema version",
            event="report_builder_contract_version_start",
            context={"field": field},
        )

        versions = {getattr(record, "schema_version", None) for record in records}
        versions.discard(None)
        if not versions:
            return None
        if len(versions) != 1:
            raise ReportBuilderError(
                "One report artifact may not mix multiple schema versions of the same contract.",
                component=_COMPONENT,
                field=field,
                context={"versions": tuple(sorted(str(v) for v in versions))},
            )
        return str(next(iter(versions)))

    def _render_pdf(self, *, context: Mapping[str, Any]) -> bytes:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Rendering human-readable BIMAP report",
            event="report_builder_pdf_render_start",
        )

        if self.renderer is None:
            raise ReportTemplateError(
                "include_pdf=True requires an injected ReportRenderer. "
                "No concrete renderer/template is currently defined in reporting/templates.",
                component=_COMPONENT,
                field="renderer",
            )
        try:
            payload = self.renderer.render(context=context)
        except ReportingError:
            raise
        except Exception as exc:
            raise ReportTemplateError(
                "Human-readable report renderer failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise ReportTemplateError(
                "ReportRenderer.render() must return bytes-like PDF content.",
                component=_COMPONENT,
                field="renderer",
                context={"received_type": type(payload).__name__},
            )
        result = bytes(payload)
        if not result:
            raise ReportTemplateError(
                "ReportRenderer returned an empty human-readable report.",
                component=_COMPONENT,
                field="renderer",
            )
        return result

    def build_report(
        self,
        *,
        findings: Iterable[FindingContract],
        evidence: Iterable[EvidenceContract | EvidenceItem],
        requirements: Iterable[RequirementContract] = (),
        reviews: Iterable[Review] = (),
        report_id: str,
        order_id: str,
        report_version: str,
        generated_at: Any,
        artifact_ids: Mapping[str, str],
        expires_at: Any | None = None,
        software_versions: Mapping[str, str] | None = None,
        ruleset_versions: Mapping[str, str] | None = None,
        include_pdf: bool = True,
    ) -> ReportBuildResult:
        """
        Build BIMAP customer deliverables and their authoritative manifest.

        Structured artifacts are always generated. ``requirement_matrix.csv``
        is emitted only when requirement assessments are present, matching its
        BIM QA / Combined Audit role. The PDF is emitted only when
        ``include_pdf`` is true and an explicit renderer is available.
        """
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building BIMAP report deliverables",
            event="report_builder_build_start",
            context={
                "report_id": report_id,
                "order_id": order_id,
                "include_pdf": include_pdf,
            },
        )

        if not isinstance(artifact_ids, Mapping):
            raise ReportingValidationError(
                "artifact_ids must be a mapping of generated filename to stable artifact ID.",
                component=_COMPONENT,
                field="artifact_ids",
                context={"received_type": type(artifact_ids).__name__},
            )

        try:
            finding_contracts = self.finding_json_serializer.validate_many(findings)
            evidence_contracts = self.evidence_manifest_serializer.validate(evidence)
            requirement_contracts = self.requirement_matrix_serializer.validate(requirements)
            review_records = self._validate_reviews(reviews)

            findings_payload = self.finding_json_serializer.to_payload(finding_contracts)
            evidence_payload = self.evidence_manifest_serializer.generate_manifest(evidence_contracts)
            remediation_rows = self.remediation_csv_serializer.to_rows(finding_contracts)
            requirement_rows = self.requirement_matrix_serializer.generate_matrix(requirement_contracts)

            artifacts: dict[str, bytes] = {
                _FINDINGS_FILENAME: self.finding_json_serializer.serialize_many(finding_contracts, pretty=True).encode("utf-8"),
                _REMEDIATION_FILENAME: self.remediation_csv_serializer.generate_csv(finding_contracts).encode("utf-8"),
                _EVIDENCE_FILENAME: self.evidence_manifest_serializer.serialize(evidence_contracts, pretty=True).encode("utf-8"),
            }

            if requirement_contracts:
                artifacts[_REQUIREMENT_MATRIX_FILENAME] = (
                    self.requirement_matrix_serializer.generate_csv(requirement_contracts).encode("utf-8")
                )

            render_context: dict[str, Any] = {
                "report": {
                    "report_id": report_id,
                    "order_id": order_id,
                    "report_version": report_version,
                    "generated_at": generated_at,
                    "expires_at": expires_at,
                },
                "findings": findings_payload,
                "evidence_manifest": evidence_payload,
                "remediation": remediation_rows,
                "requirement_matrix": requirement_rows,
                "reviews": [review.to_dict() for review in review_records],
            }

            if include_pdf:
                artifacts[_REPORT_FILENAME] = self._render_pdf(context=render_context)

            contract_versions: dict[str, str] = {
                "report_manifest": REPORT_MANIFEST_SCHEMA_VERSION,
            }
            finding_version = self._single_contract_version(finding_contracts, field="findings.schema_version")
            evidence_version = self._single_contract_version(evidence_contracts, field="evidence.schema_version")
            requirement_version = self._single_contract_version(requirement_contracts, field="requirements.schema_version")
            if finding_version is not None:
                contract_versions["finding"] = finding_version
            if evidence_version is not None:
                contract_versions["evidence"] = evidence_version
            if requirement_version is not None:
                contract_versions["requirement"] = requirement_version

            manifest = self.artifact_manifest.create_manifest(
                report_id=report_id,
                order_id=order_id,
                report_version=report_version,
                generated_at=generated_at,
                expires_at=expires_at,
                artifacts=artifacts,
                artifact_ids=artifact_ids,
                finding_refs=[item.finding_id for item in finding_contracts],
                requirement_refs=[item.requirement_id for item in requirement_contracts],
                evidence_refs=[item.evidence_id for item in evidence_contracts],
                contract_versions=contract_versions,
                software_versions=dict(software_versions or {}),
                ruleset_versions=dict(ruleset_versions or {}),
            )
            self.artifact_manifest.verify(manifest, artifacts)

            result = ReportBuildResult(manifest=manifest, artifacts=artifacts)
            logger.info(
                {
                    "event": "report_deliverables_built",
                    "report_id": manifest.report_id,
                    "artifact_count": len(result.artifacts),
                    "finding_count": len(finding_contracts),
                    "evidence_count": len(evidence_contracts),
                    "requirement_count": len(requirement_contracts),
                    "review_count": len(review_records),
                }
            )
            return result
        except ReportingError:
            raise
        except (ContractError, DomainError) as exc:
            raise ReportBuilderError(
                "A domain or external contract failed while building report deliverables.",
                component=_COMPONENT,
                cause=exc,
            ) from exc
        except (KeyError, TypeError, UnicodeError, ValueError) as exc:
            raise ReportBuilderError(
                "BIMAP report deliverable generation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc


__all__ = [
    "ReportRenderer",
    "ReportBuildResult",
    "ReportBuilder"
]