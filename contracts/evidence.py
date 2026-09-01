"""
Versioned external BIMAP evidence contract.

This module defines the stable evidence DTO shared by higher-level Family
Evidence and Project Evidence contracts. It is intentionally an external
representation rather than a replacement for ``domain.evidence``.

The minimum evidence fields are grounded in the BIMAP implementation
specification: stable evidence identity, source identity/hash/type, logical
source location, extractor version, extracted value, and extraction confidence
where probabilistic extraction is used. Additional provenance fields exposed
here already exist in the canonical domain provenance model and are retained so
contract/domain conversion does not discard source-integrity information.

Dependency direction
--------------------
domain.evidence
contracts.utils
contracts.versions
        ↑
contracts/evidence.py
        ↑
family_evidence.py / project_evidence.py / finding.py / schema_export.py

The domain layer must never import this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .utils.contracts_errors import (
    ContractDeserializationError,
    ContractIntegrityError,
    ContractSerializationError,
    ContractValidationError,
)
from .utils.contracts_helpers import (
    canonical_json_dumps,
    canonical_json_loads,
    ensure_supported_schema_version,
    to_json_primitive,
    validate_contract_fields,
)
from .versions import (
    EVIDENCE_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ContractName,
)
from ..domain.evidence.models import EvidenceItem, LogicalLocation
from ..domain.evidence.provenance import ContentHash, Provenance, SourceIdentity
from ..domain.utils.domain_errors import DomainError, DomainInvariantError
from ..domain.utils.domain_helpers import (
    ensure_utc_datetime,
    format_utc_datetime,
    normalize_probability,
    optional_text,
    require_text,
    stable_unique_text,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Evidence")
printer = PrettyPrinter()

_CONTRACT = ContractName.EVIDENCE.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer evidence content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "evidence_contract_method_start", "action": action})


def _raise_contract_validation(
    message: str,
    *,
    field: str | None = None,
    cause: BaseException,
) -> None:
    """Translate lower-layer validation failures at the contract boundary."""
    if isinstance(cause, DomainInvariantError):
        raise ContractIntegrityError(
            message,
            contract=_CONTRACT,
            field=field,
            cause=cause,
        ) from cause

    raise ContractValidationError(
        message,
        contract=_CONTRACT,
        field=field,
        cause=cause,
    ) from cause


@dataclass(frozen=True, slots=True)
class EvidenceLocationContract:
    """Version-contained logical location of evidence inside a source object."""

    page: int | None = None
    row: int | None = None
    element: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        _announce("Validating evidence logical location")
        canonical: LogicalLocation | None = None
        try:
            canonical = LogicalLocation(
                page=self.page,
                row=self.row,
                element=self.element,
                path=self.path,
            )
        except DomainError as exc:
            _raise_contract_validation(
                "Invalid evidence logical location.",
                field="logical_location",
                cause=exc,
            )

        assert canonical is not None
        object.__setattr__(self, "page", canonical.page)
        object.__setattr__(self, "row", canonical.row)
        object.__setattr__(self, "element", canonical.element)
        object.__setattr__(self, "path", canonical.path)

    def to_dict(self) -> dict[str, Any]:
        """Return the compact primitive logical-location representation."""
        _announce("Serializing evidence logical location")
        return self.to_domain().to_dict()

    def to_domain(self) -> LogicalLocation:
        """Convert to the canonical domain ``LogicalLocation`` value object."""
        _announce("Converting evidence location to domain")
        try:
            return LogicalLocation(
                page=self.page,
                row=self.row,
                element=self.element,
                path=self.path,
            )
        except DomainError as exc:
            _raise_contract_validation(
                "Evidence logical location cannot be converted to domain form.",
                field="logical_location",
                cause=exc,
            )

    @classmethod
    def from_domain(cls, location: LogicalLocation) -> "EvidenceLocationContract":
        """Construct the contract value from a domain ``LogicalLocation``."""
        _announce("Creating evidence location from domain")
        if not isinstance(location, LogicalLocation):
            raise ContractValidationError(
                "location must be a LogicalLocation instance.",
                contract=_CONTRACT,
                field="logical_location",
                context={"received_type": type(location).__name__},
            )
        return cls(
            page=location.page,
            row=location.row,
            element=location.element,
            path=location.path,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceLocationContract":
        """Construct a logical-location contract from a strict mapping."""
        _announce("Deserializing evidence logical location")
        data = validate_contract_fields(
            payload,
            optional=("page", "row", "element", "path"),
            contract=_CONTRACT,
        )
        return cls(
            page=data.get("page"),
            row=data.get("row"),
            element=data.get("element"),
            path=data.get("path"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceContract:
    """
    Stable externally serializable BIMAP evidence object.

    ``source_file_id`` is the authoritative source identifier; filenames are
    descriptive metadata only. ``source_hash`` is validated against
    ``hash_algorithm``. ``confidence=None`` means the extraction process did not
    provide a probabilistic confidence value and MUST NOT be interpreted as
    confidence 1.0.
    """

    evidence_id: str
    source_file_id: str
    source_hash: str
    source_type: str
    extracted_at: str | datetime
    extracted_value: Any

    logical_location: EvidenceLocationContract | Mapping[str, Any] | None = None
    extractor_version: str | None = None
    confidence: float | None = None

    hash_algorithm: str = "sha256"
    extractor_name: str | None = None
    source_timestamp: str | datetime | None = None
    original_filename: str | None = None
    source_version: str | None = None
    traceability_refs: tuple[str, ...] = ()
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating evidence contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        source: SourceIdentity | None = None
        content_hash: ContentHash | None = None
        extracted_at: datetime | None = None
        source_timestamp: datetime | None = None
        extractor_name: str | None = None
        extractor_version: str | None = None
        evidence_id: str | None = None
        confidence: float | None = None
        traceability_refs: tuple[str, ...] = ()

        try:
            source = SourceIdentity(
                source_file_id=self.source_file_id,
                source_type=self.source_type,
                original_filename=self.original_filename,
                source_version=self.source_version,
            )
            content_hash = ContentHash(
                value=self.source_hash,
                algorithm=self.hash_algorithm,
            )
            extracted_at = ensure_utc_datetime(
                self.extracted_at,
                field="extracted_at",
            )
            source_timestamp = (
                ensure_utc_datetime(self.source_timestamp, field="source_timestamp")
                if self.source_timestamp is not None
                else None
            )
            extractor_name = optional_text(
                self.extractor_name,
                field="extractor_name",
            )
            extractor_version = optional_text(
                self.extractor_version,
                field="extractor_version",
            )
            evidence_id = require_text(self.evidence_id, field="evidence_id")
            confidence = normalize_probability(
                self.confidence,
                field="confidence",
                allow_none=True,
            )
            traceability_refs = stable_unique_text(
                self.traceability_refs,
                field="traceability_refs",
            )
        except (DomainError, ValueError, TypeError) as exc:
            _raise_contract_validation(
                "Evidence contract contains invalid provenance or evidence data.",
                cause=exc,
            )

        location: EvidenceLocationContract | None
        if self.logical_location is None:
            location = None
        elif isinstance(self.logical_location, EvidenceLocationContract):
            location = self.logical_location
        elif isinstance(self.logical_location, Mapping):
            location = EvidenceLocationContract.from_dict(self.logical_location)
        elif isinstance(self.logical_location, LogicalLocation):
            location = EvidenceLocationContract.from_domain(self.logical_location)
        else:
            raise ContractValidationError(
                "logical_location must be an EvidenceLocationContract, mapping, LogicalLocation, or None.",
                contract=_CONTRACT,
                field="logical_location",
                context={"received_type": type(self.logical_location).__name__},
            )

        try:
            extracted_value = to_json_primitive(
                self.extracted_value,
                contract=_CONTRACT,
                field="extracted_value",
            )
        except ContractSerializationError:
            raise

        object.__setattr__(self, "schema_version", str(self.schema_version).strip())
        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "source_file_id", source.source_file_id)
        object.__setattr__(self, "source_type", source.source_type)
        object.__setattr__(self, "original_filename", source.original_filename)
        object.__setattr__(self, "source_version", source.source_version)
        object.__setattr__(self, "source_hash", content_hash.value)
        object.__setattr__(self, "hash_algorithm", content_hash.algorithm)
        object.__setattr__(self, "extracted_at", format_utc_datetime(extracted_at))
        object.__setattr__(self, "source_timestamp",
            format_utc_datetime(source_timestamp) if source_timestamp is not None else None,
        )
        object.__setattr__(self, "extractor_name", extractor_name)
        object.__setattr__(self, "extractor_version", extractor_version)
        object.__setattr__(self, "logical_location", location)
        object.__setattr__(self, "extracted_value", extracted_value)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "traceability_refs", traceability_refs)

        logger.debug(
            {
                "event": "evidence_contract_validated",
                "evidence_id": self.evidence_id,
                "source_type": self.source_type,
                "has_location": self.logical_location is not None,
                "has_confidence": self.confidence is not None,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-ready external evidence representation."""
        _announce("Serializing evidence contract")
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "source_file_id": self.source_file_id,
            "source_hash": self.source_hash,
            "hash_algorithm": self.hash_algorithm,
            "source_type": self.source_type,
            "logical_location": (
                self.logical_location.to_dict()
                if isinstance(self.logical_location, EvidenceLocationContract)
                else None
            ),
            "extractor_name": self.extractor_name,
            "extractor_version": self.extractor_version,
            "extracted_at": self.extracted_at,
            "source_timestamp": self.source_timestamp,
            "original_filename": self.original_filename,
            "source_version": self.source_version,
            "traceability_refs": list(self.traceability_refs),
            "extracted_value": to_json_primitive(
                self.extracted_value,
                contract=_CONTRACT,
                field="extracted_value",
            ),
            "confidence": self.confidence,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize this evidence object using BIMAP canonical JSON rules."""
        _announce("Encoding evidence contract JSON")
        return canonical_json_dumps(self.to_dict(), contract=_CONTRACT, pretty=pretty)

    def to_domain(self) -> EvidenceItem:
        """Convert the external evidence contract to the canonical domain model."""
        _announce("Converting evidence contract to domain")
        try:
            extracted_at = ensure_utc_datetime(self.extracted_at, field="extracted_at")
            source_timestamp = (
                ensure_utc_datetime(self.source_timestamp, field="source_timestamp")
                if self.source_timestamp is not None
                else None
            )
            provenance = Provenance(
                source=SourceIdentity(
                    source_file_id=self.source_file_id,
                    source_type=self.source_type,
                    original_filename=self.original_filename,
                    source_version=self.source_version,
                ),
                content_hash=ContentHash(
                    value=self.source_hash,
                    algorithm=self.hash_algorithm,
                ),
                extracted_at=extracted_at,
                source_timestamp=source_timestamp,
                extractor_name=self.extractor_name,
                extractor_version=self.extractor_version,
                schema_version=self.schema_version,
                traceability_refs=self.traceability_refs,
            )
            return EvidenceItem(
                evidence_id=self.evidence_id,
                provenance=provenance,
                logical_location=(
                    self.logical_location.to_domain()
                    if isinstance(self.logical_location, EvidenceLocationContract)
                    else None
                ),
                extracted_value=self.extracted_value,
                confidence=self.confidence,
            )
        except DomainError as exc:
            _raise_contract_validation("Evidence contract cannot be converted to the canonical domain model.", cause=exc)

    @classmethod
    def from_domain(
        cls,
        evidence: EvidenceItem,
        *,
        schema_version: str = EVIDENCE_SCHEMA_VERSION,
    ) -> "EvidenceContract":
        """Construct an external evidence contract from a canonical EvidenceItem."""
        _announce("Creating evidence contract from domain")
        if not isinstance(evidence, EvidenceItem):
            _raise_contract_validation(
                "evidence must be an EvidenceItem instance.",
                contract=_CONTRACT,
                field="evidence",
                context={"received_type": type(evidence).__name__},
            )

        provenance = evidence.provenance
        return cls(
            schema_version=schema_version,
            evidence_id=evidence.evidence_id,
            source_file_id=provenance.source.source_file_id,
            source_hash=provenance.content_hash.value,
            hash_algorithm=provenance.content_hash.algorithm,
            source_type=provenance.source.source_type,
            logical_location=(
                EvidenceLocationContract.from_domain(evidence.logical_location)
                if evidence.logical_location is not None
                else None
            ),
            extractor_name=provenance.extractor_name,
            extractor_version=provenance.extractor_version,
            extracted_at=provenance.extracted_at,
            source_timestamp=provenance.source_timestamp,
            original_filename=provenance.source.original_filename,
            source_version=provenance.source.source_version,
            traceability_refs=provenance.traceability_refs,
            extracted_value=evidence.extracted_value,
            confidence=evidence.confidence,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceContract":
        """Parse a strict versioned external evidence mapping."""
        _announce("Deserializing evidence contract")
        data = validate_contract_fields(
            payload,
            required=(
                "schema_version",
                "evidence_id",
                "source_file_id",
                "source_hash",
                "source_type",
                "extracted_at",
                "extracted_value",
            ),
            optional=(
                "hash_algorithm",
                "logical_location",
                "extractor_name",
                "extractor_version",
                "source_timestamp",
                "original_filename",
                "source_version",
                "traceability_refs",
                "confidence",
            ),
            contract=_CONTRACT,
        )
        return cls(
            schema_version=data["schema_version"],
            evidence_id=data["evidence_id"],
            source_file_id=data["source_file_id"],
            source_hash=data["source_hash"],
            hash_algorithm=data.get("hash_algorithm", "sha256"),
            source_type=data["source_type"],
            logical_location=data.get("logical_location"),
            extractor_name=data.get("extractor_name"),
            extractor_version=data.get("extractor_version"),
            extracted_at=data["extracted_at"],
            source_timestamp=data.get("source_timestamp"),
            original_filename=data.get("original_filename"),
            source_version=data.get("source_version"),
            traceability_refs=data.get("traceability_refs") or (),
            extracted_value=data["extracted_value"],
            confidence=data.get("confidence"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "EvidenceContract":
        """Decode canonical JSON and validate it as an EvidenceContract."""
        _announce("Decoding evidence contract JSON")
        try:
            data = canonical_json_loads(payload, contract=_CONTRACT)
        except ContractDeserializationError:
            raise
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Evidence JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


# Backward-compatible name retained from the initial scaffold.
ContractsEvidence = EvidenceContract


__all__ = [
    "EvidenceLocationContract",
    "EvidenceContract",
    "ContractsEvidence",
]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Evidence Contract Self-Test ===\n")
    printer.status("TEST", "Evidence contract module initialized", "info")

    digest = hashlib.sha256(b"bimap-evidence").hexdigest()
    contract = EvidenceContract(
        evidence_id="EV-0001",
        source_file_id="SRC-0001",
        source_hash=digest,
        source_type="json",
        logical_location={"path": "family.parameters.FireRating"},
        extractor_name="r3d-test",
        extractor_version="1.0.0",
        extracted_at="2026-09-01T00:00:00Z",
        extracted_value={"name": "FireRating", "present": False},
        confidence=1.0,
    )
    assert EvidenceContract.from_json(contract.to_json()) == contract
    assert EvidenceContract.from_domain(contract.to_domain()) == contract
    printer.status("PASS", "Evidence contract round trip", "success")

    print("\n=== Test ran successfully ===\n")
