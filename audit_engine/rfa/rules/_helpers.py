"""Shared deterministic helpers for BIMAP Revit Family Audit semantic rules."""

from __future__ import annotations

import re

from collections.abc import Mapping, Sequence
from typing import Any

from ...context import AuditContext
from ...utils.engine_helpers import to_engine_primitive
from ....domain.evidence.models import EvidenceItem


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")


def canonical_key(value: Any) -> str:
    """Normalize an identifier/name for duplicate and lookup comparisons."""
    if value is None:
        return ""
    text = str(value).strip().casefold()
    return _NON_ALNUM.sub("", text)


def canonical_name(value: Any) -> str:
    """Normalize a semantic Revit name without erasing meaningful punctuation."""
    if value is None:
        return ""
    return _SPACE.sub(" ", str(value).strip().casefold())


def normalized_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def normalized_expression(value: Any) -> str | None:
    text = normalized_text(value)
    if text is None:
        return None
    return _SPACE.sub(" ", text).strip().casefold()


def primitive_mapping(item: EvidenceItem) -> dict[str, Any] | None:
    primitive = to_engine_primitive(
        item.extracted_value,
        field=f"evidence.{item.evidence_id}.extracted_value",
    )
    return primitive if isinstance(primitive, dict) else None


def refs(items: Sequence[EvidenceItem]) -> tuple[str, ...]:
    return tuple(item.evidence_id for item in items)


def group_items(context: AuditContext, name: str) -> tuple[EvidenceItem, ...]:
    ids = context.evidence_groups.get(name)
    if ids is None:
        return ()
    index = {item.evidence_id: item for item in context.evidence_items}
    return tuple(index[item_id] for item_id in ids if item_id in index)


def group_mappings(
    context: AuditContext,
    name: str,
) -> tuple[tuple[EvidenceItem, dict[str, Any]], ...]:
    result: list[tuple[EvidenceItem, dict[str, Any]]] = []
    for item in group_items(context, name):
        payload = primitive_mapping(item)
        if payload is not None:
            result.append((item, payload))
    return tuple(result)


def value_for(
    mapping: Mapping[str, Any],
    *names: str,
) -> Any:
    """Resolve a field by tolerant canonical-key matching."""
    wanted = {canonical_key(name) for name in names}
    for key, value in mapping.items():
        if canonical_key(key) in wanted:
            return value
    return None


def text_for(mapping: Mapping[str, Any], *names: str) -> str | None:
    value = value_for(mapping, *names)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def bool_for(mapping: Mapping[str, Any], *names: str) -> bool | None:
    value = value_for(mapping, *names)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "1", "type"}:
            return True
        if normalized in {"false", "no", "0", "instance"}:
            return False
    return None


def number_for(mapping: Mapping[str, Any], *names: str) -> float | None:
    value = value_for(mapping, *names)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def mapping_for(mapping: Mapping[str, Any], *names: str) -> Mapping[str, Any] | None:
    value = value_for(mapping, *names)
    return value if isinstance(value, Mapping) else None


def sequence_for(mapping: Mapping[str, Any], *names: str) -> tuple[Any, ...] | None:
    value = value_for(mapping, *names)
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        return None
    try:
        return tuple(value)
    except TypeError:
        return None


def family_identity_payload(context: AuditContext) -> tuple[EvidenceItem | None, dict[str, Any]]:
    """Return the first canonical family identity record and its family payload."""
    records = group_mappings(context, "family_identity")
    if not records:
        return None, {}

    item, payload = records[0]
    family = payload.get("family")
    if isinstance(family, Mapping):
        return item, dict(family)

    # Backwards-compatible fallback for older native extraction shapes.
    return item, dict(payload)


def extraction_metadata(context: AuditContext) -> Mapping[str, Any]:
    records = group_mappings(context, "family_identity")
    if not records:
        return {}
    payload = records[0][1]
    value = payload.get("extraction")
    return value if isinstance(value, Mapping) else {}


def section_assessed(context: AuditContext, section_name: str) -> bool | None:
    """
    Return whether the native worker explicitly states that a semantic section
    was assessed. ``None`` means the extraction did not publish that metadata.
    """
    metadata = extraction_metadata(context)
    raw = metadata.get("sections_assessed")
    if raw is None or isinstance(raw, (str, bytes, bytearray, Mapping)):
        return None
    try:
        values = tuple(raw)
    except TypeError:
        return None
    target = canonical_key(section_name)
    return any(canonical_key(value) == target for value in values)


def family_name(context: AuditContext) -> str | None:
    _, payload = family_identity_payload(context)
    return text_for(payload, "family_name", "familyName", "name")


def family_category(context: AuditContext) -> str | None:
    _, payload = family_identity_payload(context)
    return text_for(payload, "category", "category_name", "family_category")


def type_name(record: Mapping[str, Any]) -> str | None:
    return text_for(record, "type_name", "typeName", "name", "family_type")


def parameter_name(record: Mapping[str, Any]) -> str | None:
    return text_for(record, "parameter_name", "parameterName", "name")


def parameter_scope(record: Mapping[str, Any]) -> str | None:
    scope = text_for(record, "scope", "parameter_scope")
    if scope:
        normalized = scope.casefold().replace("_", "-")
        if normalized in {"type", "family-type", "type-parameter"}:
            return "type"
        if normalized in {"instance", "instance-parameter"}:
            return "instance"

    is_instance = bool_for(record, "is_instance", "isInstance", "instance")
    if is_instance is not None:
        return "instance" if is_instance else "type"
    return None


def parameter_data_type(record: Mapping[str, Any]) -> str | None:
    return text_for(
        record,
        "data_type",
        "dataType",
        "spec_type",
        "specType",
        "parameter_type",
        "parameterType",
        "storage_type",
        "storageType",
    )


def parameter_shared(record: Mapping[str, Any]) -> bool | None:
    return bool_for(record, "shared", "is_shared", "isShared")


def formula_parameter(record: Mapping[str, Any]) -> str | None:
    return text_for(
        record,
        "parameter",
        "parameter_name",
        "parameterName",
        "name",
    )


def formula_expression(record: Mapping[str, Any]) -> str | None:
    return text_for(record, "formula", "expression", "formula_text", "formulaText")


def formula_references(record: Mapping[str, Any]) -> tuple[str, ...] | None:
    raw = sequence_for(record, "references", "parameter_references", "dependencies")
    if raw is None:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if isinstance(value, Mapping):
            text = text_for(value, "name", "parameter", "parameter_name")
        elif isinstance(value, str):
            text = value.strip() or None
        else:
            text = None
        if text is None:
            continue
        key = canonical_name(text)
        if key and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def metric_name(record: Mapping[str, Any]) -> str | None:
    return text_for(record, "metric", "metric_name", "name", "key")


def metric_value(record: Mapping[str, Any]) -> float | None:
    return number_for(record, "value", "metric_value", "count", "total")


__all__ = [
    "canonical_key",
    "canonical_name",
    "normalized_text",
    "normalized_expression",
    "primitive_mapping",
    "refs",
    "group_items",
    "group_mappings",
    "value_for",
    "text_for",
    "bool_for",
    "number_for",
    "mapping_for",
    "sequence_for",
    "family_identity_payload",
    "extraction_metadata",
    "section_assessed",
    "family_name",
    "family_category",
    "type_name",
    "parameter_name",
    "parameter_scope",
    "parameter_data_type",
    "parameter_shared",
    "formula_parameter",
    "formula_expression",
    "formula_references",
    "metric_name",
    "metric_value",
]
