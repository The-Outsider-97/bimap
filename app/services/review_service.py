"""
Application service for BIMAP governance-review workflows.

The service coordinates the canonical domain ``Review`` aggregate and its
append-only ``GovernanceDecision`` history with application persistence and a
deterministic ``Clock``.  It does not redefine severity, confidence, review
outcomes, or decision override semantics.

Only the review trigger currently supported by the domain model can be
automatically evaluated here: high/critical severity below a caller-supplied
confidence threshold.  Contradictory-evidence and inference-only triggers are
not fabricated because those facts are not represented on the canonical domain
``Finding``.  Callers may request a review explicitly with caller-owned stable
reason codes when such conditions have been established elsewhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from ..ports.clock import Clock
from ..ports.repositories import Repository
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.findings.models import Finding
from ...domain.governance.decisions import DecisionOutcome, GovernanceDecision
from ...domain.governance.review import Review
from ...domain.utils.domain_errors import DomainError, DomainInvariantError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Review Service")
printer = PrettyPrinter()

_COMPONENT = "review_service"


def _translate_domain_error(
    exc: DomainError,
    *,
    operation: str,
    message: str,
    field: str | None = None,
) -> AppError:
    """Translate review-domain validation/invariant failures at the app boundary."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Translating governance domain failure",
        event="review_service_domain_error_translate_start",
        context={"operation": operation, "error_type": type(exc).__name__},
    )
    error_type = AppIntegrityError if isinstance(exc, DomainInvariantError) else AppValidationError
    return error_type(
        message,
        component=_COMPONENT,
        operation=operation,
        field=field,
        context=lower_error_context(exc),
        cause=exc,
    )


