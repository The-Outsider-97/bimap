"""
Shared deterministic helpers for BIMAP's Level-4 audit engine.

These helpers provide the small cross-cutting mechanics needed by ingestion and
later deterministic engine modules without recreating lower-level contract or
domain policy.  Canonical JSON decoding/primitive conversion is delegated to
``bimap.contracts.utils.contracts_helpers``; contract DTOs remain authoritative
for external schema validation.

The module intentionally does not open files, inspect upload signatures, scan
malware, resolve object-storage URIs, load YAML, or import the SLAI integration
package.  Those concerns belong to outer adapters/bootstrap rather than the
deterministic audit engine.
"""

from __future__ import annotations

import re

from collections.abc import Mapping
from enum import Enum
from typing import Any

from ...contracts.utils.contracts_errors import ContractError
from ...contracts.utils.contracts_helpers import *
from ..utils.engine_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Engine Helpers")
printer = PrettyPrinter()

_CANONICAL_KIND_RE = re.compile(r"_+")


class IngestionKind(str, Enum):
    """Canonical evidence-package types accepted by the ingestion dispatcher."""

    FAMILY_EVIDENCE = "family_evidence"
    PROJECT_EVIDENCE = "project_evidence"


def announce_engine_action(
    target_printer: PrettyPrinter,
    target_logger: Any,
    *,
    component: str,
    action: str,
    event: str,
    context: Mapping[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Emit the standard method-start diagnostic without evidence content."""
    safe_context = sanitize_engine_context(context)
    target_printer.status("AUDIT ENGINE", action, level)

    payload: dict[str, Any] = {
        "event": str(event),
        "component": str(component),
        "action": str(action),
    }
    if safe_context:
        payload["context"] = safe_context
    target_logger.debug(payload)


def lower_error_context(error: BaseException) -> dict[str, Any]:
    """Return safe metadata for an exception raised by a lower layer."""
    context: dict[str, Any] = {"lower_error_type": type(error).__name__}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.strip():
        context["lower_error_code"] = code.strip()
    return context


def require_engine_mapping(
    value: Any,
    *,
    field: str,
    error_type: type[EngineError] = IngestionValidationError,
) -> dict[str, Any]:
    """Require a string-keyed mapping and return an isolated shallow copy."""
    if not isinstance(value, Mapping):
        raise error_type(
            f"{field} must be a mapping.",
            component="engine_helpers",
            operation="require_engine_mapping",
            field=field,
            context={"received_type": type(value).__name__},
        )

    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise error_type(
                f"{field} keys must be non-empty strings.",
                component="engine_helpers",
                operation="require_engine_mapping",
                field=field,
                context={"received_key_type": type(key).__name__},
            )
        normalized_key = key.strip()
        if normalized_key in result:
            raise error_type(
                f"{field} contains keys that collide after whitespace normalization.",
                component="engine_helpers",
                operation="require_engine_mapping",
                field=field,
                context={"key": normalized_key},
            )
        result[normalized_key] = item
    return result


def require_engine_text(
    value: Any,
    *,
    field: str,
    error_type: type[EngineError] = IngestionValidationError,
) -> str:
    """Require non-empty text without coercing arbitrary Python objects."""
    if not isinstance(value, str):
        raise error_type(
            f"{field} must be a string.",
            component="engine_helpers",
            operation="require_engine_text",
            field=field,
            context={"received_type": type(value).__name__},
        )
    normalized = value.strip()
    if not normalized:
        raise error_type(
            f"{field} must not be empty.",
            component="engine_helpers",
            operation="require_engine_text",
            field=field,
        )
    return normalized


def normalize_ingestion_kind(
    value: IngestionKind | str | None,
    *,
    field: str = "declared_type",
    allow_none: bool = True,
) -> IngestionKind | None:
    """Normalize a caller-declared canonical ingestion type."""
    if value is None:
        if allow_none:
            return None
        raise UnsupportedIngestionTypeError(
            f"{field} must declare an ingestion type.",
            component="engine_helpers",
            operation="normalize_ingestion_kind",
            field=field,
        )

    if isinstance(value, IngestionKind):
        return value

    text = require_engine_text(
        value,
        field=field,
        error_type=UnsupportedIngestionTypeError,
    )
    normalized = text.casefold().replace("-", "_").replace(" ", "_")
    normalized = _CANONICAL_KIND_RE.sub("_", normalized)

    try:
        return IngestionKind(normalized)
    except ValueError as exc:
        raise UnsupportedIngestionTypeError(
            "Unsupported ingestion type.",
            component="engine_helpers",
            operation="normalize_ingestion_kind",
            field=field,
            context={
                "received": normalized,
                "supported": tuple(kind.value for kind in IngestionKind),
            },
            cause=exc,
        ) from exc


def decode_json_object(
    payload: str | bytes | bytearray,
    *,
    field: str = "payload",
) -> dict[str, Any]:
    """Decode canonical JSON once and require an object-shaped root value."""
    if not isinstance(payload, (str, bytes, bytearray)):
        raise UnsupportedIngestionTypeError(
            "Serialized ingestion input must be str, bytes, or bytearray.",
            component="engine_helpers",
            operation="decode_json_object",
            field=field,
            context={"received_type": type(payload).__name__},
        )

    try:
        decoded = canonical_json_loads(payload)
    except ContractError as exc:
        raise IngestionDeserializationError(
            "Ingestion payload is not valid JSON.",
            component="engine_helpers",
            operation="decode_json_object",
            field=field,
            context=lower_error_context(exc),
            cause=exc,
        ) from exc

    if not isinstance(decoded, Mapping):
        raise IngestionDeserializationError(
            "Ingestion JSON root must be an object.",
            component="engine_helpers",
            operation="decode_json_object",
            field=field,
            context={"received_type": type(decoded).__name__},
        )
    return require_engine_mapping(decoded, field=field)


def to_engine_primitive(value: Any, *, field: str) -> Any:
    """Delegate deterministic JSON primitive conversion to the contracts layer."""
    try:
        return to_json_primitive(value, field=field)
    except ContractError as exc:
        raise EngineSerializationError(
            "Audit-engine metadata cannot be represented deterministically.",
            component="engine_helpers",
            operation="to_engine_primitive",
            field=field,
            context=lower_error_context(exc),
            cause=exc,
        ) from exc


def infer_ingestion_kind(payload: Mapping[str, Any]) -> IngestionKind:
    """
    Infer the canonical package type from the existing contract discriminator.

    ``ProjectEvidence`` requires ``project_id`` while ``FamilyEvidence`` does
    not define that field.  No section-name heuristics or product-scope aliases
    are introduced here.  A caller that already knows the type should pass an
    explicit declaration to the dispatcher.
    """
    mapping = require_engine_mapping(payload, field="payload")
    if "project_id" in mapping:
        return IngestionKind.PROJECT_EVIDENCE
    return IngestionKind.FAMILY_EVIDENCE


__all__ = [
    "IngestionKind",
    "announce_engine_action",
    "lower_error_context",
    "require_engine_mapping",
    "require_engine_text",
    "normalize_ingestion_kind",
    "decode_json_object",
    "to_engine_primitive",
    "infer_ingestion_kind",
]


if __name__ == "__main__":
    print("\n=== Running Audit Engine Helpers Self-Test ===\n")
    printer.status("TEST", "Audit engine helpers initialized", "info")

    assert normalize_ingestion_kind("project-evidence") is IngestionKind.PROJECT_EVIDENCE
    assert infer_ingestion_kind({"schema_version": "1.0.0", "project_id": "P-1"}) is IngestionKind.PROJECT_EVIDENCE
    assert infer_ingestion_kind({"schema_version": "1.0.0"}) is IngestionKind.FAMILY_EVIDENCE
    assert decode_json_object('{"project_id":"P-1"}') == {"project_id": "P-1"}
    printer.status("PASS", "Ingestion helper normalization", "success")

    print("\n=== Test ran successfully ===\n")