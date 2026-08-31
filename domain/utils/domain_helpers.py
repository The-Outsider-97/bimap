"""
Shared BIMAP domain helpers.

These helpers provide deterministic normalization and validation primitives
used by domain value objects and aggregates.

Dependency rule
---------------
domain_helpers.py may import domain_errors.py.

domain_errors.py must never import domain_helpers.py.

Neither module imports concrete domain models, preserving a one-directional
dependency graph.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import unicodedata

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from .domain_errors import (
    DomainSerializationError,
    DomainValidationError,
)



def require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must be a string, got {type(value).__name__}."
        )
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty.")
    return stripped


def optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must be a string or None, got {type(value).__name__}."
        )
    stripped = value.strip()
    return stripped or None


def require_mapping(value: Any, *, field_name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(
            f"{field_name} must be a mapping, got {type(value).__name__}."
        )
    return value


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    """
    Return the current time as a timezone-aware UTC datetime.
    """

    return datetime.now(timezone.utc)


def ensure_utc_datetime(
    value: datetime | str,
    *,
    field: str,
) -> datetime:
    """
    Normalize a timezone-aware datetime or ISO-8601 value to UTC.

    Naive datetimes are rejected rather than silently interpreted as UTC.
    Provenance timestamps must remain unambiguous for reproducibility and
    auditability.
    """

    parsed: datetime

    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        raw = value.strip()

        if not raw:
            raise DomainValidationError(
                "Timestamp must not be empty.",
                field=field,
            )

        if raw.endswith(("Z", "z")):
            raw = f"{raw[:-1]}+00:00"

        try:
            parsed = datetime.fromisoformat(raw)

        except ValueError as exc:
            raise DomainValidationError(
                "Timestamp must be a valid ISO-8601 datetime.",
                field=field,
            ) from exc

    else:
        raise DomainValidationError(
            "Timestamp must be a datetime or ISO-8601 string.",
            field=field,
            context={
                "received_type": type(value).__name__,
            },
        )

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DomainValidationError(
            "Timestamp must include timezone information.",
            field=field,
        )

    return parsed.astimezone(timezone.utc)


def format_utc_datetime(value: datetime) -> str:
    """
    Format an aware datetime as canonical ISO-8601 UTC ending in ``Z``.
    """

    normalized = ensure_utc_datetime(
        value,
        field="datetime",
    )

    return normalized.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _contains_disallowed_control_character(value: str) -> bool:
    """
    Return True when text contains unsafe/non-semantic control characters.
    """

    for character in value:
        if character in ("\t", "\n", "\r"):
            return True

        if unicodedata.category(character) == "Cc":
            return True

    return False


def require_text(
    value: Any,
    *,
    field: str,
    max_length: int | None = None,
) -> str:
    """
    Validate and normalize required domain text.

    Whitespace surrounding the value is removed, but internal content is
    preserved.
    """

    if not isinstance(value, str):
        raise DomainValidationError(
            "Value must be a string.",
            field=field,
            context={
                "received_type": type(value).__name__,
            },
        )

    normalized = value.strip()

    if not normalized:
        raise DomainValidationError(
            "Value must not be empty.",
            field=field,
        )

    if _contains_disallowed_control_character(normalized):
        raise DomainValidationError(
            "Value contains disallowed control characters.",
            field=field,
        )

    if max_length is not None and len(normalized) > max_length:
        raise DomainValidationError(
            "Value exceeds the allowed length.",
            field=field,
            context={
                "max_length": max_length,
            },
        )

    return normalized


def optional_text(
    value: Any,
    *,
    field: str,
    max_length: int | None = None,
) -> str | None:
    """
    Normalize optional text while preserving ``None``.
    """

    if value is None:
        return None

    return require_text(
        value,
        field=field,
        max_length=max_length,
    )


def stable_unique_text(
    values: Iterable[Any] | None,
    *,
    field: str,
) -> tuple[str, ...]:
    """
    Normalize text values and remove duplicates while preserving order.
    """

    if values is None:
        return ()

    if isinstance(values, (str, bytes, bytearray)):
        raise DomainValidationError(
            "Value must be an iterable of strings, not a scalar string/bytes value.",
            field=field,
            context={
                "received_type": type(values).__name__,
            },
        )

    result: list[str] = []
    seen: set[str] = set()

    for index, value in enumerate(values):
        normalized = require_text(
            value,
            field=f"{field}[{index}]",
        )

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return tuple(result)


# ---------------------------------------------------------------------------
# Probability / confidence helpers
# ---------------------------------------------------------------------------


def normalize_probability(
    value: Any,
    *,
    field: str,
    allow_none: bool = False,
) -> float | None:
    """
    Normalize a numeric probability to the inclusive interval [0, 1].

    Boolean values are explicitly rejected because ``bool`` subclasses
    ``int`` in Python.
    """

    if value is None:
        if allow_none:
            return None

        raise DomainValidationError(
            "Probability is required.",
            field=field,
        )

    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, Decimal),
    ):
        raise DomainValidationError(
            "Probability must be numeric.",
            field=field,
            context={
                "received_type": type(value).__name__,
            },
        )

    normalized = float(value)

    if not math.isfinite(normalized):
        raise DomainValidationError(
            "Probability must be finite.",
            field=field,
        )

    if normalized < 0.0 or normalized > 1.0:
        raise DomainValidationError(
            "Probability must be between 0 and 1 inclusive.",
            field=field,
            context={
                "received": normalized,
            },
        )

    return normalized


# ---------------------------------------------------------------------------
# Cryptographic provenance helpers
# ---------------------------------------------------------------------------


def normalize_hash_algorithm(
    value: Any,
    *,
    field: str = "hash_algorithm",
) -> str:
    """
    Validate a fixed-length content-hash algorithm supported by hashlib.

    Extendable-output algorithms such as SHAKE are rejected because a
    canonical digest would additionally require a caller-selected output
    length.
    """

    requested = require_text(
        value,
        field=field,
    ).lower()

    try:
        digest = hashlib.new(requested)

    except (ValueError, TypeError) as exc:
        raise DomainValidationError(
            "Unsupported content-hash algorithm.",
            field=field,
            context={
                "algorithm": requested,
            },
        ) from exc

    if digest.digest_size <= 0:
        raise DomainValidationError(
            "Hash algorithm must have a fixed digest length.",
            field=field,
            context={
                "algorithm": digest.name,
            },
        )

    return digest.name


def normalize_hex_digest(
    value: Any,
    *,
    algorithm: str,
    field: str = "source_hash",
) -> str:
    """
    Validate a hexadecimal digest against its selected hash algorithm.
    """

    normalized_algorithm = normalize_hash_algorithm(algorithm)

    digest_text = require_text(
        value,
        field=field,
    ).lower()

    try:
        bytes.fromhex(digest_text)

    except ValueError as exc:
        raise DomainValidationError(
            "Content hash must be hexadecimal.",
            field=field,
        ) from exc

    expected_length = (
        hashlib.new(normalized_algorithm).digest_size * 2
    )

    if len(digest_text) != expected_length:
        raise DomainValidationError(
            "Content hash length does not match the selected algorithm.",
            field=field,
            context={
                "algorithm": normalized_algorithm,
                "expected_hex_length": expected_length,
                "received_hex_length": len(digest_text),
            },
        )

    return digest_text


def digest_bytes(
    data: bytes | bytearray | memoryview,
    *,
    algorithm: str = "sha256",
) -> str:
    """
    Compute a hexadecimal content digest without performing file I/O.

    File loading belongs to the infrastructure/ingestion layers. The domain
    helper deliberately accepts bytes only.
    """

    if not isinstance(
        data,
        (bytes, bytearray, memoryview),
    ):
        raise DomainValidationError(
            "Hash input must be bytes-like.",
            field="data",
            context={
                "received_type": type(data).__name__,
            },
        )

    normalized_algorithm = normalize_hash_algorithm(
        algorithm,
    )

    digest = hashlib.new(normalized_algorithm)
    digest.update(bytes(data))

    return digest.hexdigest()


def verify_digest(
    data: bytes | bytearray | memoryview,
    *,
    expected_digest: str,
    algorithm: str = "sha256",
) -> bool:
    """
    Verify source bytes against an expected digest.

    Constant-time comparison is used to avoid unnecessary timing differences
    in hash comparison.
    """

    normalized_algorithm = normalize_hash_algorithm(
        algorithm,
    )

    normalized_expected = normalize_hex_digest(
        expected_digest,
        algorithm=normalized_algorithm,
    )

    actual = digest_bytes(
        data,
        algorithm=normalized_algorithm,
    )

    return hmac.compare_digest(
        actual,
        normalized_expected,
    )


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def require_mapping(
    value: Any,
    *,
    field: str,
) -> Mapping[str, Any]:
    """
    Validate that ``value`` is a string-keyed mapping.
    """

    if not isinstance(value, Mapping):
        raise DomainValidationError(
            "Value must be a mapping.",
            field=field,
            context={
                "received_type": type(value).__name__,
            },
        )

    for key in value:
        if not isinstance(key, str):
            raise DomainValidationError(
                "Mapping keys must be strings.",
                field=field,
                context={
                    "received_key_type": type(key).__name__,
                },
            )

    return value


# ---------------------------------------------------------------------------
# Immutable evidence-value helpers
# ---------------------------------------------------------------------------


def freeze_json_value(
    value: Any,
    *,
    field: str = "value",
) -> Any:
    """
    Normalize evidence into an immutable deterministic JSON-compatible form.

    Supported values
    ----------------
    - None
    - str
    - bool
    - int
    - finite float
    - Decimal
    - datetime/date
    - UUID
    - Enum
    - string-keyed mappings
    - lists/tuples

    Decimal values are converted to strings rather than binary floating-point
    numbers so evidence precision is not silently reduced.
    """

    if value is None or isinstance(
        value,
        (str, bool, int),
    ):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise DomainSerializationError(
                "Floating-point evidence values must be finite.",
                field=field,
            )

        return value

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, datetime):
        return format_utc_datetime(value)

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, Enum):
        return freeze_json_value(
            value.value,
            field=field,
        )

    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}

        for key, item in value.items():
            normalized_key = require_text(
                key,
                field=f"{field}.key",
            )

            if normalized_key in frozen:
                raise DomainSerializationError(
                    "Mapping contains duplicate normalized keys.",
                    field=field,
                    context={
                        "key": normalized_key,
                    },
                )

            frozen[normalized_key] = freeze_json_value(
                item,
                field=f"{field}.{normalized_key}",
            )

        return MappingProxyType(frozen)

    if isinstance(value, (list, tuple)):
        return tuple(
            freeze_json_value(
                item,
                field=f"{field}[{index}]",
            )
            for index, item in enumerate(value)
        )

    raise DomainSerializationError(
        "Unsupported evidence value type.",
        field=field,
        context={
            "received_type": type(value).__name__,
        },
    )


def thaw_json_value(value: Any) -> Any:
    """
    Convert immutable internal evidence values into ordinary JSON-ready data.
    """

    if isinstance(value, Mapping):
        return {
            str(key): thaw_json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, tuple):
        return [
            thaw_json_value(item)
            for item in value
        ]

    return value


__all__ = [
    "utc_now",
    "ensure_utc_datetime",
    "format_utc_datetime",
    "require_text",
    "optional_text",
    "stable_unique_text",
    "normalize_probability",
    "normalize_hash_algorithm",
    "normalize_hex_digest",
    "digest_bytes",
    "verify_digest",
    "require_mapping",
    "freeze_json_value",
    "thaw_json_value",
]