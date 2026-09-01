"""
Structured error hierarchy for the BIMAP reporting layer.

The reporting layer converts validated BIMAP domain/contract objects into
customer-facing artifacts. Errors in this layer therefore describe reporting,
serialization, manifest, packaging, and rendering failures; they must not
replace lower-layer domain or contract errors.

Design rules
------------
- Exceptions are structured and machine-readable through ``code``/``to_dict``.
- Exception construction has no logging side effects. A handled boundary may
  explicitly call ``announce()`` once, avoiding duplicate error logs.
- Diagnostic context is bounded and redacted so raw customer evidence, report
  text, filenames, credentials, and other sensitive values are not echoed into
  logs by accident.
- Existing scaffold exception names are retained for compatibility.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Reporting Errors")
printer = PrettyPrinter()

_REDACTED = "<redacted>"
_MAX_CONTEXT_STRING = 256
_MAX_CONTEXT_ITEMS = 32
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "content",
    "cookie",
    "credential",
    "evidence_value",
    "explanation",
    "extracted_value",
    "filename",
    "observed_value",
    "expected_value",
    "password",
    "path",
    "payload",
    "raw",
    "remediation",
    "secret",
    "source_requirement",
    "token",
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded, log-safe diagnostic representation."""
    if depth >= 3:
        return f"<{type(value).__name__}>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        if len(value) <= _MAX_CONTEXT_STRING:
            return value
        return f"{value[:_MAX_CONTEXT_STRING]}…"

    if isinstance(value, Mapping):
        mapping_rendered: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTEXT_ITEMS:
                mapping_rendered["__truncated__"] = True
                break
            rendered_key = str(key)
            mapping_rendered[rendered_key] = (
                _REDACTED
                if _is_sensitive_key(rendered_key)
                else _safe_context_value(item, depth=depth + 1)
            )
        return mapping_rendered

    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = list(value)
        sequence_rendered: list[Any] = [
            _safe_context_value(item, depth=depth + 1)
            for item in sequence[:_MAX_CONTEXT_ITEMS]
        ]
        if len(sequence) > _MAX_CONTEXT_ITEMS:
            sequence_rendered.append("<truncated>")
        return sequence_rendered

    return f"<{type(value).__name__}>"


def sanitize_reporting_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize reporting diagnostic context without leaking report content."""
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise TypeError(
            "Reporting error context must be a mapping or None, "
            f"got {type(context).__name__}."
        )

    safe: dict[str, Any] = {}
    for key, value in context.items():
        rendered_key = str(key)
        safe[rendered_key] = (
            _REDACTED
            if _is_sensitive_key(rendered_key)
            else _safe_context_value(value)
        )
    return safe


class ReportingError(Exception):
    """Base exception for all BIMAP reporting-layer failures."""

    code = "BIMAP.REPORTING.ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        self.message = normalized_message
        self.component = str(component).strip() if component is not None else None
        self.field = str(field).strip() if field is not None else None
        self.context = sanitize_reporting_context(context)
        self.cause = cause

        rendered = normalized_message
        qualifiers: list[str] = []
        if self.component:
            qualifiers.append(f"component={self.component}")
        if self.field:
            qualifiers.append(f"field={self.field}")
        if qualifiers:
            rendered = f"{rendered} [{', '.join(qualifiers)}]"

        super().__init__(rendered)

    def announce(
        self,
        *,
        label: str = "REPORTING",
        level: str = "error",
    ) -> None:
        """Explicitly emit this handled error once through SLAI diagnostics."""
        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "reporting_error_announced",
                "code": self.code,
                "type": self.__class__.__name__,
                "component": self.component,
                "field": self.field,
            }
        )

    def to_dict(
        self,
        *,
        include_context: bool = True,
        include_cause_type: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic logging/observability representation."""
        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.component:
            payload["component"] = self.component
        if self.field:
            payload["field"] = self.field
        if include_context and self.context:
            payload["context"] = dict(self.context)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__
        return payload


class ReportingValidationError(ReportingError):
    """Raised when reporting input violates a reporting-layer constraint."""

    code = "BIMAP.REPORTING.VALIDATION"


class ReportingSerializationError(ReportingError):
    """Raised when a validated report value cannot be serialized safely."""

    code = "BIMAP.REPORTING.SERIALIZATION"


class ReportingIntegrityError(ReportingError):
    """Raised when report records are internally inconsistent or ambiguous."""

    code = "BIMAP.REPORTING.INTEGRITY"


