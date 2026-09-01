"""
Structured error hierarchy for BIMAP external contracts.

The contracts layer is the boundary between BIMAP's internal domain model and
versioned external representations used by API payloads, workers, report
artifacts, the future Revit exporter, and other integrations.

Dependency direction
--------------------
contracts_errors.py
    -> Python standard library
    -> SLAI logging utilities

contracts_errors.py MUST NOT import:
    - contracts_helpers.py
    - versions.py
    - any concrete contract DTO/model
    - domain models
    - API/application/infrastructure/SLAI adapters

Keeping the error hierarchy at the bottom of the contracts dependency graph
prevents circular imports and allows every contract module to depend on one
stable exception vocabulary.

Operational note
----------------
Exception construction deliberately does not print or emit an error-level log.
Errors should be logged once at an architectural boundary (API, worker,
bootstrap, schema export, etc.) rather than once when raised and again when
handled. The module still integrates SLAI's logger and PrettyPrinter so callers
can explicitly emit a status through ``ContractError.announce`` when useful.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Errors")
printer = PrettyPrinter()


_REDACTED = "<redacted>"
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "evidence_value",
    "password",
    "payload",
    "raw_content",
    "secret",
    "token",
)
_MAX_CONTEXT_STRING = 256
_MAX_CONTEXT_ITEMS = 32


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded, logging-safe diagnostic representation."""
    if depth >= 3:
        return f"<{type(value).__name__}>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        if len(value) <= _MAX_CONTEXT_STRING:
            return value
        return f"{value[:_MAX_CONTEXT_STRING]}…"

    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTEXT_ITEMS:
                safe["__truncated__"] = True
                break
            rendered_key = str(key)
            safe[rendered_key] = (
                _REDACTED
                if _is_sensitive_key(rendered_key)
                else _safe_context_value(item, depth=depth + 1)
            )
        return safe

    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = list(value)
        rendered = [
            _safe_context_value(item, depth=depth + 1)
            for item in sequence[:_MAX_CONTEXT_ITEMS]
        ]
        if len(sequence) > _MAX_CONTEXT_ITEMS:
            rendered.append("<truncated>")
        return rendered

    return f"<{type(value).__name__}>"


def _normalize_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if context is None:
        return {}

    if not isinstance(context, Mapping):
        raise TypeError(
            "Contract error context must be a mapping or None, "
            f"got {type(context).__name__}."
        )

    return {
        str(key): _safe_context_value(
            _REDACTED if _is_sensitive_key(str(key)) else value
        )
        for key, value in context.items()
    }


