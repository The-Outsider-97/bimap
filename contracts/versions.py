"""
Authoritative version registry for BIMAP externally visible data contracts.

This module versions external *data contracts*. It does not version the BIMAP
application itself (``bimap/version.py``), deterministic audit rulesets, the
Combined Audit correlation algorithm, SLAI, or generated report templates.
Those concerns evolve independently and must not be conflated.

The R3D implementation specification requires stable, versioned evidence,
finding, order, job, and report representations so customer deliverables and
future Revit/APS integrations remain reproducible. This module provides one
immutable registry for those schema identities.

Dependency direction
--------------------
contracts/utils/contracts_errors.py
contracts/utils/contracts_helpers.py
        ↑
contracts/versions.py
        ↑
contract DTO modules / schema_export.py

``versions.py`` MUST NOT import concrete contract DTO modules. Schema export may
import this registry; the registry must never import schema export.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts version")
printer = PrettyPrinter()


# ---------------------------------------------------------------------------
# Public schema-version constants
# ---------------------------------------------------------------------------
#
# These are the initial declared versions of BIMAP's external contract schemas.
# They intentionally begin independently even though the first release shares
# the same numeric value. A future change to one schema MUST NOT require a
# synchronized version bump to unrelated schemas.

EVIDENCE_SCHEMA_VERSION = "1.0.0"
FAMILY_EVIDENCE_SCHEMA_VERSION = "1.0.0"
PROJECT_EVIDENCE_SCHEMA_VERSION = "1.0.0"
FINDING_SCHEMA_VERSION = "1.0.0"
REQUIREMENT_SCHEMA_VERSION = "1.0.0"
ORDER_SCHEMA_VERSION = "1.0.0"
AUDIT_JOB_SCHEMA_VERSION = "1.0.0"
REPORT_MANIFEST_SCHEMA_VERSION = "1.0.0"


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "contracts_version_method_start", "action": action})


class ContractName(str, Enum):
    """Stable canonical keys for BIMAP's externally visible contracts."""

    EVIDENCE = "evidence"
    FAMILY_EVIDENCE = "family_evidence"
    PROJECT_EVIDENCE = "project_evidence"
    FINDING = "finding"
    REQUIREMENT = "requirement"
    ORDER = "order"
    AUDIT_JOB = "audit_job"
    REPORT_MANIFEST = "report_manifest"

    @classmethod
    def parse(cls, value: Any) -> "ContractName":
        """Normalize a supported contract key into ``ContractName``."""
        _announce("Parsing contract name")

        if isinstance(value, cls):
            return value

        normalized = normalize_contract_key(value)
        try:
            return cls(normalized)
        except ValueError as exc:
            logger.warning(
                {
                    "event": "unknown_contract",
                    "contract": normalized,
                }
            )
            raise UnknownContractError(
                "Unknown BIMAP external contract.",
                contract=normalized,
                context={"registered": tuple(item.value for item in cls)},
                cause=exc,
            ) from exc

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True, slots=True)
class SchemaVersion:
    """
    Immutable numeric BIMAP schema version.

    Compatibility is *not* inferred from this value object. The explicit
    supported-version registry is authoritative; sharing a major version alone
    does not automatically make a payload supported.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for field_name in ("major", "minor", "patch"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractVersionFormatError(
                    "Schema-version components must be integers.",
                    field=field_name,
                    context={"received_type": type(value).__name__},
                )
            if value < 0:
                raise ContractVersionFormatError(
                    "Schema-version components must be non-negative.",
                    field=field_name,
                    context={"received": value},
                )

    @classmethod
    def parse(cls, value: str | "SchemaVersion", *, contract: str | None = None) -> "SchemaVersion":
        """Parse a canonical schema-version string."""
        _announce("Parsing schema version")

        if isinstance(value, cls):
            return value

        major, minor, patch = parse_schema_version(value, contract=contract)
        return cls(major=major, minor=minor, patch=patch)

    def same_major(self, other: str | "SchemaVersion") -> bool:
        """Return whether another version has the same major component."""
        _announce("Comparing schema major version")
        candidate = SchemaVersion.parse(other)
        return self.major == candidate.major

    def to_tuple(self) -> tuple[int, int, int]:
        """Return the version as ``(major, minor, patch)``."""
        _announce("Serializing schema version tuple")
        return self.major, self.minor, self.patch

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class ContractVersionRecord:
    """Immutable version metadata for one external contract."""

    contract: ContractName
    current: SchemaVersion
    supported: tuple[SchemaVersion, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.contract, ContractName):
            raise ContractRegistryError(
                "ContractVersionRecord requires a ContractName.",
                context={"received_type": type(self.contract).__name__},
            )
        if not isinstance(self.current, SchemaVersion):
            raise ContractRegistryError(
                "ContractVersionRecord requires a SchemaVersion as current.",
                contract=self.contract.value,
                context={"received_type": type(self.current).__name__},
            )

        normalized_supported = tuple(self.supported)
        if not normalized_supported:
            raise ContractRegistryError(
                "A contract must declare at least one supported schema version.",
                contract=self.contract.value,
            )
        if any(not isinstance(item, SchemaVersion) for item in normalized_supported):
            raise ContractRegistryError(
                "Supported schema versions must be SchemaVersion values.",
                contract=self.contract.value,
            )
        if len(set(normalized_supported)) != len(normalized_supported):
            raise ContractRegistryError(
                "Supported schema-version list contains duplicates.",
                contract=self.contract.value,
            )
        if self.current not in normalized_supported:
            raise ContractRegistryError(
                "Current schema version must also be explicitly supported.",
                contract=self.contract.value,
                version=str(self.current),
            )

        object.__setattr__(self, "supported", normalized_supported)

    def supports(self, version: str | SchemaVersion) -> bool:
        """Return whether a version is explicitly supported by this record."""
        _announce(f"Checking {self.contract.value} schema support")
        candidate = SchemaVersion.parse(version, contract=self.contract.value)
        return candidate in self.supported

    def require_supported(self, version: str | SchemaVersion) -> SchemaVersion:
        """Return a parsed version or raise when it is not explicitly supported."""
        _announce(f"Requiring supported {self.contract.value} schema version")
        candidate = SchemaVersion.parse(version, contract=self.contract.value)
        ensure_supported_schema_version(
            str(candidate),
            supported=(str(item) for item in self.supported),
            contract=self.contract.value,
        )
        return candidate

    def to_dict(self) -> dict[str, Any]:
        """Return primitive version metadata suitable for manifests/logs."""
        _announce(f"Serializing {self.contract.value} version record")
        return {
            "contract": self.contract.value,
            "current": str(self.current),
            "supported": [str(item) for item in self.supported],
        }


def _record(contract: ContractName, current: str) -> ContractVersionRecord:
    # Registry construction occurs at import time and must remain side-effect
    # free: use the pure helper rather than the public, PrettyPrinter-enabled
    # ``SchemaVersion.parse`` method.
    normalized = normalize_schema_version(current, contract=contract.value)
    major, minor, patch = parse_schema_version(normalized, contract=contract.value)
    version = SchemaVersion(major=major, minor=minor, patch=patch)
    return ContractVersionRecord(
        contract=contract,
        current=version,
        supported=(version,),
    )


_DEFAULT_RECORDS = (
    _record(ContractName.EVIDENCE, EVIDENCE_SCHEMA_VERSION),
    _record(ContractName.FAMILY_EVIDENCE, FAMILY_EVIDENCE_SCHEMA_VERSION),
    _record(ContractName.PROJECT_EVIDENCE, PROJECT_EVIDENCE_SCHEMA_VERSION),
    _record(ContractName.FINDING, FINDING_SCHEMA_VERSION),
    _record(ContractName.REQUIREMENT, REQUIREMENT_SCHEMA_VERSION),
    _record(ContractName.ORDER, ORDER_SCHEMA_VERSION),
    _record(ContractName.AUDIT_JOB, AUDIT_JOB_SCHEMA_VERSION),
    _record(ContractName.REPORT_MANIFEST, REPORT_MANIFEST_SCHEMA_VERSION),
)


def _build_registry(
    records: tuple[ContractVersionRecord, ...],
) -> Mapping[ContractName, ContractVersionRecord]:
    registry: dict[ContractName, ContractVersionRecord] = {}
    for record in records:
        if record.contract in registry:
            raise DuplicateContractRegistrationError(
                "Duplicate contract-version registration.",
                contract=record.contract.value,
            )
        registry[record.contract] = record
    return MappingProxyType(registry)


_REGISTRY = _build_registry(_DEFAULT_RECORDS)

CURRENT_SCHEMA_VERSIONS: Mapping[str, str] = MappingProxyType(
    {
        name.value: str(record.current)
        for name, record in _REGISTRY.items()
    }
)

SUPPORTED_SCHEMA_VERSIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        name.value: tuple(str(item) for item in record.supported)
        for name, record in _REGISTRY.items()
    }
)


class ContractsVersion:
    """
    Read-only service over BIMAP's authoritative contract-version registry.

    The service deliberately exposes no runtime registration or mutation API.
    External contract support is source-controlled and release-reviewed so a
    running process cannot silently change what persisted payloads mean.
    """

    def __init__(self) -> None:
        _announce("Initializing contract-version registry")
        self._registry = _REGISTRY
        self.validate()
        logger.info(
            {
                "event": "contracts_version_registry_initialized",
                "contracts": len(self._registry),
            }
        )

    def _resolve(self, contract: str | ContractName) -> ContractName:
        return ContractName.parse(contract)

    def get(self, contract: str | ContractName) -> ContractVersionRecord:
        """Return immutable version metadata for a contract."""
        _announce("Resolving contract version record")
        name = self._resolve(contract)
        try:
            return self._registry[name]
        except KeyError as exc:  # defensive; ContractName currently mirrors registry
            raise UnknownContractError(
                "Contract is not registered in the version registry.",
                contract=name.value,
                cause=exc,
            ) from exc

    def current(self, contract: str | ContractName) -> SchemaVersion:
        """Return the current schema version for a contract."""
        _announce("Getting current contract schema version")
        return self.get(contract).current

    def supported(self, contract: str | ContractName) -> tuple[SchemaVersion, ...]:
        """Return all explicitly supported versions for a contract."""
        _announce("Getting supported contract schema versions")
        return self.get(contract).supported

    def supports(
        self,
        contract: str | ContractName,
        version: str | SchemaVersion,
    ) -> bool:
        """Return whether an exact schema version is supported."""
        _announce("Checking contract schema-version support")
        return self.get(contract).supports(version)

    def require_supported(
        self,
        contract: str | ContractName,
        version: str | SchemaVersion,
    ) -> SchemaVersion:
        """Require an exact explicitly supported schema version."""
        _announce("Validating contract schema-version support")
        return self.get(contract).require_supported(version)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a deterministic primitive snapshot for manifests/diagnostics."""
        _announce("Creating contract-version registry snapshot")
        return {
            name.value: {
                "current": str(record.current),
                "supported": [str(item) for item in record.supported],
            }
            for name, record in sorted(
                self._registry.items(),
                key=lambda item: item[0].value,
            )
        }

    def validate(self) -> None:
        """Validate internal registry invariants and fail closed on drift."""
        _announce("Validating contract-version registry")

        expected = set(ContractName)
        actual = set(self._registry)
        if expected != actual:
            missing = tuple(sorted(item.value for item in expected - actual))
            unexpected = tuple(sorted(item.value for item in actual - expected))
            raise ContractRegistryError(
                "Contract-version registry does not match ContractName.",
                context={
                    "missing": missing,
                    "unexpected": unexpected,
                },
            )

        for name, record in self._registry.items():
            if record.contract is not name:
                raise ContractRegistryError(
                    "Contract-version registry key/record identity mismatch.",
                    contract=name.value,
                    context={"record_contract": record.contract.value},
                )

            normalized_current = normalize_schema_version(
                str(record.current),
                contract=name.value,
            )
            if normalized_current != str(record.current):
                raise ContractRegistryError(
                    "Contract current version is not canonical.",
                    contract=name.value,
                    version=str(record.current),
                )

            if record.current not in record.supported:
                raise ContractRegistryError(
                    "Current contract version is not supported.",
                    contract=name.value,
                    version=str(record.current),
                )

    def __contains__(self, contract: object) -> bool:
        # Keep dunder membership checks quiet; public methods provide explicit
        # PrettyPrinter diagnostics.
        if isinstance(contract, ContractName):
            return contract in self._registry
        try:
            normalized = normalize_contract_key(contract)
            name = ContractName(normalized)
        except (ContractError, TypeError, ValueError) as exc:
            # ``normalize_contract_key`` raises a ContractError, while the enum
            # constructor raises ValueError. Membership must return False for
            # either case rather than turn a probe into control-flow failure.
            logger.debug(
                {
                    "event": "contract_membership_probe_rejected",
                    "received_type": type(contract).__name__,
                    "cause_type": type(exc).__name__,
                }
            )
            return False
        return name in self._registry

    def __len__(self) -> int:
        return len(self._registry)

    def __iter__(self) -> Iterator[ContractName]:
        return iter(self._registry)


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "FAMILY_EVIDENCE_SCHEMA_VERSION",
    "PROJECT_EVIDENCE_SCHEMA_VERSION",
    "FINDING_SCHEMA_VERSION",
    "REQUIREMENT_SCHEMA_VERSION",
    "ORDER_SCHEMA_VERSION",
    "AUDIT_JOB_SCHEMA_VERSION",
    "REPORT_MANIFEST_SCHEMA_VERSION",
    "CURRENT_SCHEMA_VERSIONS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ContractName",
    "SchemaVersion",
    "ContractVersionRecord",
    "ContractsVersion",
]


if __name__ == "__main__":
    print("\n=== Running Contracts Version Self-Test ===\n")
    printer.status("TEST", "Contracts version module initialized", "info")

    registry = ContractsVersion()
    assert len(registry) == len(ContractName)
    printer.status("PASS", "Registry cardinality", "success")

    for contract in ContractName:
        current = registry.current(contract)
        assert registry.supports(contract, current)
        assert registry.require_supported(contract, str(current)) == current
    printer.status("PASS", "Current/supported version invariants", "success")

    snapshot = registry.snapshot()
    assert snapshot["finding"]["current"] == FINDING_SCHEMA_VERSION
    assert snapshot["report_manifest"]["current"] == REPORT_MANIFEST_SCHEMA_VERSION
    printer.status("PASS", "Registry snapshot", "success")

    print("\n=== Test ran successfully ===\n")
