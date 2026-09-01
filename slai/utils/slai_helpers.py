"""
Shared helpers for BIMAP's SLAI integration package.

The helpers in this module are intentionally small, deterministic primitives used
by ``agent_policy.py``, ``job_envelope.py``, ``health.py``, ``governance.py``,
and later adapter/orchestration modules.  They do not import those higher-level
modules and therefore remain safe at the bottom of the integration dependency
graph.

Design principles
-----------------
- validate instead of silently coercing ambiguous data;
- preserve stable insertion order when normalizing collections;
- reject arbitrary Python objects at the SLAI envelope boundary;
- canonicalize JSON before hashing so integrity checks are reproducible;
- never log raw customer evidence from helper diagnostics;
- keep configuration loading outside this module.  ``bootstrap.py`` remains the
  owner of YAML/environment loading and passes normalized mappings to the SLAI
  integration layer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import uuid

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar

from .slai_errors import *


TEnum = TypeVar("TEnum", bound=Enum)
_AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_LOG_CONTEXT_STRING = 160
_MAX_LOG_CONTEXT_ITEMS = 24


def announce_method_start(
    printer: Any,
    logger: Any,
    label: str,
    action: str,
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Emit the standard BIMAP/SLAI method-start status and safe debug event."""

    printer.status(str(label), str(action), "info")
    event: dict[str, Any] = {
        "event": "bimap_slai_method_start",
        "label": str(label),
        "action": str(action),
    }
    if context:
        event["context"] = safe_log_context(context)
    logger.debug(event)