class ContractError(Exception):
    """
    Base exception for all BIMAP contract-layer failures.

    Parameters
    ----------
    message:
        Human-readable technical description. This text is intended for
        developers/operators and must not contain raw customer evidence.
    contract:
        Optional canonical contract key such as ``family_evidence``.
    version:
        Optional contract/schema version involved in the failure.
    field:
        Optional logical field name associated with the failure.
    path:
        Optional dotted/indexed path inside a contract payload.
    context:
        Optional non-sensitive structured diagnostics. Context is bounded and
        redacted before storage on the exception.
    cause:
        Optional underlying exception. Only the cause type is exposed by
        ``to_dict``; raw cause text is deliberately omitted.

    Notes
    -----
    ``code`` is stable and machine-readable. Higher layers should map errors by
    class or ``code`` rather than parsing exception messages.
    """

    code = "BIMAP.CONTRACTS.ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        contract: str | None = None,
        version: str | None = None,
        field: str | None = None,
        path: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__

        self.message = normalized_message
        self.contract = str(contract).strip() if contract is not None else None
        self.version = str(version).strip() if version is not None else None
        self.field = str(field).strip() if field is not None else None
        self.path = str(path).strip() if path is not None else None
        self.context = _normalize_context(context)
        self.cause = cause

        rendered = normalized_message
        qualifiers: list[str] = []
        if self.contract:
            qualifiers.append(f"contract={self.contract}")
        if self.version:
            qualifiers.append(f"version={self.version}")
        if self.field:
            qualifiers.append(f"field={self.field}")
        if self.path:
            qualifiers.append(f"path={self.path}")
        if qualifiers:
            rendered = f"{rendered} [{', '.join(qualifiers)}]"

        super().__init__(rendered)

    def announce(
        self,
        *,
        label: str = "CONTRACTS",
        level: str = "error",
    ) -> None:
        """Explicitly emit a PrettyPrinter status for this handled error."""
        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "contract_error_announced",
                "code": self.code,
                "contract": self.contract,
                "version": self.version,
                "field": self.field,
                "path": self.path,
            }
        )

    def to_dict(
        self,
        *,
        include_context: bool = True,
        include_cause_type: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic, logging-safe representation of the error."""
        logger.debug(
            {
                "event": "contract_error_to_dict",
                "code": self.code,
                "type": self.__class__.__name__,
            }
        )

        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "retryable": bool(self.retryable),
        }

        if self.contract:
            payload["contract"] = self.contract
        if self.version:
            payload["version"] = self.version
        if self.field:
            payload["field"] = self.field
        if self.path:
            payload["path"] = self.path
        if include_context and self.context:
            payload["context"] = dict(self.context)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__

        return payload


class ContractValidationError(ContractError):
    """Raised when contract data violates a declared contract constraint."""

    code = "BIMAP.CONTRACTS.VALIDATION"


class ContractFieldError(ContractValidationError):
    """Base class for field-set and field-value contract failures."""

    code = "BIMAP.CONTRACTS.FIELD"


class MissingContractFieldError(ContractFieldError):
    """Raised when one or more required external contract fields are absent."""

    code = "BIMAP.CONTRACTS.FIELD.MISSING"


class UnexpectedContractFieldError(ContractFieldError):
    """Raised when a closed contract receives unsupported fields."""

    code = "BIMAP.CONTRACTS.FIELD.UNEXPECTED"


class ContractSerializationError(ContractError):
    """Raised when a contract cannot be deterministically serialized."""

    code = "BIMAP.CONTRACTS.SERIALIZATION"


class ContractDeserializationError(ContractError):
    """Raised when serialized contract data cannot be decoded safely."""

    code = "BIMAP.CONTRACTS.DESERIALIZATION"


class ContractVersionError(ContractError):
    """Base class for contract/schema version failures."""

    code = "BIMAP.CONTRACTS.VERSION"


class ContractVersionFormatError(ContractVersionError):
    """Raised when a version identifier is not in BIMAP's canonical format."""

    code = "BIMAP.CONTRACTS.VERSION.FORMAT"


class UnsupportedContractVersionError(ContractVersionError):
    """Raised when a well-formed contract version is not explicitly supported."""

    code = "BIMAP.CONTRACTS.VERSION.UNSUPPORTED"


class ContractCompatibilityError(ContractVersionError):
    """Raised when an attempted compatibility relation is not permitted."""

    code = "BIMAP.CONTRACTS.VERSION.INCOMPATIBLE"


class ContractRegistryError(ContractError):
    """Base class for contract-version registry integrity failures."""

    code = "BIMAP.CONTRACTS.REGISTRY"


class UnknownContractError(ContractRegistryError):
    """Raised when a contract key is not registered by BIMAP."""

    code = "BIMAP.CONTRACTS.REGISTRY.UNKNOWN_CONTRACT"


class DuplicateContractRegistrationError(ContractRegistryError):
    """Raised when a contract registry would contain duplicate identities."""

    code = "BIMAP.CONTRACTS.REGISTRY.DUPLICATE"


class ContractSchemaError(ContractError):
    """Base class for JSON-Schema definition/export/validation failures."""

    code = "BIMAP.CONTRACTS.SCHEMA"


class ContractSchemaDefinitionError(ContractSchemaError):
    """Raised when BIMAP itself defines an internally invalid schema contract."""

    code = "BIMAP.CONTRACTS.SCHEMA.DEFINITION"


class ContractSchemaValidationError(ContractSchemaError):
    """Raised when external data fails validation against a declared schema."""

    code = "BIMAP.CONTRACTS.SCHEMA.VALIDATION"


class ContractIntegrityError(ContractError):
    """Raised when contract identity/version/serialized state is inconsistent."""

    code = "BIMAP.CONTRACTS.INTEGRITY"


__all__ = [
    "ContractError",
    "ContractValidationError",
    "ContractFieldError",
    "MissingContractFieldError",
    "UnexpectedContractFieldError",
    "ContractSerializationError",
    "ContractDeserializationError",
    "ContractVersionError",
    "ContractVersionFormatError",
    "UnsupportedContractVersionError",
    "ContractCompatibilityError",
    "ContractRegistryError",
    "UnknownContractError",
    "DuplicateContractRegistrationError",
    "ContractSchemaError",
    "ContractSchemaDefinitionError",
    "ContractSchemaValidationError",
    "ContractIntegrityError",
]


if __name__ == "__main__":
    print("\n=== Running Contracts Error Self-Test ===\n")
    printer.status("TEST", "Contracts error hierarchy initialized", "info")

    sample = UnsupportedContractVersionError(
        "Unsupported schema version.",
        contract="finding",
        version="9.0.0",
        field="schema_version",
        context={
            "supported": ("1.0.0",),
            "token": "must-not-leak",
        },
    )

    payload = sample.to_dict()
    assert payload["code"] == "BIMAP.CONTRACTS.VERSION.UNSUPPORTED"
    assert payload["context"]["token"] == _REDACTED
    assert "must-not-leak" not in str(payload)
    printer.status("PASS", "Structured error serialization", "success")

    print("\n=== Test ran successfully ===\n")