def _normalize_reason_codes(values: Iterable[str], *, operation: str) -> tuple[str, ...]:
    """Normalize stable unique reason codes without defining their taxonomy."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing governance review reason codes",
        event="review_service_reason_codes_normalize_start",
        context={"operation": operation},
    )
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedAppInputError(
            "reason_codes must be an iterable of individual text values.",
            component=_COMPONENT,
            operation=operation,
            field="reason_codes",
            context={"received_type": type(values).__name__},
        )
    try:
        raw = tuple(values)
    except TypeError as exc:
        raise UnsupportedAppInputError(
            "reason_codes must be iterable.",
            component=_COMPONENT,
            operation=operation,
            field="reason_codes",
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        code = require_app_text(
            value,
            field=f"reason_codes[{index}]",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )
        if code in seen:
            raise AppValidationError(
                "reason_codes contains a duplicate normalized value.",
                component=_COMPONENT,
                operation=operation,
                field="reason_codes",
                context={"duplicate": code},
            )
        seen.add(code)
        normalized.append(code)

    if not normalized:
        raise AppValidationError(
            "At least one governance review reason code is required.",
            component=_COMPONENT,
            operation=operation,
            field="reason_codes",
        )
    return tuple(normalized)


class ReviewService:
    """Coordinate review requests and append-only governance decisions."""

    def __init__(self, repository: Repository, clock: Clock) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing review service",
            event="review_service_init_start",
        )
        if not isinstance(repository, Repository):
            raise AppConfigurationError(
                "repository must implement the BIMAP Repository port.",
                component=_COMPONENT,
                operation="initialize",
                field="repository",
                context={"received_type": type(repository).__name__},
            )
        if not isinstance(clock, Clock):
            raise AppConfigurationError(
                "clock must implement the BIMAP Clock port.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
                context={"received_type": type(clock).__name__},
            )

        self.repository = repository
        self.clock = clock
        logger.info({"event": "review_service_initialized"})

    def find_review(self, review_id: str) -> Review | None:
        """Return a review or ``None`` when it does not exist."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding governance review",
            event="review_service_find_start",
            context={"review_id": review_id},
        )
        target = require_app_text(
            review_id,
            field="review_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="find_review",
        )
        return self.repository.get_review(target)

    def get_review(self, review_id: str) -> Review:
        """Return one required review or fail when absent."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading required governance review",
            event="review_service_get_start",
            context={"review_id": review_id},
        )
        review = self.find_review(review_id)
        if review is None:
            raise AppValidationError(
                "Governance review does not exist.",
                component=_COMPONENT,
                operation="get_review",
                field="review_id",
                context={"review_id": review_id},
            )
        return review

    def requires_review(
        self,
        finding: Finding,
        *,
        confidence_threshold: float,
    ) -> bool:
        """Evaluate the domain-supported material-severity confidence trigger."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Evaluating mandatory governance review",
            event="review_service_requires_review_start",
            context={"finding_id": getattr(finding, "finding_id", None)},
        )
        if not isinstance(finding, Finding):
            raise UnsupportedAppInputError(
                "requires_review() requires a canonical domain Finding.",
                component=_COMPONENT,
                operation="requires_review",
                field="finding",
                context={"received_type": type(finding).__name__},
            )
        try:
            return Review.requires_review(
                finding,
                confidence_threshold=confidence_threshold,
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="requires_review",
                message="Governance review threshold evaluation failed validation.",
                field="confidence_threshold",
            ) from exc

    def request_review(
        self,
        finding: Finding,
        *,
        review_id: str,
        reason_codes: Iterable[str],
        requested_by: str | None = None,
        requested_at: datetime | str | None = None,
    ) -> Review:
        """Create and persist an explicit governance review for one finding.

        ``review_id`` provides replay identity.  Reusing an existing identifier
        is accepted only when the existing review targets the same finding and
        has the same normalized reason/requestor data; otherwise the service
        fails closed instead of overwriting review history.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Requesting governance review",
            event="review_service_request_start",
            context={
                "review_id": review_id,
                "finding_id": getattr(finding, "finding_id", None),
            },
        )
        if not isinstance(finding, Finding):
            raise UnsupportedAppInputError(
                "request_review() requires a canonical domain Finding.",
                component=_COMPONENT,
                operation="request_review",
                field="finding",
                context={"received_type": type(finding).__name__},
            )

        target_review_id = require_app_text(
            review_id,
            field="review_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="request_review",
        )
        reasons = _normalize_reason_codes(reason_codes, operation="request_review")
        requestor = optional_app_text(
            requested_by,
            field="requested_by",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="request_review",
        )
        explicit_timestamp = (
            None
            if requested_at is None
            else ensure_app_utc_datetime(
                requested_at,
                field="requested_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="request_review",
            )
        )

        existing = self.repository.get_review(target_review_id)
        if existing is not None:
            if (
                existing.finding == finding
                and existing.reason_codes == reasons
                and existing.requested_by == requestor
                and (
                    explicit_timestamp is None
                    or existing.requested_at == explicit_timestamp
                )
            ):
                return existing
            raise AppIntegrityError(
                "Review identifier is already bound to different governance data.",
                component=_COMPONENT,
                operation="request_review",
                field="review_id",
                context={
                    "review_id": target_review_id,
                    "existing_finding_id": existing.finding_id,
                    "requested_finding_id": finding.finding_id,
                },
            )

        timestamp = self.clock.now() if explicit_timestamp is None else explicit_timestamp
        try:
            review = Review(
                review_id=target_review_id,
                finding=finding,
                reason_codes=reasons,
                requested_at=timestamp,
                requested_by=requestor,
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="request_review",
                message="Governance review request violates review-domain constraints.",
            ) from exc

        persisted = self.repository.save_review(review)
        if persisted.review_id != review.review_id or persisted.finding_id != review.finding_id:
            raise AppIntegrityError(
                "Repository changed governance review identity while saving.",
                component=_COMPONENT,
                operation="request_review",
                field="persisted_review",
                context={
                    "expected_review_id": review.review_id,
                    "returned_review_id": persisted.review_id,
                    "expected_finding_id": review.finding_id,
                    "returned_finding_id": persisted.finding_id,
                },
            )

        logger.info(
            {
                "event": "review_service_review_requested",
                "review_id": persisted.review_id,
                "finding_id": persisted.finding_id,
                "reason_count": len(persisted.reason_codes),
            }
        )
        return persisted

    def request_if_required(
        self,
        finding: Finding,
        *,
        confidence_threshold: float,
        review_id: str,
        reason_codes: Iterable[str],
        requested_by: str | None = None,
    ) -> Review | None:
        """Request review only when the currently supported automatic trigger fires."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Requesting governance review when required",
            event="review_service_request_if_required_start",
            context={"finding_id": getattr(finding, "finding_id", None)},
        )
        if not self.requires_review(
            finding,
            confidence_threshold=confidence_threshold,
        ):
            return None
        return self.request_review(
            finding,
            review_id=review_id,
            reason_codes=reason_codes,
            requested_by=requested_by,
        )

    def record_decision(
        self,
        review_id: str,
        *,
        decision_id: str,
        outcome: DecisionOutcome | str,
        reason_code: str,
        rationale: str | None = None,
        decided_by: str | None = None,
        is_override: bool = False,
        decided_at: datetime | str | None = None,
    ) -> Review:
        """Append and persist one governance decision.

        Decision IDs provide replay identity.  An exact replay returns the
        existing review; reusing an identifier for different decision content is
        rejected.  Override legality remains owned by ``Decisions.add``.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Recording governance decision",
            event="review_service_decision_start",
            context={"review_id": review_id, "decision_id": decision_id},
        )

        review = self.get_review(review_id)
        target_decision_id = require_app_text(
            decision_id,
            field="decision_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="record_decision",
        )
        normalized_reason = require_app_text(
            reason_code,
            field="reason_code",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="record_decision",
        )
        normalized_rationale = optional_app_text(
            rationale,
            field="rationale",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="record_decision",
        )
        normalized_decider = optional_app_text(
            decided_by,
            field="decided_by",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="record_decision",
        )
        if not isinstance(is_override, bool):
            raise AppValidationError(
                "is_override must be boolean.",
                component=_COMPONENT,
                operation="record_decision",
                field="is_override",
                context={"received_type": type(is_override).__name__},
            )

        explicit_timestamp = (
            None
            if decided_at is None
            else ensure_app_utc_datetime(
                decided_at,
                field="decided_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="record_decision",
            )
        )

        try:
            normalized_outcome = DecisionOutcome.parse(outcome)
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="record_decision",
                message="Governance decision outcome violates domain constraints.",
                field="outcome",
            ) from exc

        for existing in review.decisions.history():
            if existing.decision_id != target_decision_id:
                continue

            same_content = (
                existing.outcome is normalized_outcome
                and existing.reason_code == normalized_reason
                and existing.rationale == normalized_rationale
                and existing.decided_by == normalized_decider
                and existing.is_override is is_override
                and (
                    explicit_timestamp is None
                    or existing.decided_at == explicit_timestamp
                )
            )
            if same_content:
                return review

            raise AppIntegrityError(
                "Governance decision identifier is already bound to different decision data.",
                component=_COMPONENT,
                operation="record_decision",
                field="decision_id",
                context={
                    "review_id": review.review_id,
                    "decision_id": target_decision_id,
                    "existing_outcome": existing.outcome.value,
                    "requested_outcome": normalized_outcome.value,
                },
            )

        timestamp = self.clock.now() if explicit_timestamp is None else explicit_timestamp
        try:
            decision = GovernanceDecision(
                decision_id=target_decision_id,
                finding_id=review.finding_id,
                outcome=normalized_outcome,
                reason_code=normalized_reason,
                rationale=normalized_rationale,
                decided_at=timestamp,
                decided_by=normalized_decider,
                is_override=is_override,
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="record_decision",
                message="Governance decision input violates domain constraints.",
            ) from exc

        try:
            review.add_decision(decision)
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="record_decision",
                message="Governance decision cannot be appended to this review history.",
                field="decision",
            ) from exc

        persisted = self.repository.save_review(review)
        current = persisted.current_decision()
        if current is None or current.decision_id != decision.decision_id:
            raise AppIntegrityError(
                "Repository write-back did not preserve the appended governance decision.",
                component=_COMPONENT,
                operation="record_decision",
                field="persisted_review.decisions",
                context={
                    "review_id": persisted.review_id,
                    "expected_decision_id": decision.decision_id,
                    "returned_decision_id": None if current is None else current.decision_id,
                },
            )

        logger.info(
            {
                "event": "review_service_decision_recorded",
                "review_id": persisted.review_id,
                "finding_id": persisted.finding_id,
                "decision_id": decision.decision_id,
                "outcome": decision.outcome.value,
                "is_override": decision.is_override,
            }
        )
        return persisted

    def finding_release_allowed(self, review_id: str) -> bool:
        """Return the domain-governed release status for one reviewed finding."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Checking reviewed finding release status",
            event="review_service_release_check_start",
            context={"review_id": review_id},
        )
        return self.get_review(review_id).finding_release_allowed()


__all__ = ["ReviewService"]