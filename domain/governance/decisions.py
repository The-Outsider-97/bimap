"""
Canonical BIMAP governance decisions and append-only decision history.

This module owns the domain meaning of governance outcomes. It intentionally
contains no application-service, API, persistence, SLAI-runtime, or reporting
imports. The dependency direction is therefore one-way::

    domain/utils
        -> domain/governance/decisions.py
        -> domain/governance/review.py

Governance outcomes are deliberately separate from finding severity and
confidence. A decision records what BIMAP governance permits for a finding;
it does not alter the finding's underlying technical assessment.

The supported outcomes follow the BIMAP governance model:

``approved``
    The finding passed governance and may be released subject to any
    higher-level report/order policy.

``suppressed``
    The finding is deliberately withheld from customer-facing release while
    remaining in the auditable governance history.

``review_required``
    The finding is unresolved and requires human governance review before it
    may be released.

``blocked``
    Governance determined that the finding must not be released.

Decision history is append-only. Once a terminal decision exists, any later
terminal change must be explicitly recorded as an override with its own reason
code rather than mutating or deleting the prior decision.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("BIMAP Governance Decisions")
printer = PrettyPrinter()


class DecisionOutcome(str, Enum):
    """Canonical BIMAP governance outcomes for a finding."""

    APPROVED = "approved"
    SUPPRESSED = "suppressed"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"

    @property
    def is_terminal(self) -> bool:
        """Return whether the outcome closes the current review cycle."""

        return self is not DecisionOutcome.REVIEW_REQUIRED

    @property
    def permits_finding_release(self) -> bool:
        """Return whether the finding itself may be released."""

        return self is DecisionOutcome.APPROVED

    @property
    def suppresses_finding(self) -> bool:
        """Return whether the finding is intentionally withheld."""

        return self is DecisionOutcome.SUPPRESSED

    @property
    def blocks_finding_release(self) -> bool:
        """Return whether release must remain blocked pending/following review."""

        return self in {
            DecisionOutcome.REVIEW_REQUIRED,
            DecisionOutcome.BLOCKED,
        }

    @classmethod
    def parse(cls, value: Any, *, field_name: str = "outcome") -> "DecisionOutcome":
        """
        Normalize a governance outcome from an enum instance or canonical text.

        Hyphens and spaces are normalized to underscores so the report wording
        ``review-required`` and the canonical serialized value
        ``review_required`` resolve to the same domain outcome.
        """

        printer.status("GOVERNANCE", "Parsing governance decision outcome", "info")

        if isinstance(value, cls):
            return value

        if not isinstance(value, str):
            raise DomainValidationError(
                "Governance outcome must be a DecisionOutcome or string.",
                field=field_name,
                context={"received_type": type(value).__name__},
            )

        normalized = require_text(value, field=field_name).lower()
        normalized = normalized.replace("-", "_").replace(" ", "_")

        try:
            return cls(normalized)
        except ValueError as exc:
            raise DomainValidationError(
                "Unsupported governance outcome.",
                field=field_name,
                context={
                    "received": normalized,
                    "allowed": [member.value for member in cls],
                },
            ) from exc


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    """
    Immutable governance decision attached to exactly one finding.

    Parameters
    ----------
    decision_id:
        Stable identifier of this governance decision.
    finding_id:
        Identifier of the finding governed by this decision.
    outcome:
        Canonical governance outcome.
    reason_code:
        Stable non-empty reason code. BIMAP requires a reason for governance
        actions so release/withhold decisions and overrides remain auditable.
    rationale:
        Optional human-readable explanation supplementing ``reason_code``.
    decided_at:
        Timezone-aware timestamp. Values are normalized to UTC.
    decided_by:
        Optional actor/service identifier. The domain does not prescribe an
        authentication or identity scheme.
    is_override:
        True only when this decision intentionally supersedes an already
        terminal prior decision for the same finding.
    """

    decision_id: str
    finding_id: str
    outcome: DecisionOutcome
    reason_code: str
    rationale: str | None = None
    decided_at: datetime = field(default_factory=utc_now)
    decided_by: str | None = None
    is_override: bool = False

    def __post_init__(self) -> None:
        printer.status("GOVERNANCE", "Validating governance decision", "info")

        object.__setattr__(
            self,
            "decision_id",
            require_text(self.decision_id, field="decision_id"),
        )
        object.__setattr__(
            self,
            "finding_id",
            require_text(self.finding_id, field="finding_id"),
        )
        object.__setattr__(
            self,
            "outcome",
            DecisionOutcome.parse(self.outcome, field_name="outcome"),
        )
        object.__setattr__(
            self,
            "reason_code",
            require_text(self.reason_code, field="reason_code"),
        )
        object.__setattr__(
            self,
            "rationale",
            optional_text(self.rationale, field="rationale"),
        )
        object.__setattr__(
            self,
            "decided_at",
            ensure_utc_datetime(self.decided_at, field="decided_at"),
        )
        object.__setattr__(
            self,
            "decided_by",
            optional_text(self.decided_by, field="decided_by"),
        )

        if not isinstance(self.is_override, bool):
            raise DomainValidationError(
                "is_override must be a boolean.",
                field="is_override",
                context={"received_type": type(self.is_override).__name__},
            )

        logger.debug(
            "Governance decision constructed: decision_id=%s finding_id=%s "
            "outcome=%s override=%s",
            self.decision_id,
            self.finding_id,
            self.outcome.value,
            self.is_override,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical serializable representation of the decision."""

        printer.status("GOVERNANCE", "Serializing governance decision", "info")

        return {
            "decision_id": self.decision_id,
            "finding_id": self.finding_id,
            "outcome": self.outcome.value,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "decided_at": format_utc_datetime(self.decided_at),
            "decided_by": self.decided_by,
            "is_override": self.is_override,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "GovernanceDecision":
        """Reconstruct a decision from its canonical serializable form."""

        printer.status("GOVERNANCE", "Reconstructing governance decision", "info")
        data = require_mapping(payload, field="governance_decision")

        return cls(
            decision_id=data.get("decision_id"),
            finding_id=data.get("finding_id"),
            outcome=DecisionOutcome.parse(data.get("outcome"), field_name="outcome"),
            reason_code=data.get("reason_code"),
            rationale=data.get("rationale"),
            decided_at=data.get("decided_at", utc_now()),
            decided_by=data.get("decided_by"),
            is_override=data.get("is_override", False),
        )


class Decisions:
    """
    Append-only decision history for one BIMAP finding.

    The aggregate enforces:

    - one fixed ``finding_id`` per history;
    - unique ``decision_id`` values;
    - chronological append order;
    - no silent replacement/removal of prior decisions;
    - explicit ``is_override=True`` when changing an already terminal decision.

    ``Decisions`` deliberately stores no ``Finding`` object. That keeps this
    module independent from ``domain/findings`` and prevents a reverse
    dependency from governance outcomes into finding models.
    """

    __slots__ = ("_finding_id", "_history", "_by_id")

    def __init__(
        self,
        finding_id: str,
        decisions: tuple[GovernanceDecision, ...] | list[GovernanceDecision] | None = None,
    ) -> None:
        printer.status("GOVERNANCE", "Initializing governance decision history", "info")

        self._finding_id = require_text(finding_id, field="finding_id")
        self._history: list[GovernanceDecision] = []
        self._by_id: dict[str, GovernanceDecision] = {}

        for decision in decisions or ():
            self.add(decision)

        logger.info(
            "Governance decision history initialized for finding_id=%s with %d decision(s)",
            self._finding_id,
            len(self._history),
        )

    @property
    def finding_id(self) -> str:
        """Identifier of the finding governed by this history."""

        return self._finding_id

    def add(self, decision: GovernanceDecision) -> None:
        """
        Append one decision after validating governance-history invariants.

        Raises
        ------
        DomainValidationError
            If ``decision`` is not a ``GovernanceDecision``.
        DomainInvariantError
            If the decision targets another finding, duplicates an identifier,
            predates the current decision, or violates override semantics.
        """

        printer.status("GOVERNANCE", "Appending governance decision", "info")

        if not isinstance(decision, GovernanceDecision):
            raise DomainValidationError(
                "add() requires a GovernanceDecision instance.",
                field="decision",
                context={"received_type": type(decision).__name__},
            )

        if decision.finding_id != self._finding_id:
            raise DomainInvariantError(
                "Governance decision targets a different finding.",
                field="finding_id",
                context={
                    "history_finding_id": self._finding_id,
                    "decision_finding_id": decision.finding_id,
                },
            )

        if decision.decision_id in self._by_id:
            raise DomainInvariantError(
                "A governance decision with this identifier already exists.",
                field="decision_id",
                context={"decision_id": decision.decision_id},
            )

        current = self.current()
        if current is None:
            if decision.is_override:
                raise DomainInvariantError(
                    "An override requires a prior terminal governance decision.",
                    field="is_override",
                    context={"decision_id": decision.decision_id},
                )
        else:
            if decision.decided_at < current.decided_at:
                raise DomainInvariantError(
                    "Governance decisions must be appended in chronological order.",
                    field="decided_at",
                    context={
                        "current_decision_id": current.decision_id,
                        "current_decided_at": format_utc_datetime(current.decided_at),
                        "received_decision_id": decision.decision_id,
                        "received_decided_at": format_utc_datetime(decision.decided_at),
                    },
                )

            if current.outcome.is_terminal and not decision.is_override:
                raise DomainInvariantError(
                    "Changing a terminal governance decision requires an explicit override.",
                    field="is_override",
                    context={
                        "current_decision_id": current.decision_id,
                        "current_outcome": current.outcome.value,
                        "received_decision_id": decision.decision_id,
                        "received_outcome": decision.outcome.value,
                    },
                )

            if not current.outcome.is_terminal and decision.is_override:
                raise DomainInvariantError(
                    "Resolving a review-required decision is not an override.",
                    field="is_override",
                    context={
                        "current_decision_id": current.decision_id,
                        "current_outcome": current.outcome.value,
                    },
                )

        self._history.append(decision)
        self._by_id[decision.decision_id] = decision

        logger.info(
            "Governance decision appended: decision_id=%s finding_id=%s outcome=%s override=%s",
            decision.decision_id,
            decision.finding_id,
            decision.outcome.value,
            decision.is_override,
        )

    def get(self, decision_id: str) -> GovernanceDecision:
        """Return a decision by identifier."""

        printer.status("GOVERNANCE", "Retrieving governance decision", "info")
        key = require_text(decision_id, field="decision_id")

        try:
            return self._by_id[key]
        except KeyError as exc:
            raise DomainInvariantError(
                "No governance decision with this identifier exists.",
                field="decision_id",
                context={"decision_id": key},
            ) from exc

    def current(self) -> GovernanceDecision | None:
        """Return the latest appended decision, or ``None`` when empty."""

        if not self._history:
            return None
        return self._history[-1]

    def history(self) -> tuple[GovernanceDecision, ...]:
        """Return the immutable decision history in append order."""

        printer.status("GOVERNANCE", "Reading governance decision history", "info")
        return tuple(self._history)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical serializable representation of the history."""

        printer.status("GOVERNANCE", "Serializing governance decision history", "info")
        return {
            "finding_id": self._finding_id,
            "decisions": [decision.to_dict() for decision in self._history],
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "Decisions":
        """Reconstruct and fully revalidate a decision history."""

        printer.status("GOVERNANCE", "Reconstructing governance decision history", "info")
        data = require_mapping(payload, field="decisions")

        raw_decisions = data.get("decisions") or ()
        if isinstance(raw_decisions, (str, bytes, bytearray)) or not isinstance(
            raw_decisions,
            (list, tuple),
        ):
            raise DomainValidationError(
                "decisions must be a list or tuple of governance decisions.",
                field="decisions",
                context={"received_type": type(raw_decisions).__name__},
            )

        history = cls(finding_id=data.get("finding_id"))
        for index, raw_decision in enumerate(raw_decisions):
            try:
                history.add(GovernanceDecision.from_dict(raw_decision))
            except (DomainValidationError, DomainInvariantError) as exc:
                raise type(exc)(
                    exc.message,
                    field=exc.field or f"decisions[{index}]",
                    context={**exc.context, "index": index},
                ) from exc

        return history

    def __contains__(self, decision_id: object) -> bool:
        return isinstance(decision_id, str) and decision_id in self._by_id

    def __iter__(self) -> Iterator[GovernanceDecision]:
        return iter(self._history)

    def __len__(self) -> int:
        return len(self._history)

    def __bool__(self) -> bool:
        return bool(self._history)

    def __repr__(self) -> str:
        current = self.current()
        current_outcome = current.outcome.value if current else None
        return (
            f"Decisions(finding_id={self._finding_id!r}, count={len(self._history)}, "
            f"current_outcome={current_outcome!r})"
        )


__all__ = [
    "DecisionOutcome",
    "GovernanceDecision",
    "Decisions",
]
