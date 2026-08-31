"""
Canonical BIMAP evidence provenance.

This module defines source identity, source integrity, extraction/version
metadata, timestamps, and upstream traceability references.

Dependency direction
--------------------
domain.utils
    ↑
provenance.py

This module must not import models.py or project_evidence.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """
    Stable identity of one source object supplied to BIMAP.

    ``source_file_id`` is the authoritative opaque internal identifier.

    ``original_filename`` is retained only as descriptive provenance metadata;
    it must not be used as source identity because filenames are user-controlled
    and are neither unique nor stable.
    """

    source_file_id: str
    source_type: str

    original_filename: str | None = None
    source_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_file_id",
            require_text(
                self.source_file_id,
                field="source_file_id",
            ),
        )

        object.__setattr__(
            self,
            "source_type",
            require_text(
                self.source_type,
                field="source_type",
            ),
        )

        object.__setattr__(
            self,
            "original_filename",
            optional_text(
                self.original_filename,
                field="original_filename",
            ),
        )

        object.__setattr__(
            self,
            "source_version",
            optional_text(
                self.source_version,
                field="source_version",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Return a primitive representation of the source identity.
        """

        return {
            "source_file_id": self.source_file_id,
            "source_type": self.source_type,
            "original_filename": self.original_filename,
            "source_version": self.source_version,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Any,
    ) -> "SourceIdentity":
        """
        Construct SourceIdentity from an internal mapping.
        """

        data = require_mapping(
            payload,
            field="source",
        )

        return cls(
            source_file_id=data.get("source_file_id"),
            source_type=data.get("source_type"),
            original_filename=data.get("original_filename"),
            source_version=data.get("source_version"),
        )


