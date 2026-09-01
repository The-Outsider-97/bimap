"""
Shared helpers for BIMAP external contracts.

The helpers in this module are intentionally contract-specific. Generic domain
normalization, evidence hashing, timestamp handling, and immutable evidence
value handling remain owned by ``domain.utils.domain_helpers`` and are reused
here rather than reimplemented.

Dependency direction
--------------------
contracts_errors.py
        ↑
domain.utils.domain_helpers
        ↑
contracts_helpers.py
        ↑
versions.py / contract DTOs / schema_export.py

``contracts_helpers.py`` MUST NOT import ``versions.py`` or any concrete
contract DTO. That rule keeps version parsing and serialization helpers usable
from the bottom of the contracts graph without circular imports.
"""

from __future__ import annotations

import json
import re

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from ...domain.utils.domain_errors import DomainError
from ...domain.utils.domain_helpers import *
from .contracts_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Helpers")
printer = PrettyPrinter()


_CONTRACT_KEY_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_SCHEMA_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


def normalize_contract_key(value: Any, *, field: str = "contract") -> str:
    """
    Validate a canonical BIMAP contract key.

    Contract keys are deliberately strict lower-case snake_case identifiers.
    The helper does not silently lowercase or rewrite caller input because
    external contract identity must remain explicit and reproducible.
    """
    if not isinstance(value, str):
        raise ContractValidationError(
            "Contract key must be a string.",
            field=field,
            context={"received_type": type(value).__name__},
        )

    normalized = value.strip()
    if not normalized:
        raise ContractValidationError(
            "Contract key must not be empty.",
            field=field,
        )

    if _CONTRACT_KEY_RE.fullmatch(normalized) is None:
        raise ContractValidationError(
            "Contract key must use canonical lower-case snake_case syntax.",
            field=field,
            context={"received": normalized},
        )

    logger.debug(
        {"event": "contract_key_normalized", "contract": normalized}
    )
    return normalized


def normalize_schema_version(
    value: Any,
    *,
    field: str = "schema_version",
    contract: str | None = None,
) -> str:
    """
    Validate BIMAP's canonical external-schema version format.

    BIMAP contract schemas use the unambiguous numeric ``MAJOR.MINOR.PATCH``
    core form. Pre-release/build suffixes are intentionally excluded from the
    public schema identifier so persisted evidence and report manifests do not
    depend on deployment-local labels.
    """
    if not isinstance(value, str):
        raise ContractVersionFormatError(
            "Schema version must be a string in MAJOR.MINOR.PATCH form.",
            contract=contract,
            field=field,
            context={"received_type": type(value).__name__},
        )

    normalized = value.strip()
    if _SCHEMA_VERSION_RE.fullmatch(normalized) is None:
        raise ContractVersionFormatError(
            "Schema version must use canonical MAJOR.MINOR.PATCH syntax.",
            contract=contract,
            version=normalized or None,
            field=field,
        )

    return normalized


def parse_schema_version(
    value: Any,
    *,
    field: str = "schema_version",
    contract: str | None = None,
) -> tuple[int, int, int]:
    """Parse a canonical schema version into ``(major, minor, patch)``."""
    normalized = normalize_schema_version(
        value,
        field=field,
        contract=contract,
    )
    major, minor, patch = normalized.split(".")
    return int(major), int(minor), int(patch)


def compare_schema_versions(left: str, right: str) -> int:
    """Return ``-1``, ``0`` or ``1`` for two canonical schema versions."""
    lhs = parse_schema_version(left, field="left_version")
    rhs = parse_schema_version(right, field="right_version")
    return (lhs > rhs) - (lhs < rhs)


def same_schema_major(left: str, right: str) -> bool:
    """Return whether two canonical schema versions share the same major."""
    return parse_schema_version(left)[0] == parse_schema_version(right)[0]