class ReportingConfigurationError(ReportingError):
    """Raised when a serializer/reporting component is misconfigured."""

    code = "BIMAP.REPORTING.CONFIGURATION"


class UnsupportedReportingInputError(ReportingValidationError):
    """Raised when a serializer receives an unsupported object type."""

    code = "BIMAP.REPORTING.INPUT.UNSUPPORTED"


class DuplicateReportingRecordError(ReportingIntegrityError):
    """Raised when a report artifact contains duplicate stable identifiers."""

    code = "BIMAP.REPORTING.RECORD.DUPLICATE"


class ManifestValidationError(ReportingValidationError):
    """Base error for reporting-manifest validation failures."""

    code = "BIMAP.REPORTING.MANIFEST.VALIDATION"


class EvidenceError(ReportingValidationError):
    """Raised when evidence cannot be accepted by a reporting serializer."""

    code = "BIMAP.REPORTING.EVIDENCE"


class EvidenceManifestError(ManifestValidationError):
    """Raised when the evidence manifest cannot be validated or generated."""

    code = "BIMAP.REPORTING.EVIDENCE_MANIFEST"


class EvidenceProvenanceError(EvidenceManifestError):
    """Raised when evidence source provenance is inconsistent in a manifest."""

    code = "BIMAP.REPORTING.EVIDENCE_MANIFEST.PROVENANCE"


class RequirementMatrixError(ReportingSerializationError):
    """Raised when requirement-matrix generation/serialization fails."""

    code = "BIMAP.REPORTING.REQUIREMENT_MATRIX"


class ArtifactManifestError(ReportingIntegrityError):
    """Raised when generated artifact-manifest state is inconsistent."""

    code = "BIMAP.REPORTING.ARTIFACT_MANIFEST"


class PackageBuilderError(ReportingError):
    """Raised when a customer delivery package cannot be constructed."""

    code = "BIMAP.REPORTING.PACKAGE_BUILDER"


class ReportBuilderError(ReportingError):
    """Raised when the human-readable report cannot be assembled."""

    code = "BIMAP.REPORTING.REPORT_BUILDER"


class FindingJSONError(ReportingSerializationError):
    """Raised when findings.json generation fails."""

    code = "BIMAP.REPORTING.FINDINGS_JSON"


class RemediationCSVError(ReportingSerializationError):
    """Raised when remediation.csv generation fails."""

    code = "BIMAP.REPORTING.REMEDIATION_CSV"


class ReportTemplateError(ReportingError):
    """Raised when a report template cannot be rendered safely."""

    code = "BIMAP.REPORTING.TEMPLATE"


class ReportManifestError(ReportingIntegrityError):
    """Raised when the report-manifest state is internally inconsistent."""

    code = "BIMAP.REPORTING.REPORT_MANIFEST"


class ReportManifestValidationError(ManifestValidationError):
    """Raised when a report manifest fails reporting-layer validation."""

    code = "BIMAP.REPORTING.REPORT_MANIFEST.VALIDATION"


__all__ = [
    "sanitize_reporting_context",
    "ReportingError",
    "ReportingValidationError",
    "ReportingSerializationError",
    "ReportingIntegrityError",
    "ReportingConfigurationError",
    "UnsupportedReportingInputError",
    "DuplicateReportingRecordError",
    "ManifestValidationError",
    "EvidenceError",
    "EvidenceManifestError",
    "EvidenceProvenanceError",
    "RequirementMatrixError",
    "ArtifactManifestError",
    "PackageBuilderError",
    "ReportBuilderError",
    "FindingJSONError",
    "RemediationCSVError",
    "ReportTemplateError",
    "ReportManifestError",
    "ReportManifestValidationError",
]


if __name__ == "__main__":
    print("\n=== Running Reporting Errors Self-Test ===\n")
    printer.status("TEST", "Reporting error hierarchy initialized", "info")

    sample = FindingJSONError(
        "Unable to serialize finding.",
        component="findings_json",
        field="finding",
        context={"finding_id": "F-1", "payload": "must-not-leak"},
        cause=ValueError("internal detail"),
    )
    payload = sample.to_dict()
    assert payload["code"] == "BIMAP.REPORTING.FINDINGS_JSON"
    assert payload["context"]["payload"] == _REDACTED
    assert "must-not-leak" not in str(payload)
    assert payload["cause_type"] == "ValueError"
    printer.status("PASS", "Structured reporting error serialization", "success")

    print("\n=== Test ran successfully ===\n")
