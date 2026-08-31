"""
Central authority for BIMAP order-state transitions.

The transition graph implements the order flow defined by the R3D BIM Audit
Platform implementation report while keeping commercial policy in higher
layers. Representing a structural transition does not itself authorize a refund
or other commercial action; those policies remain application-layer concerns.

Dependency direction
--------------------
domain.utils
    ↑
states.py
    ↑
events.py
    ↑
models.py
    ↑
transitions.py

No lower-level order module imports this module, preventing circular imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .events import OrderEvent
from .models import Order
from .states import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Orders Transitions")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("ORDERS", action, "info")
    logger.debug(action)


# Structural lifecycle graph.
#
# - Pre-payment cancellation uses CANCELLED.
# - Post-payment termination uses REFUNDED so paid state is not silently lost.
# - Transient queue/packaging failures should normally retry in the same state.
# - REVIEW_REQUIRED remains an exception/stop state per the implementation
#   report; any later manual-release workflow should be introduced explicitly.
TRANSITION_GRAPH: Mapping[OrderState, frozenset[OrderState]] = MappingProxyType(
    {
        OrderState.DRAFT: frozenset(
            {
                OrderState.UPLOADING,
                OrderState.CANCELLED,
                OrderState.EXPIRED,
            }
        ),
        OrderState.UPLOADING: frozenset(
            {
                OrderState.UPLOAD_VALIDATED,
                OrderState.UPLOAD_REJECTED,
                OrderState.CANCELLED,
                OrderState.EXPIRED,
            }
        ),
        OrderState.UPLOAD_VALIDATED: frozenset(
            {
                OrderState.PAYMENT_PENDING,
                OrderState.CANCELLED,
                OrderState.EXPIRED,
            }
        ),
        OrderState.PAYMENT_PENDING: frozenset(
            {
                OrderState.PAID,
                OrderState.PAYMENT_FAILED,
                OrderState.CANCELLED,
                OrderState.EXPIRED,
            }
        ),
        OrderState.PAID: frozenset(
            {OrderState.QUEUED, OrderState.REFUNDED}
        ),
        OrderState.QUEUED: frozenset(
            {OrderState.INGESTING, OrderState.REFUNDED}
        ),
        OrderState.INGESTING: frozenset(
            {
                OrderState.ANALYZING,
                OrderState.ANALYSIS_FAILED,
                OrderState.REFUNDED,
            }
        ),
        OrderState.ANALYZING: frozenset(
            {
                OrderState.GOVERNANCE_REVIEW,
                OrderState.ANALYSIS_FAILED,
                OrderState.REFUNDED,
            }
        ),
        OrderState.GOVERNANCE_REVIEW: frozenset(
            {
                OrderState.PACKAGING,
                OrderState.REVIEW_REQUIRED,
                OrderState.ANALYSIS_FAILED,
                OrderState.REFUNDED,
            }
        ),
        OrderState.PACKAGING: frozenset(
            {OrderState.DELIVERED, OrderState.REFUNDED}
        ),
        OrderState.DELIVERED: frozenset(
            {OrderState.REFUNDED, OrderState.EXPIRED}
        ),
        OrderState.UPLOAD_REJECTED: frozenset(),
        OrderState.PAYMENT_FAILED: frozenset(),
        OrderState.ANALYSIS_FAILED: frozenset(),
        OrderState.REVIEW_REQUIRED: frozenset(),
        OrderState.REFUNDED: frozenset(),
        OrderState.CANCELLED: frozenset(),
        OrderState.EXPIRED: frozenset(),
    }
)


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """
    Result of attempting one order transition.

    ``applied=False`` means a successful idempotent replay: the same key was
    already recorded for the requested target state.
    """

    order: Order
    event: OrderEvent
    applied: bool


class OrderTransitions:
    """Single state-transition authority for the BIMAP ``Order`` aggregate."""

    @classmethod
    def allowed_targets(
        cls,
        state: OrderState | str,
    ) -> frozenset[OrderState]:
        """Return the immutable set of structurally valid target states."""
        _announce("Resolving allowed order-state targets")

        normalized = coerce_order_state(state)
        return TRANSITION_GRAPH[normalized]

    @classmethod
    def can_transition(
        cls,
        source: OrderState | str,
        target: OrderState | str,
    ) -> bool:
        """Return whether ``source -> target`` exists in the state graph."""
        _announce("Checking order-state transition")

        normalized_source = coerce_order_state(source)
        normalized_target = coerce_order_state(target)

        return normalized_target in TRANSITION_GRAPH[normalized_source]

    @classmethod
    def validate(
        cls,
        source: OrderState | str,
        target: OrderState | str,
    ) -> None:
        """Raise ``DomainInvariantError`` when a transition is not permitted."""
        _announce("Validating order-state transition")

        normalized_source = coerce_order_state(source)
        normalized_target = coerce_order_state(target)

        if normalized_source is normalized_target:
            raise DomainInvariantError(
                "Order cannot transition to its current state.",
                field="target_state",
                context={"state": normalized_source.value},
            )

        allowed = TRANSITION_GRAPH[normalized_source]

        if normalized_target not in allowed:
            logger.warning(
                "Rejected BIMAP order transition %s -> %s",
                normalized_source.value,
                normalized_target.value,
            )
            raise DomainInvariantError(
                "Order state transition is not permitted.",
                field="target_state",
                context={
                    "from_state": normalized_source.value,
                    "to_state": normalized_target.value,
                    "allowed_targets": tuple(
                        sorted(state.value for state in allowed)
                    ),
                },
            )

    @classmethod
    def apply(
        cls,
        order: Order,
        target: OrderState | str,
        *,
        idempotency_key: str,
        occurred_at: datetime | str | None = None,
        expected_version: int | None = None,
        event_id: str | None = None,
        reason: str | None = None,
        actor: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> TransitionResult:
        """
        Validate and append exactly one lifecycle transition.

        A repeated idempotency key targeting the same state returns the
        unchanged aggregate with ``applied=False``. Reusing the key for another
        target is rejected.

        ``expected_version`` is checked after idempotency replay detection so a
        safe retry may succeed with the caller's pre-transition version.
        Persistence must additionally provide atomic compare-and-swap and a
        unique ``(order_id, idempotency_key)`` constraint.
        """
        _announce("Applying order-state transition")

        if not isinstance(order, Order):
            raise DomainValidationError(
                "order must be an Order aggregate.",
                field="order",
                context={"received_type": type(order).__name__},
            )

        normalized_target = coerce_order_state(target)
        normalized_key = require_text(
            idempotency_key,
            field="idempotency_key",
        )

        existing = order.event_for_idempotency_key(normalized_key)

        if existing is not None:
            if existing.same_transition(
                order_id=order.order_id,
                to_state=normalized_target,
            ):
                logger.info(
                    "Idempotent replay accepted for order %s",
                    order.order_id,
                )
                return TransitionResult(
                    order=order,
                    event=existing,
                    applied=False,
                )

            raise DomainInvariantError(
                "Idempotency key is already bound to a different transition.",
                field="idempotency_key",
                context={
                    "order_id": order.order_id,
                    "idempotency_key": normalized_key,
                    "existing_to_state": existing.to_state.value,
                    "requested_to_state": normalized_target.value,
                },
            )

        if expected_version is not None:
            if (
                isinstance(expected_version, bool)
                or not isinstance(expected_version, int)
            ):
                raise DomainValidationError(
                    "expected_version must be an integer or None.",
                    field="expected_version",
                    context={
                        "received_type": type(expected_version).__name__
                    },
                )

            if expected_version < 0:
                raise DomainValidationError(
                    "expected_version must be non-negative.",
                    field="expected_version",
                    context={"received": expected_version},
                )

            if order.version != expected_version:
                raise DomainInvariantError(
                    "Order version does not match expected_version.",
                    field="expected_version",
                    context={
                        "order_id": order.order_id,
                        "expected_version": expected_version,
                        "actual_version": order.version,
                    },
                )

        cls.validate(order.state, normalized_target)

        timestamp = (
            utc_now()
            if occurred_at is None
            else ensure_utc_datetime(occurred_at, field="occurred_at")
        )

        event = OrderEvent.create(
            order_id=order.order_id,
            idempotency_key=normalized_key,
            from_state=order.state,
            to_state=normalized_target,
            occurred_at=timestamp,
            event_id=event_id,
            reason=reason,
            actor=actor,
            metadata=metadata or {},
        )

        updated_order = order._apply_state_event(event)

        logger.info(
            "BIMAP order %s transitioned %s -> %s",
            order.order_id,
            order.state.value,
            normalized_target.value,
        )

        return TransitionResult(
            order=updated_order,
            event=event,
            applied=True,
        )


# Backward-compatible alias for the original scaffold placeholder.
Transitions = OrderTransitions


__all__ = [
    "TRANSITION_GRAPH",
    "TransitionResult",
    "OrderTransitions",
    "Transitions",
]