def safe_log_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded metadata suitable for diagnostics, never raw payloads."""

    sensitive_fragments = (
        "authorization",
        "cookie",
        "credential",
        "document",
        "evidence_content",
        "evidence_value",
        "password",
        "payload",
        "raw",
        "secret",
        "token",
    )

    def render(key: str, value: Any, depth: int = 0) -> Any:
        if any(fragment in key.casefold() for fragment in sensitive_fragments):
            return "<redacted>"
        if depth >= 2:
            return f"<{type(value).__name__}>"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= _MAX_LOG_CONTEXT_STRING:
                return value
            return f"{value[:_MAX_LOG_CONTEXT_STRING]}…"
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for index, (child_key, child_value) in enumerate(value.items()):
                if index >= _MAX_LOG_CONTEXT_ITEMS:
                    result["__truncated__"] = True
                    break
                text_key = str(child_key)
                result[text_key] = render(text_key, child_value, depth + 1)
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            rendered = [
                render("item", item, depth + 1)
                for item in list(value)[:_MAX_LOG_CONTEXT_ITEMS]
            ]
            if len(value) > _MAX_LOG_CONTEXT_ITEMS:
                rendered.append("<truncated>")
            return rendered
        return f"<{type(value).__name__}>"

    return {str(key): render(str(key), value) for key, value in context.items()}


def require_mapping(
    value: Any,
    *,
    field: str,
    error_type: type[SLAIIntegrationError] = SLAIEnvelopeValidationError,
) -> dict[str, Any]:
    """Require a mapping and return a shallow string-keyed copy."""

    if not isinstance(value, Mapping):
        raise error_type(
            f"{field} must be a mapping.",
            field=field,
            context={"received_type": type(value).__name__},
        )

    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise error_type(
                f"{field} keys must be non-empty strings.",
                field=field,
                context={"received_key_type": type(key).__name__},
            )
        result[key.strip()] = item
    return result


def require_text(
    value: Any,
    *,
    field: str,
    error_type: type[SLAIIntegrationError] = SLAIEnvelopeValidationError,
) -> str:
    """Require a non-empty string without coercing arbitrary values."""

    if not isinstance(value, str):
        raise error_type(
            f"{field} must be a string.",
            field=field,
            context={"received_type": type(value).__name__},
        )
    normalized = value.strip()
    if not normalized:
        raise error_type(f"{field} must not be empty.", field=field)
    return normalized


def optional_text(
    value: Any,
    *,
    field: str,
    error_type: type[SLAIIntegrationError] = SLAIEnvelopeValidationError,
) -> str | None:
    """Normalize an optional string while preserving ``None``."""

    if value is None:
        return None
    return require_text(value, field=field, error_type=error_type)


def require_bool(
    value: Any,
    *,
    field: str,
    error_type: type[SLAIIntegrationError] = SLAIPolicyValidationError,
) -> bool:
    """Require a real boolean instead of truthiness-based coercion."""

    if not isinstance(value, bool):
        raise error_type(
            f"{field} must be a boolean.",
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


def normalize_text_sequence(
    values: Iterable[Any] | None,
    *,
    field: str,
    allow_empty: bool = True,
    error_type: type[SLAIIntegrationError] = SLAIEnvelopeValidationError,
) -> tuple[str, ...]:
    """Normalize a stable, duplicate-free sequence of non-empty text values."""

    if values is None:
        result: tuple[str, ...] = ()
    else:
        if isinstance(values, (str, bytes, bytearray)):
            raise error_type(
                f"{field} must be a sequence of strings, not one string.",
                field=field,
                context={"received_type": type(values).__name__},
            )
        try:
            iterator = iter(values)
        except TypeError as exc:
            raise error_type(
                f"{field} must be an iterable of text values.",
                field=field,
                context={"received_type": type(values).__name__},
                cause=exc,
            ) from exc

        normalized: list[str] = []
        seen: set[str] = set()
        for index, value in enumerate(iterator):
            text = require_text(
                value,
                field=f"{field}[{index}]",
                error_type=error_type,
            )
            if text not in seen:
                seen.add(text)
                normalized.append(text)
        result = tuple(normalized)

    if not allow_empty and not result:
        raise error_type(
            f"{field} must contain at least one value.",
            field=field,
        )
    return result


def normalize_agent_name(
    value: Any,
    *,
    field: str = "agent",
    error_type: type[SLAIIntegrationError] = SLAIPolicyValidationError,
) -> str:
    """Normalize a BIMAP/SLAI agent identifier to the factory-style key form."""

    text = require_text(value, field=field, error_type=error_type)
    normalized = text.casefold().replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized)
    if not _AGENT_NAME_RE.fullmatch(normalized):
        raise error_type(
            "Agent name contains unsupported characters.",
            component="agent_policy",
            operation="normalize_agent_name",
            field=field,
            context={"agent": normalized},
        )
    return normalized


def normalize_agent_sequence(
    values: Iterable[Any] | None,
    *,
    field: str = "agents",
    error_type: type[SLAIIntegrationError] = SLAIPolicyValidationError,
) -> tuple[str, ...]:
    """Return stable unique normalized agent names."""

    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray)):
        raise error_type(
            f"{field} must be a sequence of agent names, not one string.",
            component="agent_policy",
            operation="normalize_agent_sequence",
            field=field,
        )

    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values):
        normalized = normalize_agent_name(
            value,
            field=f"{field}[{index}]",
            error_type=error_type,
        )
        if normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


def parse_enum(
    enum_type: type[TEnum],
    value: Any,
    *,
    field: str,
    aliases: Mapping[str, str] | None = None,
    error_type: type[SLAIIntegrationError] = SLAIPolicyValidationError,
) -> TEnum:
    """Parse a string enum with optional explicit aliases."""

    if isinstance(value, enum_type):
        return value
    text = require_text(value, field=field, error_type=error_type)
    normalized = text.casefold().replace("-", "_").replace(" ", "_")
    if aliases:
        normalized = aliases.get(normalized, normalized)
    for member in enum_type:
        if str(member.value).casefold() == normalized:
            return member
    raise error_type(
        f"Unsupported value for {field}.",
        field=field,
        context={
            "received": normalized,
            "allowed": [str(member.value) for member in enum_type],
        },
    )


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def ensure_utc_datetime(
    value: datetime | str,
    *,
    field: str,
    error_type: type[SLAIIntegrationError] = SLAIEnvelopeValidationError,
) -> datetime:
    """Normalize an ISO-8601 string or aware datetime to UTC."""

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise error_type(f"{field} must not be empty.", field=field)
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise error_type(
                f"{field} must be a valid ISO-8601 datetime.",
                field=field,
                cause=exc,
            ) from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise error_type(
            f"{field} must be a datetime or ISO-8601 string.",
            field=field,
            context={"received_type": type(value).__name__},
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise error_type(
            f"{field} must be timezone-aware.",
            field=field,
        )
    return parsed.astimezone(timezone.utc)


def format_utc_datetime(value: datetime) -> str:
    """Return canonical UTC ISO-8601 text with ``Z`` suffix."""

    normalized = ensure_utc_datetime(value, field="datetime")
    return normalized.isoformat().replace("+00:00", "Z")


def generate_identifier(prefix: str) -> str:
    """Generate an opaque, non-sequential identifier with a stable prefix."""

    normalized_prefix = str(prefix).strip().upper().replace(" ", "-")
    if not normalized_prefix:
        raise ValueError("identifier prefix must not be empty")
    return f"{normalized_prefix}-{uuid.uuid4().hex}"


def _json_safe_value(
    value: Any,
    *,
    field: str,
    depth: int,
    max_depth: int,
) -> Any:
    if depth > max_depth:
        raise SLAIEnvelopeSerializationError(
            "Grounded SLAI context exceeds the allowed nesting depth.",
            component="job_envelope",
            operation="normalize_context",
            field=field,
            context={"max_depth": max_depth},
        )

    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SLAIEnvelopeSerializationError(
                "Grounded SLAI context contains a non-finite float.",
                component="job_envelope",
                operation="normalize_context",
                field=field,
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not key.strip():
                raise SLAIEnvelopeSerializationError(
                    "Grounded SLAI context mappings require non-empty string keys.",
                    component="job_envelope",
                    operation="normalize_context",
                    field=field,
                    context={"received_key_type": type(key).__name__},
                )
            key_text = key.strip()
            result[key_text] = _json_safe_value(
                child,
                field=f"{field}.{key_text}",
                depth=depth + 1,
                max_depth=max_depth,
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _json_safe_value(
                child,
                field=f"{field}[{index}]",
                depth=depth + 1,
                max_depth=max_depth,
            )
            for index, child in enumerate(value)
        ]

    raise SLAIEnvelopeSerializationError(
        "Grounded SLAI context contains a non-JSON-safe runtime object.",
        component="job_envelope",
        operation="normalize_context",
        field=field,
        context={"received_type": type(value).__name__},
    )


def normalize_json_mapping(
    payload: Mapping[str, Any],
    *,
    field: str = "grounded_context",
    max_depth: int = 16,
) -> dict[str, Any]:
    """Validate and deep-copy a JSON-safe mapping for the SLAI boundary."""

    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    mapping = require_mapping(payload, field=field)
    normalized = _json_safe_value(
        mapping,
        field=field,
        depth=0,
        max_depth=max_depth,
    )
    assert isinstance(normalized, dict)
    return normalized


def freeze_json_value(value: Any) -> Any:
    """Recursively freeze a normalized JSON value."""

    if isinstance(value, dict):
        return MappingProxyType({key: freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze_json_value(item) for item in value)
    return value


def thaw_json_value(value: Any) -> Any:
    """Convert a frozen JSON value back into ordinary JSON containers."""

    if isinstance(value, Mapping):
        return {str(key): thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


def canonical_json_dumps(value: Any, *, pretty: bool = False) -> str:
    """Encode a JSON-safe value deterministically."""

    try:
        if pretty:
            return json.dumps(
                thaw_json_value(value),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
        return json.dumps(
            thaw_json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SLAIEnvelopeSerializationError(
            "Unable to encode SLAI envelope data as canonical JSON.",
            component="job_envelope",
            operation="serialize",
            cause=exc,
        ) from exc


def canonical_json_loads(payload: str | bytes | bytearray) -> Any:
    """Decode JSON for the SLAI integration boundary."""

    if not isinstance(payload, (str, bytes, bytearray)):
        raise SLAIEnvelopeSerializationError(
            "JSON payload must be str, bytes, or bytearray.",
            component="job_envelope",
            operation="deserialize",
            context={"received_type": type(payload).__name__},
        )
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SLAIEnvelopeSerializationError(
            "Unable to decode SLAI envelope JSON.",
            component="job_envelope",
            operation="deserialize",
            cause=exc,
        ) from exc


def stable_payload_digest(value: Any) -> str:
    """Return SHA-256 over canonical JSON for integrity/provenance checks."""

    encoded = canonical_json_dumps(value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def digests_equal(left: str, right: str) -> bool:
    """Compare two digest strings using constant-time comparison."""

    return hmac.compare_digest(str(left), str(right))


def payload_size_bytes(value: Any) -> int:
    """Return the UTF-8 byte size of a canonical JSON representation."""

    return len(canonical_json_dumps(value).encode("utf-8"))


def normalize_probability(
    value: Any,
    *,
    field: str,
    error_type: type[SLAIIntegrationError],
) -> float:
    """Normalize a finite probability in the inclusive range [0, 1]."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_type(
            f"{field} must be numeric.",
            field=field,
            context={"received_type": type(value).__name__},
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise error_type(
            f"{field} must be between 0.0 and 1.0 inclusive.",
            field=field,
            context={"received": result},
        )
    return result


