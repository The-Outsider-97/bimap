"""
BIMAP governance-review aggregate for findings.

``Review`` represents the review state attached to one canonical ``Finding``.
It composes the append-only ``Decisions`` history from ``decisions.py`` rather
than reimplementing decision storage or override rules.

The module implements only governance behaviour supported by the BIMAP design:

- high/critical-severity findings below a caller-supplied confidence threshold
  require review;
- a review records one or more stable reason codes;
- unresolved/review-required and blocked outcomes prevent finding release;
- approved findings may be released;
- suppressed findings remain auditable but are intentionally withheld;
- any later change to a terminal decision is recorded as an explicit override
  by ``Decisions`` rather than mutating prior history.

Other review triggers described by BIMAP (for example contradictory evidence or
inference as the sole support for a material recommendation) are represented by
``reason_codes`` but are not auto-detected here because the current canonical
``Finding`` model does not carry those facts. Higher layers must detect those
conditions from grounded audit/evidence data and request a review explicitly.

Dependency direction
--------------------

    findings/models.py ----\
    findings/severity.py ----+--> governance/review.py
    governance/decisions.py -/

``decisions.py`` never imports ``review.py`` or ``findings``. This preserves a
one-directional graph and prevents circular imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..findings.models import Finding
from ..findings.severity import SeverityLevel
from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .decisions import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("BIMAP Governance Review")
printer = PrettyPrinter()


class Review:
    """
    Governance-review aggregate for exactly one BIMAP finding.

    Parameters
    ----------
    review_id:
        Stable identifier of this review record.
    finding:
        Canonical ``Finding`` being reviewed.
    reason_codes:
        One or more stable reason codes explaining why review was requested.
        Detection/definition of product-specific reason codes belongs to the
        policy/application layer; this domain aggregate preserves them without
        redefining product policy.
    requested_at:
        Timezone-aware review-request timestamp, normalized to UTC.
    requested_by:
        Optional service/actor identifier. Authentication semantics are owned by
        higher layers.
    decisions:
        Optional pre-existing ``Decisions`` history for rehydration/composition.
    """

    __slots__ = (
        "_review_id",
        "_finding",
        "_reason_codes",
        "_requested_at",
        "_requested_by",
        "_decisions",
    )

    def __init__(
        self,
        review_id: str,
        finding: Finding,
        reason_codes: tuple[str, ...] | list[str],
        *,
        requested_at: datetime | str | None = None,
        requested_by: str | None = None,
        decisions: Decisions | None = None,
    ) -> None:
        printer.status("GOVERNANCE", "Initializing finding governance review", "info")

        self._review_id = require_text(review_id, field="review_id")

        if not isinstance(finding, Finding):
            raise DomainValidationError(
                "Review requires a canonical Finding instance.",
                field="finding",
                context={"received_type": type(finding).__name__},
            )
        self._finding = finding

        normalized_reasons = stable_unique_text(reason_codes, field="reason_codes")
        if not normalized_reasons:
            raise DomainValidationError(
                "A governance review requires at least one reason code.",
                field="reason_codes",
            )
        self._reason_codes = normalized_reasons

        self._requested_at = ensure_utc_datetime(
            requested_at if requested_at is not None else utc_now(),
            field="requested_at",
        )
        self._requested_by = optional_text(requested_by, field="requested_by")

        if decisions is None:
            self._decisions = Decisions(finding_id=finding.finding_id)
        else:
            if not isinstance(decisions, Decisions):
                raise DomainValidationError(
                    "decisions must be a Decisions instance.",
                    field="decisions",
                    context={"received_type": type(decisions).__name__},
                )
            if decisions.finding_id != finding.finding_id:
                raise DomainInvariantError(
                    "Review decision history belongs to a different finding.",
                    field="finding_id",
                    context={
                        "review_finding_id": finding.finding_id,
                        "decision_history_finding_id": decisions.finding_id,
                    },
                )
            self._decisions = decisions

        current = self._decisions.current()
        if current is not None and current.decided_at < self._requested_at:
            raise DomainInvariantError(
                "A governance decision cannot predate its review request.",
                field="decided_at",
                context={
                    "review_id": self._review_id,
                    "requested_at": format_utc_datetime(self._requested_at),
                    "current_decision_id": current.decision_id,
                    "current_decided_at": format_utc_datetime(current.decided_at),
                },
            )

        logger.info(
            "Governance review initialized: review_id=%s finding_id=%s reasons=%d decisions=%d",
            self._review_id,
            finding.finding_id,
            len(self._reason_codes),
            len(self._decisions),
        )

    # ------------------------------------------------------------------
    # Identity and state
    # ------------------------------------------------------------------

    @property
    def review_id(self) -> str:
        return self._review_id

    @property
    def finding(self) -> Finding:
        return self._finding

    @property
    def finding_id(self) -> str:
        return self._finding.finding_id

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self._reason_codes

    @property
    def requested_at(self) -> datetime:
        return self._requested_at

    @property
    def requested_by(self) -> str | None:
        return self._requested_by

    @property
    def decisions(self) -> Decisions:
        """Return the owned append-only decision-history aggregate."""

        return self._decisions

    def current_decision(self) -> GovernanceDecision | None:
        """Return the current governance decision for the finding."""

        printer.status("GOVERNANCE", "Reading current review decision", "info")
        return self._decisions.current()

    def is_pending(self) -> bool:
        """
        Return True while no terminal governance decision has resolved review.
        """

        current = self._decisions.current()
        return current is None or current.outcome is DecisionOutcome.REVIEW_REQUIRED

    def is_resolved(self) -> bool:
        """Return True when a terminal governance outcome exists."""

        current = self._decisions.current()
        return current is not None and current.outcome.is_terminal

    def finding_release_allowed(self) -> bool:
        """Return whether the reviewed finding has an APPROVED outcome."""

        current = self._decisions.current()
        return current is not None and current.outcome.permits_finding_release

    def finding_is_suppressed(self) -> bool:
        """Return whether governance intentionally suppressed the finding."""

        current = self._decisions.current()
        return current is not None and current.outcome.suppresses_finding

    def blocks_finding_release(self) -> bool:
        """
        Return whether the finding must not currently be customer-released.

        Pending review and explicit BLOCKED outcomes block finding release.
        SUPPRESSED is treated separately: the finding is intentionally omitted,
        while the larger report may still proceed under higher-level policy.
        """

        current = self._decisions.current()
        if current is None:
            return True
        return current.outcome.blocks_finding_release

    # ------------------------------------------------------------------
    # Policy-supported review trigger
    # ------------------------------------------------------------------

    @staticmethod
    def requires_review(
        finding: Finding,
        *,
        confidence_threshold: float,
    ) -> bool:
        """
        Evaluate the BIMAP high-severity/low-confidence mandatory-review rule.

        The threshold is caller-supplied rather than hard-coded because the
        BIMAP implementation report requires a *product threshold* but does not
        prescribe a numeric value. Product-specific calibration therefore
        remains outside this domain module.

        This method intentionally does not infer the other documented review
        triggers (contradictory evidence or inference-only support), because
        those facts are not fields of the current canonical ``Finding`` model.
        """

        printer.status("GOVERNANCE", "Evaluating mandatory review threshold", "info")

        if not isinstance(finding, Finding):
            raise DomainValidationError(
                "requires_review() requires a Finding instance.",
                field="finding",
                context={"received_type": type(finding).__name__},
            )

        threshold = normalize_probability(
            confidence_threshold,
            field="confidence_threshold",
        )
        assert threshold is not None  # normalize_probability cannot return None here.

        material_severity = finding.severity.level in {
            SeverityLevel.HIGH,
            SeverityLevel.CRITICAL,
        }
        below_threshold = finding.confidence.score < threshold

        result = material_severity and below_threshold
        logger.debug(
            "Mandatory-review evaluation: finding_id=%s severity=%s confidence=%.6f "
            "threshold=%.6f requires_review=%s",
            finding.finding_id,
            finding.severity.level.label,
            finding.confidence.score,
            threshold,
            result,
        )
        return result

    # ------------------------------------------------------------------
    # Decision mutation
    # ------------------------------------------------------------------

    def add_decision(self, decision: GovernanceDecision) -> None:
        """
        Append a governance decision to this review.

        ``Decisions.add`` remains the single owner of decision-history and
        override invariants. ``Review`` adds only the review-specific temporal
        invariant that decisions may not predate the review request.
        """

        printer.status("GOVERNANCE", "Applying governance review decision", "info")

        if not isinstance(decision, GovernanceDecision):
            raise DomainValidationError(
                "add_decision() requires a GovernanceDecision instance.",
                field="decision",
                context={"received_type": type(decision).__name__},
            )

        if decision.decided_at < self._requested_at:
            raise DomainInvariantError(
                "Governance decision cannot predate the review request.",
                field="decided_at",
                context={
                    "review_id": self._review_id,
                    "requested_at": format_utc_datetime(self._requested_at),
                    "decision_id": decision.decision_id,
                    "decided_at": format_utc_datetime(decision.decided_at),
                },
            )

        self._decisions.add(decision)
        logger.info(
            "Governance review decision applied: review_id=%s finding_id=%s "
            "decision_id=%s outcome=%s",
            self._review_id,
            self.finding_id,
            decision.decision_id,
            decision.outcome.value,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical serializable representation of this review."""

        printer.status("GOVERNANCE", "Serializing governance review", "info")

        return {
            "review_id": self._review_id,
            "finding": self._finding.to_dict(),
            "reason_codes": list(self._reason_codes),
            "requested_at": format_utc_datetime(self._requested_at),
            "requested_by": self._requested_by,
            "decisions": self._decisions.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "Review":
        """Reconstruct and fully revalidate a governance review."""

        printer.status("GOVERNANCE", "Reconstructing governance review", "info")
        data = require_mapping(payload, field="review")

        finding = Finding.from_dict(data.get("finding"))

        raw_reasons = data.get("reason_codes") or ()
        if isinstance(raw_reasons, (str, bytes, bytearray)) or not isinstance(
            raw_reasons,
            (list, tuple),
        ):
            raise DomainValidationError(
                "reason_codes must be a list or tuple of strings.",
                field="reason_codes",
                context={"received_type": type(raw_reasons).__name__},
            )

        raw_decisions = data.get("decisions")
        decisions = (
            Decisions(finding_id=finding.finding_id)
            if raw_decisions is None
            else Decisions.from_dict(raw_decisions)
        )

        if decisions.finding_id != finding.finding_id:
            raise DomainInvariantError(
                "Serialized review contains decision history for another finding.",
                field="finding_id",
                context={
                    "finding_id": finding.finding_id,
                    "decisions_finding_id": decisions.finding_id,
                },
            )

        return cls(
            review_id=data.get("review_id"),
            finding=finding,
            reason_codes=list(raw_reasons),
            requested_at=data.get("requested_at", utc_now()),
            requested_by=data.get("requested_by"),
            decisions=decisions,
        )

    def __repr__(self) -> str:
        current = self._decisions.current()
        current_outcome = current.outcome.value if current else None
        return (
            f"Review(review_id={self._review_id!r}, finding_id={self.finding_id!r}, "
            f"pending={self.is_pending()!r}, current_outcome={current_outcome!r})"
        )


__all__ = [
    "Review",
]