def ensure_supported_schema_version(
    requested: str,
    *,
    supported: Iterable[str],
    contract: str | None = None,
) -> str:
    """
    Require an exact explicitly supported schema version.

    No implicit "same major means supported" rule is applied. Compatibility is
    a contract decision and must be declared in the version registry rather
    than guessed from semantic-version shape alone.
    """
    normalized_requested = normalize_schema_version(
        requested,
        contract=contract,
    )

    normalized_supported: list[str] = []
    seen: set[str] = set()
    for value in supported:
        normalized = normalize_schema_version(
            value,
            contract=contract,
        )
        if normalized not in seen:
            seen.add(normalized)
            normalized_supported.append(normalized)

    if not normalized_supported:
        raise ContractValidationError(
            "Supported schema-version set must not be empty.",
            contract=contract,
            field="supported",
        )

    if normalized_requested not in seen:
        raise UnsupportedContractVersionError(
            "Schema version is not explicitly supported.",
            contract=contract,
            version=normalized_requested,
            field="schema_version",
            context={"supported": tuple(normalized_supported)},
        )

    return normalized_requested


def require_contract_mapping(
    value: Any,
    *,
    contract: str | None = None,
    field: str = "payload",
) -> Mapping[str, Any]:
    """Require a string-keyed mapping and translate domain errors cleanly."""
    try:
        return require_mapping(value, field=field)
    except DomainError as exc:
        raise ContractValidationError(
            "Contract payload must be a string-keyed mapping.",
            contract=contract,
            field=field,
            context={"received_type": type(value).__name__},
            cause=exc,
        ) from exc


def validate_contract_fields(
    payload: Mapping[str, Any],
    *,
    required: Iterable[str] = (),
    optional: Iterable[str] = (),
    allow_unknown: bool = False,
    contract: str | None = None,
) -> dict[str, Any]:
    """
    Validate the declared field set of a mapping and return a plain copy.

    Required and optional field names are normalized only by surrounding
    whitespace removal; callers must supply canonical field names. Duplicates
    in the declarations are collapsed while preserving their first occurrence.
    """
    mapping = require_contract_mapping(payload, contract=contract)

    def _field_names(values: Iterable[str], label: str) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(values):
            if not isinstance(item, str) or not item.strip():
                raise ContractValidationError(
                    "Declared contract field names must be non-empty strings.",
                    contract=contract,
                    field=f"{label}[{index}]",
                )
            normalized = item.strip()
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return tuple(result)

    required_fields = _field_names(required, "required")
    optional_fields = _field_names(optional, "optional")

    overlap = set(required_fields).intersection(optional_fields)
    if overlap:
        raise ContractValidationError(
            "Contract field declaration contains required/optional overlap.",
            contract=contract,
            field="field_set",
            context={"overlap": tuple(sorted(overlap))},
        )

    missing = tuple(field for field in required_fields if field not in mapping)
    if missing:
        raise MissingContractFieldError(
            "Required contract fields are missing.",
            contract=contract,
            field="payload",
            context={"missing": missing},
        )

    if not allow_unknown:
        allowed = set(required_fields).union(optional_fields)
        unexpected = tuple(sorted(set(mapping).difference(allowed)))
        if unexpected:
            raise UnexpectedContractFieldError(
                "Contract payload contains unsupported fields.",
                contract=contract,
                field="payload",
                context={"unexpected": unexpected},
            )

    return dict(mapping)


def require_payload_schema_version(
    payload: Mapping[str, Any],
    *,
    expected: str,
    contract: str,
    field: str = "schema_version",
) -> str:
    """Require and exactly match a payload's declared schema version."""
    mapping = require_contract_mapping(payload, contract=contract)

    if field not in mapping:
        raise MissingContractFieldError(
            "Contract payload does not declare its schema version.",
            contract=contract,
            field=field,
        )

    requested = normalize_schema_version(
        mapping[field],
        field=field,
        contract=contract,
    )
    normalized_expected = normalize_schema_version(
        expected,
        field="expected_schema_version",
        contract=contract,
    )

    if requested != normalized_expected:
        raise UnsupportedContractVersionError(
            "Contract payload schema version does not match the expected version.",
            contract=contract,
            version=requested,
            field=field,
            context={"expected": normalized_expected},
        )

    return requested


