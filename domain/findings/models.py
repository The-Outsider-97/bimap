"""
Canonical BIMAP Finding domain representation.

This module defines two cooperating types:

``Finding``
    A single immutable finding: what was found, how severe it would be
    if true (``Severity``), how certain BIMAP is that it is true
    (``Confidence``), and where it came from (``Provenance``).

``ModelFindings``
    The aggregate root that owns the collection of ``Finding`` objects
    produced by a BIMAP model run. It enforces identifier uniqueness and
    provides lookup/filtering/serialization over the collection so that
    no caller needs to reach into a raw dict of findings directly.

Dependency direction
---------------------
Per the frozen ``domain/findings`` dependency hierarchy, ``severity.py``,
``confidence.py``, and ``evidence/provenance.py`` are leaf modules with no
BIMAP-internal dependencies of their own; only this module depends on all
three, and it must not redefine what severity or confidence mean:

    severity.py    -\\
    confidence.py   --+--> findings/models.py
    provenance.py  -/

Because ``Severity`` and ``Confidence`` are deliberately isolated from
``utils.domain_errors``, they raise the built-in ``ValueError`` on
invalid input rather than ``DomainValidationError``. This module *is*
permitted to depend on ``utils.domain_errors``, so it is the boundary
that translates a ``ValueError`` raised while reconstructing a Severity
or Confidence from raw/external data (see ``Finding.from_dict``) into the
structured BIMAP domain error hierarchy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .severity import *
from .confidence import *
from ..evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("Models Findings")
printer = PrettyPrinter()


@dataclass(frozen=True, slots=True)
class Finding:
    """
    Immutable canonical representation of a single BIMAP finding.

    Parameters
    ----------
    finding_id:
        Stable, unique identifier for this finding within its aggregate.
    title:
        Short human-readable summary of the finding.
    severity:
        Impact classification, independent of confidence.
    confidence:
        Certainty classification, independent of severity.
    provenance:
        Evidence provenance this finding was derived from.
    description:
        Optional longer-form explanation of the finding.
    created_at:
        UTC timestamp the finding was created. Defaults to now.
    tags:
        Optional free-form labels for categorization/filtering.
    """

    finding_id: str
    title: str
    severity: Severity
    confidence: Confidence
    provenance: Provenance

    description: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "finding_id",
            require_text(self.finding_id, field="finding_id"),
        )
        object.__setattr__(
            self,
            "title",
            require_text(self.title, field="title"),
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, field="description"),
        )

        if not isinstance(self.severity, Severity):
            raise DomainValidationError(
                "severity must be a Severity instance.",
                field="severity",
                context={"received_type": type(self.severity).__name__},
            )

        if not isinstance(self.confidence, Confidence):
            raise DomainValidationError(
                "confidence must be a Confidence instance.",
                field="confidence",
                context={"received_type": type(self.confidence).__name__},
            )

        if not isinstance(self.provenance, Provenance):
            raise DomainValidationError(
                "provenance must be a Provenance instance.",
                field="provenance",
                context={"received_type": type(self.provenance).__name__},
            )

        object.__setattr__(
            self,
            "created_at",
            ensure_utc_datetime(self.created_at, field="created_at"),
        )

        object.__setattr__(
            self,
            "tags",
            stable_unique_text(self.tags, field="tags"),
        )

        logger.debug(
            f"Finding constructed: finding_id={self.finding_id} "
            f"severity={self.severity.level.label} "
            f"confidence={self.confidence.level.label}"
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return the canonical internal representation of this finding.
        """

        return {
            "finding_id": self.finding_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.to_dict(),
            "confidence": self.confidence.to_dict(),
            "provenance": self.provenance.to_dict(),
            "created_at": format_utc_datetime(self.created_at),
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "Finding":
        """
        Reconstruct a Finding from its canonical internal representation.

        ``Severity`` and ``Confidence`` are dependency-isolated from the
        rest of BIMAP (see the module docstring) and therefore raise
        ``ValueError`` on invalid input. This method is the boundary that
        translates such a ``ValueError`` into a structured
        ``DomainValidationError`` so that failures raised anywhere while
        reconstructing a Finding are consistently domain errors.
        """

        data = require_mapping(payload, field="finding")

        try:
            severity = Severity.from_dict(data.get("severity"))
        except ValueError as exc:
            raise DomainValidationError(
                f"Invalid severity in finding payload: {exc}",
                field="severity",
            ) from exc

        try:
            confidence = Confidence.from_dict(data.get("confidence"))
        except ValueError as exc:
            raise DomainValidationError(
                f"Invalid confidence in finding payload: {exc}",
                field="confidence",
            ) from exc

        return cls(
            finding_id=data.get("finding_id"),
            title=data.get("title"),
            description=data.get("description"),
            severity=severity,
            confidence=confidence,
            provenance=Provenance.from_dict(data.get("provenance")),
            created_at=data.get("created_at", utc_now()),
            tags=tuple(data.get("tags") or ()),
        )

    def __str__(self) -> str:
        return (
            f"[{self.severity.level.label.upper()}] {self.title} "
            f"(confidence={self.confidence.level.label})"
        )


class ModelFindings:
    """
    Aggregate root managing the collection of ``Finding`` objects produced
    by a single BIMAP model run.

    Responsibilities
    ----------------
    - enforce finding-identifier uniqueness within the aggregate;
    - provide lookup, iteration, and filtering over the collection;
    - serialize/deserialize the aggregate as a whole.

    Notes
    -----
    Lookup-miss and duplicate-identifier failures here intentionally raise
    the generic ``DomainInvariantError`` / ``DomainValidationError`` rather
    than the ``Duplicate*``/``*NotFound`` subclasses defined in
    ``domain_errors.py``: those subclasses are explicitly scoped to
    *evidence* identifiers (see their docstrings), and a ``Finding`` is a
    distinct domain concept from an evidence item.
    """

    def __init__(self) -> None:
        self._findings: dict[str, Finding] = {}
        logger.info("Model Findings successfully initialized")

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, finding: Finding) -> None:
        """
        Add a finding to the aggregate.

        Raises
        ------
        DomainValidationError
            If ``finding`` is not a ``Finding`` instance.
        DomainInvariantError
            If a finding with the same ``finding_id`` already exists.
        """

        if not isinstance(finding, Finding):
            raise DomainValidationError(
                "add() requires a Finding instance.",
                field="finding",
                context={"received_type": type(finding).__name__},
            )

        if finding.finding_id in self._findings:
            raise DomainInvariantError(
                "A finding with this identifier already exists in the aggregate.",
                field="finding_id",
                context={"finding_id": finding.finding_id},
            )

        self._findings[finding.finding_id] = finding
        logger.debug(f"Finding added: finding_id={finding.finding_id}")

    def remove(self, finding_id: str) -> Finding:
        """
        Remove and return the finding identified by ``finding_id``.

        Raises
        ------
        DomainInvariantError
            If no finding with that identifier exists in the aggregate.
        """

        key = require_text(finding_id, field="finding_id")

        try:
            removed = self._findings.pop(key)
        except KeyError as exc:
            raise DomainInvariantError(
                "No finding with this identifier exists in the aggregate.",
                field="finding_id",
                context={"finding_id": key},
            ) from exc

        logger.debug(f"Finding removed: finding_id={key}")
        return removed

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, finding_id: str) -> Finding:
        """
        Return the finding identified by ``finding_id``.

        Raises
        ------
        DomainInvariantError
            If no finding with that identifier exists in the aggregate.
        """

        key = require_text(finding_id, field="finding_id")

        try:
            return self._findings[key]
        except KeyError as exc:
            raise DomainInvariantError(
                "No finding with this identifier exists in the aggregate.",
                field="finding_id",
                context={"finding_id": key},
            ) from exc

    def __contains__(self, finding_id: object) -> bool:
        if not isinstance(finding_id, str):
            return False
        return finding_id in self._findings

    def __iter__(self) -> Iterator[Finding]:
        return iter(self._findings.values())

    def __len__(self) -> int:
        return len(self._findings)

    def list_findings(self) -> tuple[Finding, ...]:
        """
        Return all findings in insertion order.
        """

        return tuple(self._findings.values())

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def filter_by_minimum_severity(self, minimum: Severity) -> tuple[Finding, ...]:
        """
        Return findings whose severity meets or exceeds ``minimum``.
        """

        if not isinstance(minimum, Severity):
            raise DomainValidationError(
                "filter_by_minimum_severity() requires a Severity instance.",
                field="minimum",
                context={"received_type": type(minimum).__name__},
            )

        return tuple(
            finding
            for finding in self._findings.values()
            if finding.severity.is_at_least(minimum)
        )

    def filter_by_minimum_confidence(self, minimum_score: float) -> tuple[Finding, ...]:
        """
        Return findings whose confidence score is at least ``minimum_score``.
        """

        return tuple(
            finding
            for finding in self._findings.values()
            if finding.confidence.meets_threshold(minimum_score)
        )

    def highest_severity(self) -> Severity | None:
        """
        Return the highest severity present in the aggregate, or ``None``
        if the aggregate is empty.
        """

        if not self._findings:
            return None

        return max(
            (finding.severity for finding in self._findings.values()),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return the canonical internal representation of the aggregate.
        """

        return {
            "findings": [finding.to_dict() for finding in self._findings.values()],
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ModelFindings":
        """
        Reconstruct a ModelFindings aggregate from its canonical internal
        representation.
        """

        data = require_mapping(payload, field="model_findings")

        aggregate = cls()
        for raw_finding in data.get("findings") or ():
            aggregate.add(Finding.from_dict(raw_finding))

        return aggregate

    def __repr__(self) -> str:
        return f"ModelFindings(count={len(self._findings)})"


__all__ = [
    "Finding",
    "ModelFindings",
]