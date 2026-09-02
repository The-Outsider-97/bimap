"""
Structured exception hierarchy for the deterministic BIMAP audit engine.

The audit engine is a Level-4 BIMAP subsystem.  It consumes only lower-level
BIMAP domain/contracts and must remain independently testable without the API,
workers, persistence adapters, reporting implementation, or concrete SLAI
runtime.

This module is deliberately kept at the bottom of the audit-engine dependency
graph.  It imports only the Python standard library and the shared SLAI logging
surface already used by BIMAP.  Engine modules can therefore share one stable,
machine-readable error vocabulary without introducing circular imports.

Operational policy
------------------
* Exception construction has no logging side effect.  A failure should normally
  be logged once at the architectural boundary that handles it.
* ``EngineError.announce()`` is available when an operator-facing status is
  explicitly useful.
* Diagnostic context is bounded and redacted.  Raw evidence, document content,
  extracted values, credentials, paths, and serialized payloads must not leak
  into logs through exception metadata.
* Lower-layer exceptions are retained as ``cause`` objects for exception
  chaining, while ``to_dict()`` exposes only the cause type.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Engine Errors")
printer = PrettyPrinter()

_REDACTED = "<redacted>"
_MAX_CONTEXT_DEPTH = 3
_MAX_CONTEXT_ITEMS = 32
_MAX_CONTEXT_STRING = 256
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "content",
    "cookie",
    "credential",
    "document",
    "evidence_content",
    "evidence_value",
    "extracted_value",
    "file_bytes",
    "filename",
    "observed_value",
    "password",
    "path",
    "payload",
    "raw",
    "secret",
    "session",
    "token",
)


def _is_sensitive_key(key: str) -> bool:
    """Return whether a diagnostic key should be redacted by default."""
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded representation suitable for engine diagnostics."""
    if depth >= _MAX_CONTEXT_DEPTH:
        return f"<{type(value).__name__}>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        if len(value) <= _MAX_CONTEXT_STRING:
            return value
        return f"{value[:_MAX_CONTEXT_STRING]}…"

    if isinstance(value, Mapping):
        rendered: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTEXT_ITEMS:
                rendered["__truncated__"] = True
                break
            text_key = str(key)
            rendered[text_key] = (
                _REDACTED
                if _is_sensitive_key(text_key)
                else _safe_context_value(item, depth=depth + 1)
            )
        return rendered

    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = list(value)
        rendered_sequence = [
            _safe_context_value(item, depth=depth + 1)
            for item in sequence[:_MAX_CONTEXT_ITEMS]
        ]
        if len(sequence) > _MAX_CONTEXT_ITEMS:
            rendered_sequence.append("<truncated>")
        return rendered_sequence

    return f"<{type(value).__name__}>"


def sanitize_engine_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Normalize engine diagnostic context without exposing evidence content."""
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise TypeError(
            "Engine error context must be a mapping or None, "
            f"got {type(context).__name__}."
        )

    safe: dict[str, Any] = {}
    for key, value in context.items():
        text_key = str(key)
        safe[text_key] = (
            _REDACTED
            if _is_sensitive_key(text_key)
            else _safe_context_value(value)
        )
    return safe


class EngineError(Exception):
    """Base exception for all deterministic BIMAP audit-engine failures."""

    code = "BIMAP.ENGINE.ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        operation: str | None = None,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        self.message = normalized_message
        self.component = str(component).strip() if component is not None else None
        self.operation = str(operation).strip() if operation is not None else None
        self.field = str(field).strip() if field is not None else None
        self.context = sanitize_engine_context(context)
        self.cause = cause

        qualifiers: list[str] = []
        if self.component:
            qualifiers.append(f"component={self.component}")
        if self.operation:
            qualifiers.append(f"operation={self.operation}")
        if self.field:
            qualifiers.append(f"field={self.field}")

        rendered = normalized_message
        if qualifiers:
            rendered = f"{rendered} [{', '.join(qualifiers)}]"
        super().__init__(rendered)

    def announce(
        self,
        *,
        label: str = "AUDIT ENGINE",
        level: str = "error",
    ) -> None:
        """Explicitly emit one operator-facing status for a handled failure."""
        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "audit_engine_error_announced",
                "code": self.code,
                "type": self.__class__.__name__,
                "component": self.component,
                "operation": self.operation,
                "field": self.field,
                "retryable": bool(self.retryable),
            }
        )

    def to_dict(
        self,
        *,
        include_context: bool = True,
        include_cause_type: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic, logging-safe error representation."""
        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.component:
            payload["component"] = self.component
        if self.operation:
            payload["operation"] = self.operation
        if self.field:
            payload["field"] = self.field
        if include_context and self.context:
            payload["context"] = dict(self.context)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__
        return payload


# ---------------------------------------------------------------------------
# Cross-engine validation/configuration primitives
# ---------------------------------------------------------------------------


