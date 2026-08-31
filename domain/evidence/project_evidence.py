"""
Canonical project-level BIM evidence aggregate.

ProjectEvidence represents project-scoped evidence after ingestion and
normalization.

Architectural boundary
----------------------
audit_engine/ingestion
        ↓
audit_engine/normalization
        ↓
domain/evidence/ProjectEvidence

ProjectEvidence does not perform ingestion or normalization itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .models import EvidenceItem
from .provenance import Provenance


@dataclass(frozen=True, slots=True)
class ProjectEvidence:
    """
    Canonical project-scoped aggregate of normalized BIMAP evidence.

    Aggregate invariants
    --------------------
    1. project_id must be present.
    2. every member must be an EvidenceItem.
    3. evidence_id values must be unique.
    4. one source_file_id must resolve to one content hash.
    5. one source_file_id must resolve to one source type.

    The aggregate is immutable. Operations such as ``add`` and ``extend``
    return new validated ProjectEvidence instances.
    """

    project_id: str

    evidence_items: tuple[EvidenceItem, ...] = ()

    assembled_at: datetime = field(
        default_factory=utc_now,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            require_text(
                self.project_id,
                field="project_id",
            ),
        )

        object.__setattr__(
            self,
            "assembled_at",
            ensure_utc_datetime(
                self.assembled_at,
                field="assembled_at",
            ),
        )

        try:
            normalized_items = tuple(
                self.evidence_items
            )

        except TypeError as exc:
            raise DomainValidationError(
                "evidence_items must be an iterable of EvidenceItem values.",
                field="evidence_items",
                context={
                    "received_type":
                        type(
                            self.evidence_items
                        ).__name__,
                },
            ) from exc

        for index, item in enumerate(
            normalized_items
        ):
            if not isinstance(
                item,
                EvidenceItem,
            ):
                raise DomainValidationError(
                    "ProjectEvidence accepts EvidenceItem values only.",
                    field=(
                        f"evidence_items[{index}]"
                    ),
                    context={
                        "received_type":
                            type(item).__name__,
                    },
                )

        self._assert_unique_evidence_ids(
            normalized_items
        )

        self._assert_source_identity_consistency(
            normalized_items
        )

        object.__setattr__(
            self,
            "evidence_items",
            normalized_items,
        )

    # ------------------------------------------------------------------
    # Aggregate invariants
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_unique_evidence_ids(
        items: tuple[EvidenceItem, ...],
    ) -> None:
        """
        Ensure evidence identifiers are unique within one project aggregate.
        """

        seen: set[str] = set()
        duplicates: set[str] = set()

        for item in items:
            if item.evidence_id in seen:
                duplicates.add(
                    item.evidence_id
                )

            seen.add(
                item.evidence_id
            )

        if not duplicates:
            return

        raise DuplicateEvidenceError(
            "Project evidence contains duplicate evidence identifiers.",
            field="evidence_items",
            context={
                "evidence_ids":
                    sorted(duplicates),
            },
        )

    @staticmethod
    def _assert_source_identity_consistency(
        items: tuple[EvidenceItem, ...],
    ) -> None:
        """
        Ensure one source_file_id represents one immutable source object.

        If source content changes, the upload/storage layer should issue a new
        source_file_id rather than silently reusing the old source identity.
        """

        seen: dict[
            str,
            tuple[str, str, str],
        ] = {}

        for item in items:
            source = item.provenance.source

            signature = (
                item.provenance.content_hash.algorithm,
                item.provenance.content_hash.value,
                source.source_type,
            )

            previous = seen.get(
                source.source_file_id
            )

            if previous is None:
                seen[
                    source.source_file_id
                ] = signature
                continue

            if previous == signature:
                continue

            raise EvidenceIntegrityError(
                "One source_file_id resolves to inconsistent source provenance.",
                field="source_file_id",
                context={
                    "source_file_id":
                        source.source_file_id,
                },
            )

    # ------------------------------------------------------------------
    # Aggregate summary
    # ------------------------------------------------------------------

    @property
    def evidence_count(self) -> int:
        """
        Number of evidence items in the aggregate.
        """

        return len(self.evidence_items)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """
        Ordered evidence identifiers.
        """

        return tuple(
            item.evidence_id
            for item in self.evidence_items
        )

    @property
    def source_file_ids(self) -> tuple[str, ...]:
        """
        Ordered unique source-file identifiers.
        """

        seen: set[str] = set()
        ordered: list[str] = []

        for item in self.evidence_items:
            source_file_id = (
                item.source_file_id
            )

            if source_file_id in seen:
                continue

            seen.add(source_file_id)
            ordered.append(source_file_id)

        return tuple(ordered)

    @property
    def source_count(self) -> int:
        """
        Number of distinct source files represented by this aggregate.
        """

        return len(
            self.source_file_ids
        )

    # ------------------------------------------------------------------
    # Evidence lookup
    # ------------------------------------------------------------------

    def get(
        self,
        evidence_id: str,
    ) -> EvidenceItem | None:
        """
        Return evidence by ID or None when absent.
        """

        normalized_id = require_text(
            evidence_id,
            field="evidence_id",
        )

        for item in self.evidence_items:
            if (
                item.evidence_id
                == normalized_id
            ):
                return item

        return None

    def require(
        self,
        evidence_id: str,
    ) -> EvidenceItem:
        """
        Return required evidence or raise EvidenceNotFoundError.
        """

        normalized_id = require_text(
            evidence_id,
            field="evidence_id",
        )

        item = self.get(
            normalized_id
        )

        if item is not None:
            return item

        raise EvidenceNotFoundError(
            "Evidence identifier is not present in this project aggregate.",
            field="evidence_id",
            context={
                "project_id":
                    self.project_id,
                "evidence_id":
                    normalized_id,
            },
        )

    def for_source(
        self,
        source_file_id: str,
    ) -> tuple[EvidenceItem, ...]:
        """
        Return all evidence originating from one source object.
        """

        normalized_source_id = require_text(
            source_file_id,
            field="source_file_id",
        )

        return tuple(
            item
            for item in self.evidence_items
            if (
                item.source_file_id
                == normalized_source_id
            )
        )

    def for_source_type(
        self,
        source_type: str,
    ) -> tuple[EvidenceItem, ...]:
        """
        Return evidence matching a source classification.

        Comparison is case-insensitive, while the stored source type is
        preserved exactly as normalized.
        """

        normalized_type = require_text(
            source_type,
            field="source_type",
        )

        key = normalized_type.casefold()

        return tuple(
            item
            for item in self.evidence_items
            if (
                item.source_type.casefold()
                == key
            )
        )

    def source_provenance(
        self,
    ) -> tuple[Provenance, ...]:
        """
        Return one provenance record for each unique source.

        Source order follows first occurrence in the evidence aggregate.
        """

        seen: set[str] = set()

        result: list[Provenance] = []

        for item in self.evidence_items:
            source_file_id = (
                item.source_file_id
            )

            if source_file_id in seen:
                continue

            seen.add(source_file_id)

            result.append(
                item.provenance
            )

        return tuple(result)

    # ------------------------------------------------------------------
    # Immutable aggregate operations
    # ------------------------------------------------------------------

    def add(
        self,
        item: EvidenceItem,
    ) -> "ProjectEvidence":
        """
        Return a new aggregate containing one additional evidence item.

        All invariants are revalidated through the dataclass constructor.
        """

        if not isinstance(
            item,
            EvidenceItem,
        ):
            raise DomainValidationError(
                "item must be an EvidenceItem.",
                field="item",
                context={
                    "received_type":
                        type(item).__name__,
                },
            )

        return replace(
            self,
            evidence_items=(
                self.evidence_items
                + (item,)
            ),
            assembled_at=utc_now(),
        )

    def extend(
        self,
        items: tuple[EvidenceItem, ...]
        | list[EvidenceItem],
    ) -> "ProjectEvidence":
        """
        Return a new aggregate containing additional evidence items.

        Duplicate IDs and source-integrity conflicts are detected by the
        reconstructed aggregate.
        """

        try:
            additions = tuple(items)

        except TypeError as exc:
            raise DomainValidationError(
                "items must be an iterable of EvidenceItem values.",
                field="items",
                context={
                    "received_type":
                        type(items).__name__,
                },
            ) from exc

        if not additions:
            return self

        return replace(
            self,
            evidence_items=(
                self.evidence_items
                + additions
            ),
            assembled_at=utc_now(),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return BIMAP's canonical internal project-evidence representation.
        """

        return {
            "project_id":
                self.project_id,

            "assembled_at":
                format_utc_datetime(
                    self.assembled_at
                ),

            "evidence_items": [
                item.to_dict()
                for item
                in self.evidence_items
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: Any,
    ) -> "ProjectEvidence":
        """
        Reconstruct ProjectEvidence from the internal canonical representation.
        """

        data = require_mapping(
            payload,
            field="project_evidence",
        )

        raw_items = (data.get("evidence_items") or ())

        if (isinstance(raw_items, (str, bytes, bytearray))
            or not hasattr(raw_items, "__iter__",)):
            raise DomainValidationError(
                "evidence_items must be an iterable of evidence objects.",
                field="evidence_items",
                context={
                    "received_type":
                        type(raw_items).__name__,
                },
            )

        return cls(
            project_id=data.get("project_id"),
            assembled_at=data.get("assembled_at", utc_now(),),
            evidence_items=tuple(EvidenceItem.from_dict(item) for item in raw_items))


__all__ = [
    "ProjectEvidence",
]