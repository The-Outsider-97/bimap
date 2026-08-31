"""
Canonical internal BIMAP evidence entities.

This module defines the smallest normalized evidence unit used throughout
BIMAP after ingestion/normalization.

Dependency direction
--------------------
domain.utils
      ↑
provenance.py
      ↑
models.py

models.py must never import project_evidence.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .provenance import Provenance


@dataclass(frozen=True, slots=True)
class LogicalLocation:
    """
    Logical location of an evidence item inside its source.

    BIMAP's implementation specification identifies logical source locations
    through page, row, element, or path references. Multiple dimensions may
    be populated where useful.

    The domain does not impose a zero-based or one-based indexing convention
    beyond requiring numeric indices to be non-negative; ingestion adapters
    preserve and document the source convention.
    """

    page: int | None = None
    row: int | None = None

    element: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "page",
            "row",
        ):
            value = getattr(
                self,
                field_name,
            )

            if value is None:
                continue

            # bool is a subclass of int in Python and must therefore
            # be explicitly excluded.
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
            ):
                raise DomainValidationError(
                    "Location index must be an integer.",
                    field=field_name,
                    context={
                        "received_type":
                            type(value).__name__,
                    },
                )

            if value < 0:
                raise DomainValidationError(
                    "Location index must be non-negative.",
                    field=field_name,
                    context={
                        "received": value,
                    },
                )

        object.__setattr__(
            self,
            "element",
            optional_text(
                self.element,
                field="element",
            ),
        )

        object.__setattr__(
            self,
            "path",
            optional_text(
                self.path,
                field="path",
            ),
        )

        if (
            self.page is None
            and self.row is None
            and self.element is None
            and self.path is None
        ):
            raise DomainValidationError(
                "LogicalLocation must contain at least one locator.",
                field="logical_location",
            )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize only populated location dimensions.
        """

        payload: dict[str, Any] = {}

        if self.page is not None:
            payload["page"] = self.page

        if self.row is not None:
            payload["row"] = self.row

        if self.element is not None:
            payload["element"] = self.element

        if self.path is not None:
            payload["path"] = self.path

        return payload

    @classmethod
    def from_dict(
        cls,
        payload: Any,
    ) -> "LogicalLocation":
        data = require_mapping(
            payload,
            field="logical_location",
        )

        return cls(
            page=data.get("page"),
            row=data.get("row"),
            element=data.get("element"),
            path=data.get("path"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """
    Canonical internal BIMAP evidence object.

    A finding should ultimately reference one or more stable ``evidence_id``
    values. Each evidence item preserves:

    - its stable evidence identifier;
    - source provenance;
    - logical location within that source;
    - the extracted/observed value;
    - extraction confidence where probabilistic extraction was involved.

    Severity is deliberately absent: severity belongs to findings, not
    evidence.
    """

    evidence_id: str
    provenance: Provenance

    extracted_value: Any = None

    logical_location: LogicalLocation | None = None

    confidence: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            require_text(
                self.evidence_id,
                field="evidence_id",
            ),
        )

        if not isinstance(
            self.provenance,
            Provenance,
        ):
            raise DomainValidationError(
                "provenance must be a Provenance instance.",
                field="provenance",
                context={
                    "received_type":
                        type(self.provenance).__name__,
                },
            )

        if (
            self.logical_location is not None
            and not isinstance(
                self.logical_location,
                LogicalLocation,
            )
        ):
            raise DomainValidationError(
                "logical_location must be a LogicalLocation instance or None.",
                field="logical_location",
                context={
                    "received_type":
                        type(
                            self.logical_location
                        ).__name__,
                },
            )

        # Make nested evidence values immutable and deterministic.
        object.__setattr__(
            self,
            "extracted_value",
            freeze_json_value(
                self.extracted_value,
                field=(
                    f"evidence[{self.evidence_id}]"
                    ".extracted_value"
                ),
            ),
        )

        object.__setattr__(
            self,
            "confidence",
            normalize_probability(
                self.confidence,
                field="confidence",
                allow_none=True,
            ),
        )

    # ------------------------------------------------------------------
    # Provenance convenience properties
    # ------------------------------------------------------------------

    @property
    def source_file_id(self) -> str:
        """
        Opaque source-file identifier.

        The value remains stored solely in Provenance.SourceIdentity.
        """

        return self.provenance.source.source_file_id

    @property
    def source_type(self) -> str:
        """
        Source classification associated with this evidence.
        """

        return self.provenance.source.source_type

    @property
    def source_hash(self) -> str:
        """
        Cryptographic source-content digest.
        """

        return self.provenance.content_hash.value

    @property
    def hash_algorithm(self) -> str:
        """
        Algorithm used for the source-content digest.
        """

        return self.provenance.content_hash.algorithm

    @property
    def extractor_version(self) -> str | None:
        """
        Version of the extractor that produced the evidence, if known.
        """

        return self.provenance.extractor_version

    @property
    def has_extraction_confidence(self) -> bool:
        """
        Return whether extraction supplied an explicit confidence value.

        Absence of confidence does not imply confidence=1.0; it means the
        extraction process did not supply a probabilistic confidence measure.
        """

        return self.confidence is not None

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def assert_source_integrity(
        self,
        data: bytes | bytearray | memoryview,
    ) -> None:
        """
        Verify source bytes against this item's provenance.
        """

        self.provenance.assert_source_integrity(data)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return BIMAP's canonical internal evidence representation.

        Provenance remains nested intentionally.

        External representations such as ``findings.json`` or the future
        Revit exporter contract should be created in ``contracts/`` rather
        than turning this domain object into an external API schema.
        """

        return {
            "evidence_id":
                self.evidence_id,

            "provenance":
                self.provenance.to_dict(),

            "logical_location": (
                self.logical_location.to_dict()
                if self.logical_location is not None
                else None
            ),

            "extracted_value":
                thaw_json_value(
                    self.extracted_value
                ),

            "confidence":
                self.confidence,
        }

    @classmethod
    def from_dict(
        cls,
        payload: Any,
    ) -> "EvidenceItem":
        """
        Reconstruct EvidenceItem from its canonical internal representation.
        """

        data = require_mapping(
            payload,
            field="evidence",
        )

        logical_location = data.get(
            "logical_location"
        )

        return cls(
            evidence_id=data.get(
                "evidence_id"
            ),

            provenance=Provenance.from_dict(
                data.get("provenance")
            ),

            logical_location=(
                LogicalLocation.from_dict(
                    logical_location
                )
                if logical_location is not None
                else None
            ),

            extracted_value=data.get(
                "extracted_value"
            ),

            confidence=data.get(
                "confidence"
            ),
        )


__all__ = [
    "LogicalLocation",
    "EvidenceItem",
]