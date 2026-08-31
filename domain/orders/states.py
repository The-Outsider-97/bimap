"""
Single authoritative definition of the BIMAP order lifecycle states.

The state vocabulary is derived from the R3D BIM Audit Platform implementation
report. This module intentionally owns only state identity/classification; valid
state-to-state movement is owned by ``transitions.py``.

Dependency direction
--------------------
domain.utils
    ↑
states.py

``states.py`` must not import ``events.py``, ``models.py`` or
``transitions.py``. This preserves a one-directional dependency graph.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from ..utils.domain_errors import DomainValidationError
from ..utils.domain_helpers import require_text
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Orders States")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("ORDERS", action, "info")
    logger.debug(action)


class OrderState(str, Enum):
    """
    Canonical BIMAP order lifecycle state.

    Happy path::

        draft
        -> uploading
        -> upload_validated
        -> payment_pending
        -> paid
        -> queued
        -> ingesting
        -> analyzing
        -> governance_review
        -> packaging
        -> delivered

    Documented exception states are ``upload_rejected``, ``payment_failed``,
    ``analysis_failed``, ``review_required``, ``refunded``, ``cancelled`` and
    ``expired``.

    Transition legality is deliberately not encoded on the enum itself.
    ``OrderTransitions`` in ``transitions.py`` is the sole transition authority.
    """

    DRAFT = "draft"
    UPLOADING = "uploading"
    UPLOAD_VALIDATED = "upload_validated"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    QUEUED = "queued"
    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    GOVERNANCE_REVIEW = "governance_review"
    PACKAGING = "packaging"
    DELIVERED = "delivered"

    UPLOAD_REJECTED = "upload_rejected"
    PAYMENT_FAILED = "payment_failed"
    ANALYSIS_FAILED = "analysis_failed"
    REVIEW_REQUIRED = "review_required"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @classmethod
    def parse(cls, value: Any) -> "OrderState":
        """Normalize a supported string/enum value into ``OrderState``."""
        _announce("Parsing order state")

        if isinstance(value, cls):
            return value

        normalized = require_text(value, field="state").lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            logger.warning("Unsupported BIMAP order state received: %s", normalized)
            raise DomainValidationError(
                "Unsupported BIMAP order state.",
                field="state",
                context={
                    "received": normalized,
                    "allowed": tuple(state.value for state in cls),
                },
            ) from exc

    @property
    def is_exception(self) -> bool:
        """Return whether this state belongs to the documented exception set."""
        return self in EXCEPTION_STATES

    @property
    def is_processing(self) -> bool:
        """Return whether this state belongs to the asynchronous audit pipeline."""
        return self in PROCESSING_STATES

    @property
    def is_delivered(self) -> bool:
        """Return whether customer delivery has completed."""
        return self is OrderState.DELIVERED

    def __str__(self) -> str:
        return self.value


EXCEPTION_STATES = frozenset(
    {
        OrderState.UPLOAD_REJECTED,
        OrderState.PAYMENT_FAILED,
        OrderState.ANALYSIS_FAILED,
        OrderState.REVIEW_REQUIRED,
        OrderState.REFUNDED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    }
)

TERMINAL_EXCEPTION_STATES = EXCEPTION_STATES

PROCESSING_STATES = frozenset(
    {
        OrderState.QUEUED,
        OrderState.INGESTING,
        OrderState.ANALYZING,
        OrderState.GOVERNANCE_REVIEW,
        OrderState.PACKAGING,
    }
)


def coerce_order_state(value: Any) -> OrderState:
    """Public normalization helper used by sibling order-domain modules."""
    _announce("Coercing order state")
    return OrderState.parse(value)


# Backward-compatible alias for the original scaffold placeholder.
States = OrderState


__all__ = [
    "OrderState",
    "States",
    "EXCEPTION_STATES",
    "TERMINAL_EXCEPTION_STATES",
    "PROCESSING_STATES",
    "coerce_order_state",
]