@dataclass(frozen=True, slots=True)
class ContentHash:
    """
    Validated fixed-length cryptographic source-content hash.

    SHA-256 is the default because BIMAP requires stable evidence fingerprints;
    alternative fixed-length algorithms supported by hashlib remain possible.
    """

    value: str
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        normalized_algorithm = normalize_hash_algorithm(
            self.algorithm,
        )

        normalized_value = normalize_hex_digest(
            self.value,
            algorithm=normalized_algorithm,
            field="source_hash",
        )

        object.__setattr__(
            self,
            "algorithm",
            normalized_algorithm,
        )

        object.__setattr__(
            self,
            "value",
            normalized_value,
        )

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        algorithm: str = "sha256",
    ) -> "ContentHash":
        """
        Build a validated content hash from source bytes.
        """

        normalized_algorithm = normalize_hash_algorithm(
            algorithm,
        )

        return cls(
            value=digest_bytes(
                data,
                algorithm=normalized_algorithm,
            ),
            algorithm=normalized_algorithm,
        )

    def verify(
        self,
        data: bytes | bytearray | memoryview,
    ) -> bool:
        """
        Verify source bytes against this hash.
        """

        return verify_digest(
            data,
            expected_digest=self.value,
            algorithm=self.algorithm,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "value": self.value,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Any,
    ) -> "ContentHash":
        data = require_mapping(
            payload,
            field="content_hash",
        )

        return cls(
            value=data.get("value"),
            algorithm=data.get(
                "algorithm",
                "sha256",
            ),
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    """
    Provenance record associated with canonical BIMAP evidence.

    Responsibilities
    ----------------
    - source identity;
    - cryptographic source integrity;
    - source timestamp where available;
    - extraction timestamp;
    - extractor identity/version;
    - evidence schema version where available;
    - upstream traceability references.

    Logical page/row/element/path location is deliberately not stored here.
    One source may yield many evidence items at different logical locations,
    so that information belongs to EvidenceItem.
    """

    source: SourceIdentity
    content_hash: ContentHash

    extracted_at: datetime = field(
        default_factory=utc_now,
    )

    source_timestamp: datetime | None = None

    extractor_name: str | None = None
    extractor_version: str | None = None
    schema_version: str | None = None

    traceability_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(
            self.source,
            SourceIdentity,
        ):
            raise DomainValidationError(
                "source must be a SourceIdentity instance.",
                field="source",
                context={
                    "received_type": type(self.source).__name__,
                },
            )

        if not isinstance(
            self.content_hash,
            ContentHash,
        ):
            raise DomainValidationError(
                "content_hash must be a ContentHash instance.",
                field="content_hash",
                context={
                    "received_type": type(
                        self.content_hash
                    ).__name__,
                },
            )

        object.__setattr__(
            self,
            "extracted_at",
            ensure_utc_datetime(
                self.extracted_at,
                field="extracted_at",
            ),
        )

        if self.source_timestamp is not None:
            object.__setattr__(
                self,
                "source_timestamp",
                ensure_utc_datetime(
                    self.source_timestamp,
                    field="source_timestamp",
                ),
            )

        object.__setattr__(
            self,
            "extractor_name",
            optional_text(
                self.extractor_name,
                field="extractor_name",
            ),
        )

        object.__setattr__(
            self,
            "extractor_version",
            optional_text(
                self.extractor_version,
                field="extractor_version",
            ),
        )

        object.__setattr__(
            self,
            "schema_version",
            optional_text(
                self.schema_version,
                field="schema_version",
            ),
        )

        object.__setattr__(
            self,
            "traceability_refs",
            stable_unique_text(
                self.traceability_refs,
                field="traceability_refs",
            ),
        )

    # ------------------------------------------------------------------
    # Existing placeholder responsibilities, now concretely implemented
    # ------------------------------------------------------------------

    def evidence_origin(self) -> str:
        """
        Return the opaque source identifier from which evidence originated.
        """

        return self.source.source_file_id

    def source_identity(self) -> SourceIdentity:
        """
        Return the complete immutable source identity.
        """

        return self.source

    def hashes(self) -> dict[str, str]:
        """
        Return all recorded content hashes keyed by algorithm.

        The current provenance model stores one authoritative source digest.
        Returning a mapping leaves the method semantically extensible without
        duplicating source_hash fields elsewhere.
        """

        return {
            self.content_hash.algorithm:
                self.content_hash.value,
        }

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def verify_source_bytes(
        self,
        data: bytes | bytearray | memoryview,
    ) -> bool:
        """
        Return whether supplied bytes match the recorded source hash.
        """

        return self.content_hash.verify(data)

    def assert_source_integrity(
        self,
        data: bytes | bytearray | memoryview,
    ) -> None:
        """
        Validate source bytes and raise a structured integrity error on mismatch.
        """

        if self.verify_source_bytes(data):
            return

        raise ProvenanceIntegrityError(
            "Source content does not match the recorded provenance hash.",
            field="source_hash",
            context={
                "source_file_id":
                    self.source.source_file_id,
                "hash_algorithm":
                    self.content_hash.algorithm,
            },
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return the canonical internal provenance representation.
        """

        return {
            "source": self.source.to_dict(),
            "content_hash": self.content_hash.to_dict(),
            "source_timestamp": (
                format_utc_datetime(
                    self.source_timestamp
                )
                if self.source_timestamp is not None
                else None
            ),
            "extracted_at":
                format_utc_datetime(self.extracted_at),
            "extractor_name":
                self.extractor_name,
            "extractor_version":
                self.extractor_version,
            "schema_version":
                self.schema_version,
            "traceability_refs":
                list(self.traceability_refs),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Any,
    ) -> "Provenance":
        """
        Reconstruct provenance from its canonical internal representation.
        """

        data = require_mapping(
            payload,
            field="provenance",
        )

        return cls(
            source=SourceIdentity.from_dict(
                data.get("source"),
            ),
            content_hash=ContentHash.from_dict(
                data.get("content_hash"),
            ),
            source_timestamp=data.get(
                "source_timestamp"
            ),
            extracted_at=data.get(
                "extracted_at",
                utc_now(),
            ),
            extractor_name=data.get(
                "extractor_name"
            ),
            extractor_version=data.get(
                "extractor_version"
            ),
            schema_version=data.get(
                "schema_version"
            ),
            traceability_refs=tuple(
                data.get("traceability_refs") or ()
            ),
        )


__all__ = [
    "SourceIdentity",
    "ContentHash",
    "Provenance",
]