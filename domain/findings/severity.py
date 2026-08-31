"""
Defines BIMAP finding severity semantics such as informational, low,
medium, high, and critical.

Severity captures the potential impact of a finding *if it is correct*.
It is deliberately independent from ``confidence.py``, which captures how
certain BIMAP is that the finding is correct in the first place. Keeping
the two axes separate lets callers reason about "how bad, if true" and
"how sure are we" without conflating them into a single ambiguous score.

Design note - dependency isolation
-----------------------------------
Per the frozen ``domain/findings`` dependency hierarchy, this module
imports nothing else from BIMAP. It sits at the base of the findings 
dependency graph, alongside ``confidence.py`` and ``evidence/provenance.py``:

    severity.py    -\\
    confidence.py   --+--> findings/models.py
    provenance.py  -/

Only ``findings/models.py`` is permitted to depend on ``severity.py``,
``confidence.py``, and ``provenance.py``; none of those three may depend
on each other or redefine one another's concepts. Keeping ``Severity`` a
pure, standard-library-only value object means it can be constructed and
validated without pulling in any other part of the BIMAP tree, and
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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, unique
from functools import total_ordering
from typing import Any

from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("Severity")
printer = PrettyPrinter()

@unique
class SeverityLevel(IntEnum):
    """
    Ordered severity levels recognized by BIMAP.

    Members are an ``IntEnum`` so levels are natively orderable and
    comparable (``SeverityLevel.LOW < SeverityLevel.HIGH``) while still
    retaining descriptive names for logging, storage, and serialization.
    """

    INFORMATIONAL = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        """
        Lowercase canonical label used in serialization (e.g. "high").
        """

        return self.name.lower()


_LABEL_TO_LEVEL: dict[str, SeverityLevel] = {
    level.label: level for level in SeverityLevel
}


# ----------------------------------------------------------------------
# Minimal local validation helpers.
# 
# ----------------------------------------------------------------------

def _coerce_level(value: Any, *, field_name: str = "level") -> SeverityLevel:
    """
    Coerce a raw value (``SeverityLevel``, ``str``, or ``int``) into a
    ``SeverityLevel``.

    Raises
    ------
    ValueError
        If ``value`` cannot be resolved to a known severity level.
    """

    if isinstance(value, SeverityLevel):
        return value

    if isinstance(value, bool):
        # bool is a subclass of int; reject explicitly so True/False are
        # never silently coerced into severity ordinals 0/1.
        raise ValueError(
            f"{field_name} must not be a bool (got {value!r})."
        )

    if isinstance(value, int):
        try:
            return SeverityLevel(value)
        except ValueError as exc:
            raise ValueError(
                f"Unrecognized severity level ordinal for {field_name}: "
                f"{value!r}. Allowed values: "
                f"{sorted(int(level) for level in SeverityLevel)}."
            ) from exc

    if isinstance(value, str):
        normalized = require_text(value, field=field_name).lower()
        resolved = _LABEL_TO_LEVEL.get(normalized)
        if resolved is None:
            raise ValueError(
                f"Unrecognized severity label for {field_name}: {value!r}. "
                f"Allowed values: {sorted(_LABEL_TO_LEVEL)}."
            )
        return resolved

    raise ValueError(
        f"{field_name} must be a SeverityLevel, str, or int, got "
        f"{type(value).__name__}."
    )


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class Severity:
    """
    Immutable severity assessment attached to a BIMAP finding.

    Parameters
    ----------
    level:
        The ordered severity classification. Accepts a ``SeverityLevel``,
        a case-insensitive label (``"high"``), or an ordinal ``int``.
    rationale:
        Optional free-text explanation for why the level was assigned
        (e.g. "financial impact exceeds materiality threshold"). Purely
        descriptive metadata; it never participates in ordering or
        equality.
    """

    level: SeverityLevel
    rationale: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", _coerce_level(self.level, field_name="level"))
        object.__setattr__(
            self,
            "rationale",
            optional_text(self.rationale, field="rationale"),
        )

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def informational(cls, rationale: str | None = None) -> "Severity":
        return cls(SeverityLevel.INFORMATIONAL, rationale=rationale)

    @classmethod
    def low(cls, rationale: str | None = None) -> "Severity":
        return cls(SeverityLevel.LOW, rationale=rationale)

    @classmethod
    def medium(cls, rationale: str | None = None) -> "Severity":
        return cls(SeverityLevel.MEDIUM, rationale=rationale)

    @classmethod
    def high(cls, rationale: str | None = None) -> "Severity":
        return cls(SeverityLevel.HIGH, rationale=rationale)

    @classmethod
    def critical(cls, rationale: str | None = None) -> "Severity":
        return cls(SeverityLevel.CRITICAL, rationale=rationale)

    @classmethod
    def from_label(cls, label: str, *, rationale: str | None = None) -> "Severity":
        """
        Construct a Severity from its case-insensitive string label
        (e.g. ``"high"``).
        """

        return cls(_coerce_level(label, field_name="level"), rationale=rationale)

    # ------------------------------------------------------------------
    # Comparisons - ordered strictly by level; rationale never compared.
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.level == other.level

    def __lt__(self, other: "Severity") -> bool:
        if not isinstance(other, Severity):
            return NotImplemented
        return self.level < other.level

    def __hash__(self) -> int:
        return hash(self.level)

    def is_at_least(self, other: "Severity") -> bool:
        """
        Return whether this severity meets or exceeds ``other``.
        """

        if not isinstance(other, Severity):
            raise ValueError(
                f"is_at_least() requires a Severity instance, got "
                f"{type(other).__name__}."
            )
        return self.level >= other.level

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return a primitive representation of the severity.
        """

        return {
            "level": self.level.label,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "Severity":
        """
        Reconstruct a Severity from its canonical internal representation.

        Raises
        ------
        ValueError
            If ``value`` is not a mapping or contains an invalid level.
        """

        data = require_mapping(value, field="severity")
        return cls(
            level=data.get("level"),
            rationale=data.get("rationale"),
        )

    def __str__(self) -> str:
        return self.level.label

    def __repr__(self) -> str:
        return f"Severity(level={self.level.label!r}, rationale={self.rationale!r})"


__all__ = [
    "SeverityLevel",
    "Severity",
]