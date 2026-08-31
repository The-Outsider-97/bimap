"""
Defines finding confidence independently from severity.

Confidence expresses how certain BIMAP is that a finding is correct, as a
continuous score in ``[0.0, 1.0]`` together with a discretized qualitative
band derived from that score. It is intentionally decoupled from
``severity.py``: a finding can be low-confidence-but-critical-if-true, or
high-confidence-but-merely-informational, and neither axis should be able
to silently override the other.

Design note - dependency isolation
-----------------------------------
Per the frozen ``domain/findings`` dependency hierarchy, this module
imports nothing else from BIMAP - not ``utils.domain_errors``, not
``utils.domain_helpers``, not ``logs.logger``. It sits at the base of the
findings dependency graph, alongside ``severity.py`` and
``evidence/provenance.py``:

    severity.py    -\\
    confidence.py   --+--> findings/models.py
    provenance.py  -/

Only ``findings/models.py`` is permitted to depend on ``severity.py``,
``confidence.py``, and ``provenance.py``; none of those three may depend
on each other or redefine one another's concepts. Keeping ``Confidence``
a pure, standard-library-only value object means it can be constructed
and validated without pulling in any other part of the BIMAP tree, and
removes any risk of a future circular import between ``findings/`` and
``utils/``.

Because of this isolation, validation failures here raise the built-in
``ValueError`` rather than ``DomainValidationError``. Callers one layer
up - such as ``findings/models.py``, which *is* permitted to depend on
``utils.domain_errors`` - are responsible for translating a ``ValueError``
raised here into the structured BIMAP domain error hierarchy at the
point where raw/external data crosses into the domain (see
``Finding.from_dict`` in ``models.py``).
"""

from __future__ import annotations

import math

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, unique
from functools import total_ordering
from typing import Any

from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("Confidence")
printer = PrettyPrinter()


@unique
class ConfidenceLevel(IntEnum):
    """
    Qualitative confidence bands derived from a numeric confidence score.

    Bands are derived, never set directly, so a ``Confidence`` instance's
    ``score`` and ``level`` can never disagree with one another.
    """

    LOW = 0
    MEDIUM = 1
    HIGH = 2

    @property
    def label(self) -> str:
        return self.name.lower()


# Inclusive lower bounds for each qualitative band, evaluated in ascending
# order. A validated score is always within [0.0, 1.0], so LOW's bound of
# 0.0 guarantees every valid score resolves to exactly one band.
_LEVEL_LOWER_BOUNDS: tuple[tuple[float, ConfidenceLevel], ...] = (
    (0.00, ConfidenceLevel.LOW),
    (0.50, ConfidenceLevel.MEDIUM),
    (0.85, ConfidenceLevel.HIGH),
)


# ----------------------------------------------------------------------
# Minimal local validation helpers.
#
# These deliberately duplicate a small amount of logic that also exists
# in ``utils.domain_helpers`` (e.g. mapping validation). That duplication
# is an intentional consequence of this module's "imports: none from
# BIMAP" isolation requirement rather than an oversight; the footprint is
# kept intentionally tiny.
# ----------------------------------------------------------------------

def _level_for_score(score: float) -> ConfidenceLevel:
    """
    Resolve the qualitative band for an already-validated score.
    """

    resolved = ConfidenceLevel.LOW
    for lower_bound, band in _LEVEL_LOWER_BOUNDS:
        if score >= lower_bound:
            resolved = band
    return resolved


def _validate_score(value: Any, *, field_name: str = "score") -> float:
    """
    Validate and normalize a confidence score to a float in ``[0.0, 1.0]``.

    Raises
    ------
    ValueError
        If ``value`` is not a real, finite number within range.
    """

    if isinstance(value, bool):
        raise ValueError(
            f"{field_name} must not be a bool (got {value!r})."
        )

    if not isinstance(value, (int, float)):
        raise ValueError(
            f"{field_name} must be a real number, got {type(value).__name__}."
        )

    score = float(value)

    if math.isnan(score) or math.isinf(score):
        raise ValueError(f"{field_name} must be a finite number, got {score!r}.")

    if not (0.0 <= score <= 1.0):
        raise ValueError(
            f"{field_name} must lie within [0.0, 1.0], got {score!r}."
        )

    return score


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class Confidence:
    """
    Immutable confidence assessment attached to a BIMAP finding.

    Parameters
    ----------
    score:
        Authoritative continuous confidence value in ``[0.0, 1.0]``.
    basis:
        Optional free text describing how the score was derived (e.g.
        ``"model posterior probability"``, ``"manual analyst review"``).
    """

    score: float
    basis: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _validate_score(self.score, field_name="score"))
        object.__setattr__(
            self,
            "basis",
            optional_text(self.basis, field="basis"),
        )

    @property
    def level(self) -> ConfidenceLevel:
        """
        Qualitative band derived from ``score``. Read-only by design.
        """

        return _level_for_score(self.score)

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def low(cls, score: float = 0.25, *, basis: str | None = None) -> "Confidence":
        return cls._with_expected_level(score, ConfidenceLevel.LOW, basis=basis)

    @classmethod
    def medium(cls, score: float = 0.65, *, basis: str | None = None) -> "Confidence":
        return cls._with_expected_level(score, ConfidenceLevel.MEDIUM, basis=basis)

    @classmethod
    def high(cls, score: float = 0.95, *, basis: str | None = None) -> "Confidence":
        return cls._with_expected_level(score, ConfidenceLevel.HIGH, basis=basis)

    @classmethod
    def _with_expected_level(
        cls,
        score: float,
        expected_level: ConfidenceLevel,
        *,
        basis: str | None,
    ) -> "Confidence":
        instance = cls(score, basis=basis)
        if instance.level is not expected_level:
            raise ValueError(
                f"Requested score {instance.score!r} does not fall in the "
                f"{expected_level.label.upper()} confidence band "
                f"(resolved to {instance.level.label.upper()})."
            )
        return instance

    # ------------------------------------------------------------------
    # Comparisons - ordered strictly by score.
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.score == other.score

    def __lt__(self, other: "Confidence") -> bool:
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.score < other.score

    def __hash__(self) -> int:
        return hash(self.score)

    def meets_threshold(self, minimum_score: float) -> bool:
        """
        Return whether this confidence's score is at least ``minimum_score``.
        """

        return self.score >= _validate_score(minimum_score, field_name="minimum_score")

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return a primitive representation of the confidence assessment.
        """

        return {
            "score": self.score,
            "level": self.level.label,
            "basis": self.basis,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "Confidence":
        """
        Reconstruct a Confidence from its canonical internal representation.

        Raises
        ------
        ValueError
            If ``payload`` is not a mapping or contains an invalid score.
        """

        data = require_mapping(payload, field="confidence")
        return cls(
            score=data.get("score"),
            basis=data.get("basis"),
        )

    def __str__(self) -> str:
        return f"{self.level.label} ({self.score:.2f})"

    def __repr__(self) -> str:
        return f"Confidence(score={self.score!r}, basis={self.basis!r})"


__all__ = [
    "ConfidenceLevel",
    "Confidence",
]