def to_json_primitive(
    value: Any,
    *,
    contract: str | None = None,
    field: str = "payload",
) -> Any:
    """
    Convert supported contract/domain values into deterministic JSON data.

    Dataclasses are converted to dictionaries first. The domain's established
    ``freeze_json_value`` / ``thaw_json_value`` pair then performs the canonical
    primitive conversion, avoiding a second competing serialization policy.
    """
    candidate = asdict(value) if is_dataclass(value) else value # type: ignore

    try:
        frozen = freeze_json_value(candidate, field=field)
        return thaw_json_value(frozen)
    except DomainError as exc:
        raise ContractSerializationError(
            "Contract value cannot be represented as deterministic JSON data.",
            contract=contract,
            field=field,
            context={"received_type": type(value).__name__},
            cause=exc,
        ) from exc


def canonical_json_dumps(
    value: Any,
    *,
    contract: str | None = None,
    pretty: bool = False,
) -> str:
    """Serialize contract data with deterministic key ordering and no NaN."""
    primitive = to_json_primitive(value, contract=contract)

    try:
        if pretty:
            return json.dumps(
                primitive,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )

        return json.dumps(
            primitive,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractSerializationError(
            "Contract JSON serialization failed.",
            contract=contract,
            cause=exc,
        ) from exc


def canonical_json_bytes(
    value: Any,
    *,
    contract: str | None = None,
) -> bytes:
    """Return canonical UTF-8 JSON bytes for hashing/storage boundaries."""
    return canonical_json_dumps(
        value,
        contract=contract,
        pretty=False,
    ).encode("utf-8")


def canonical_json_loads(
    data: str | bytes | bytearray,
    *,
    contract: str | None = None,
) -> Any:
    """Decode JSON without performing schema validation or business logic."""
    if not isinstance(data, (str, bytes, bytearray)):
        raise ContractDeserializationError(
            "Contract JSON input must be str, bytes, or bytearray.",
            contract=contract,
            context={"received_type": type(data).__name__},
        )

    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractDeserializationError(
            "Contract JSON input is not valid JSON.",
            contract=contract,
            cause=exc,
        ) from exc


__all__ = [
    "normalize_contract_key",
    "normalize_schema_version",
    "parse_schema_version",
    "compare_schema_versions",
    "same_schema_major",
    "ensure_supported_schema_version",
    "require_contract_mapping",
    "validate_contract_fields",
    "require_payload_schema_version",
    "to_json_primitive",
    "canonical_json_dumps",
    "canonical_json_bytes",
    "canonical_json_loads",
]


if __name__ == "__main__":
    from dataclasses import dataclass

    print("\n=== Running Contracts Helpers Self-Test ===\n")
    printer.status("TEST", "Contracts helpers initialized", "info")

    assert normalize_schema_version("1.0.0") == "1.0.0"
    assert parse_schema_version("1.2.3") == (1, 2, 3)
    assert compare_schema_versions("1.0.0", "1.1.0") == -1
    assert same_schema_major("1.0.0", "1.9.9")
    printer.status("PASS", "Schema-version helpers", "success")

    validated = validate_contract_fields(
        {"schema_version": "1.0.0", "finding_id": "F-1"},
        required=("schema_version", "finding_id"),
        contract="finding",
    )
    assert validated["finding_id"] == "F-1"
    printer.status("PASS", "Contract field validation", "success")

    @dataclass(frozen=True)
    class _Example:
        z: int
        a: str

    serialized = canonical_json_dumps(_Example(z=1, a="x"))
    assert serialized == '{"a":"x","z":1}'
    assert canonical_json_loads(serialized) == {"a": "x", "z": 1}
    printer.status("PASS", "Canonical JSON helpers", "success")

    print("\n=== Test ran successfully ===\n")