def normalize_decision_token(value: Any) -> str | None:
    """Normalize a native SLAI decision/status token, returning ``None`` if absent."""

    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    return re.sub(r"_+", "_", normalized) or None


def first_present(mapping: Mapping[str, Any], keys: Sequence[str]) -> tuple[str | None, Any]:
    """Return the first key/value pair that is explicitly present in a mapping."""

    for key in keys:
        if key in mapping:
            return key, mapping[key]
    return None, None


def normalize_health_token(value: Any) -> str:
    """Map common SLAI health/lifecycle tokens to a small neutral vocabulary."""

    if isinstance(value, bool):
        return "healthy" if value else "unavailable"
    token = normalize_decision_token(value)
    if token is None:
        return "unknown"

    if token in {"healthy", "ok", "ready", "active", "running", "normal", "pass", "good"}:
        return "healthy"
    if token in {"degraded", "warn", "warning", "unknown", "initializing", "idle"}:
        return "degraded" if token != "unknown" else "unknown"
    if token in {"unavailable", "failed", "failure", "error", "stopped", "critical", "block", "blocked"}:
        return "unavailable"
    return "unknown"


def extract_health_token(payload: Any) -> str:
    """Extract a neutral health token from common SLAI health-report shapes."""

    if isinstance(payload, bool) or isinstance(payload, str):
        return normalize_health_token(payload)
    if not isinstance(payload, Mapping):
        return "unknown"

    if "healthy" in payload and isinstance(payload["healthy"], bool):
        return normalize_health_token(payload["healthy"])

    for key in ("health", "status", "operational_state", "state"):
        if key in payload:
            normalized = normalize_health_token(payload[key])
            if normalized != "unknown":
                return normalized
    return "unknown"


__all__ = [
    "announce_method_start",
    "safe_log_context",
    "require_mapping",
    "require_text",
    "optional_text",
    "require_bool",
    "normalize_text_sequence",
    "normalize_agent_name",
    "normalize_agent_sequence",
    "parse_enum",
    "utc_now",
    "ensure_utc_datetime",
    "format_utc_datetime",
    "generate_identifier",
    "normalize_json_mapping",
    "freeze_json_value",
    "thaw_json_value",
    "canonical_json_dumps",
    "canonical_json_loads",
    "stable_payload_digest",
    "digests_equal",
    "payload_size_bytes",
    "normalize_probability",
    "normalize_decision_token",
    "first_present",
    "normalize_health_token",
    "extract_health_token",
]