class EngineConfigurationError(EngineError):
    """Raised when an audit-engine component is configured inconsistently."""

    code = "BIMAP.ENGINE.CONFIGURATION"


class EngineValidationError(EngineError):
    """Raised when engine input violates an engine-level precondition."""

    code = "BIMAP.ENGINE.VALIDATION"


class EngineIntegrityError(EngineError):
    """Raised when accepted engine state is internally contradictory."""

    code = "BIMAP.ENGINE.INTEGRITY"


class EngineSerializationError(EngineError):
    """Raised when engine metadata cannot be represented deterministically."""

    code = "BIMAP.ENGINE.SERIALIZATION"


class UnsupportedEngineInputError(EngineValidationError):
    """Raised when a component receives an object type it does not support."""

    code = "BIMAP.ENGINE.INPUT.UNSUPPORTED"


# ---------------------------------------------------------------------------
# Ingestion boundary
# ---------------------------------------------------------------------------


class IngestionError(EngineError):
    """Base class for evidence-package ingestion failures."""

    code = "BIMAP.ENGINE.INGESTION"


class IngestionValidationError(IngestionError):
    """Raised when an evidence package is structurally invalid for ingestion."""

    code = "BIMAP.ENGINE.INGESTION.VALIDATION"


class IngestionDeserializationError(IngestionError):
    """Raised when serialized ingestion input cannot be decoded as JSON data."""

    code = "BIMAP.ENGINE.INGESTION.DESERIALIZATION"


class UnsupportedIngestionTypeError(IngestionValidationError):
    """Raised for unsupported evidence-package or payload types."""

    code = "BIMAP.ENGINE.INGESTION.TYPE.UNSUPPORTED"


class DeclaredIngestionTypeMismatchError(IngestionValidationError):
    """Raised when a caller's declared type conflicts with a typed payload."""

    code = "BIMAP.ENGINE.INGESTION.TYPE.MISMATCH"


# ---------------------------------------------------------------------------
# Analytical evidence manifest
# ---------------------------------------------------------------------------


class ManifestError(IngestionError):
    """Base class for analytical evidence-manifest failures."""

    code = "BIMAP.ENGINE.INGESTION.MANIFEST"


class ManifestValidationError(ManifestError):
    """Raised when a manifest cannot be derived from accepted evidence."""

    code = "BIMAP.ENGINE.INGESTION.MANIFEST.VALIDATION"


class ManifestIntegrityError(ManifestError):
    """Raised when evidence provenance conflicts inside one package."""

    code = "BIMAP.ENGINE.INGESTION.MANIFEST.INTEGRITY"


class ManifestSourceConflictError(ManifestIntegrityError):
    """Raised when one source_file_id resolves to inconsistent provenance."""

    code = "BIMAP.ENGINE.INGESTION.MANIFEST.SOURCE_CONFLICT"


# ---------------------------------------------------------------------------
# Project-evidence importer
# ---------------------------------------------------------------------------


class ProjectEvidenceIngestionError(IngestionError):
    """Raised when a Project Evidence contract cannot be accepted for analysis."""

    code = "BIMAP.ENGINE.INGESTION.PROJECT_EVIDENCE"


__all__ = [
    "sanitize_engine_context",
    "EngineError",
    "EngineConfigurationError",
    "EngineValidationError",
    "EngineIntegrityError",
    "EngineSerializationError",
    "UnsupportedEngineInputError",
    "IngestionError",
    "IngestionValidationError",
    "IngestionDeserializationError",
    "UnsupportedIngestionTypeError",
    "DeclaredIngestionTypeMismatchError",
    "ManifestError",
    "ManifestValidationError",
    "ManifestIntegrityError",
    "ManifestSourceConflictError",
    "ProjectEvidenceIngestionError",
]


if __name__ == "__main__":
    print("\n=== Running Audit Engine Errors Self-Test ===\n")
    printer.status("TEST", "Audit engine error hierarchy initialized", "info")

    sample = ManifestSourceConflictError(
        "Conflicting source provenance.",
        component="manifest",
        operation="validate",
        field="source_file_id",
        context={
            "source_file_id": "SRC-1",
            "payload": "must-not-leak",
            "evidence_value": "must-not-leak-either",
        },
        cause=ValueError("private detail"),
    )
    rendered = sample.to_dict()
    assert rendered["code"] == "BIMAP.ENGINE.INGESTION.MANIFEST.SOURCE_CONFLICT"
    assert rendered["context"]["payload"] == _REDACTED
    assert rendered["context"]["evidence_value"] == _REDACTED
    assert "must-not-leak" not in str(rendered)
    assert rendered["cause_type"] == "ValueError"
    printer.status("PASS", "Structured engine error redaction", "success")

    print("\n=== Test ran successfully ===\n")