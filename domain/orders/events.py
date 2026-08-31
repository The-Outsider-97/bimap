"""
Append-only BIMAP order lifecycle events.

An ``OrderEvent`` records one state change with a UTC timestamp and an explicit
idempotency key. API, persistence, payment-provider and SLAI concerns remain
outside this module.

Dependency direction
--------------------
domain.utils
    ↑
states.py
    ↑
events.py

``events.py`` must not import ``models.py`` or ``transitions.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .states import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Orders Events")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("ORDERS", action, "info")
    logger.debug(action)


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """
    Immutable record of one BIMAP order lifecycle state change.

    ``idempotency_key`` is caller supplied. Persistence must additionally
    enforce uniqueness for ``(order_id, idempotency_key)`` so retries remain
    correct across processes and worker restarts.

    ``from_state=None`` is reserved for an optional creation event whose target
    must be ``draft``. Transition legality is decided by ``transitions.py``.
    """

    event_id: str
    order_id: str
    idempotency_key: str
    occurred_at: datetime
    from_state: OrderState | None
    to_state: OrderState

    reason: str | None = None
    actor: str | None = None
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        _announce("Validating order lifecycle event")

        object.__setattr__(
            self, "event_id", require_text(self.event_id, field="event_id")
        )
        object.__setattr__(
            self, "order_id", require_text(self.order_id, field="order_id")
        )
        object.__setattr__(
            self,
            "idempotency_key",
            require_text(self.idempotency_key, field="idempotency_key"),
        )
        object.__setattr__(
            self,
            "occurred_at",
            ensure_utc_datetime(self.occurred_at, field="occurred_at"),
        )

        normalized_from = (
            coerce_order_state(self.from_state)
            if self.from_state is not None
            else None
        )
        normalized_to = coerce_order_state(self.to_state)

        if normalized_from is not None and normalized_from is normalized_to:
            raise DomainInvariantError(
                "Order lifecycle event cannot transition to the same state.",
                field="to_state",
                context={"state": normalized_to.value},
            )

        if normalized_from is None and normalized_to is not OrderState.DRAFT:
            raise DomainInvariantError(
                "An order creation event may only target the draft state.",
                field="to_state",
                context={"received": normalized_to.value},
            )

        object.__setattr__(self, "from_state", normalized_from)
        object.__setattr__(self, "to_state", normalized_to)
        object.__setattr__(
            self, "reason", optional_text(self.reason, field="reason")
        )
        object.__setattr__(
            self, "actor", optional_text(self.actor, field="actor")
        )

        source_metadata = require_mapping(self.metadata, field="metadata")
        frozen_metadata = freeze_json_value(source_metadata, field="metadata")

        if not isinstance(frozen_metadata, Mapping):
            raise DomainValidationError(
                "Event metadata normalization did not produce a mapping.",
                field="metadata",
            )

        object.__setattr__(self, "metadata", frozen_metadata)

    @classmethod
    def create(
        cls,
        *,
        order_id: str,
        idempotency_key: str,
        from_state: OrderState | str | None,
        to_state: OrderState | str,
        occurred_at: datetime | str | None = None,
        event_id: str | None = None,
        reason: str | None = None,
        actor: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "OrderEvent":
        """Create a validated lifecycle event with safe defaults."""
        _announce("Creating order lifecycle event")

        timestamp = (
            utc_now()
            if occurred_at is None
            else ensure_utc_datetime(occurred_at, field="occurred_at")
        )

        return cls(
            event_id=event_id or uuid4().hex,
            order_id=order_id,
            idempotency_key=idempotency_key,
            occurred_at=timestamp,
            from_state=(
                coerce_order_state(from_state)
                if from_state is not None
                else None
            ),
            to_state=coerce_order_state(to_state),
            reason=reason,
            actor=actor,
            metadata=metadata or {},
        )

    def same_transition(
        self,
        *,
        order_id: str,
        to_state: OrderState | str,
    ) -> bool:
        """Return whether this event represents the same order/target operation."""
        _announce("Comparing order lifecycle event")

        normalized_order_id = require_text(order_id, field="order_id")
        normalized_target = coerce_order_state(to_state)

        return (
            self.order_id == normalized_order_id
            and self.to_state is normalized_target
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event into deterministic JSON-ready data."""
        _announce("Serializing order lifecycle event")

        return {
            "event_id": self.event_id,
            "order_id": self.order_id,
            "idempotency_key": self.idempotency_key,
            "occurred_at": format_utc_datetime(self.occurred_at),
            "from_state": (
                self.from_state.value if self.from_state is not None else None
            ),
            "to_state": self.to_state.value,
            "reason": self.reason,
            "actor": self.actor,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "OrderEvent":
        """Reconstruct an event from its canonical internal representation."""
        _announce("Rehydrating order lifecycle event")

        data = require_mapping(payload, field="order_event")

        return cls(
            event_id=data.get("event_id"),
            order_id=data.get("order_id"),
            idempotency_key=data.get("idempotency_key"),
            occurred_at=data.get("occurred_at"),
            from_state=(
                coerce_order_state(data.get("from_state"))
                if data.get("from_state") is not None
                else None
            ),
            to_state=coerce_order_state(data.get("to_state")),
            reason=data.get("reason"),
            actor=data.get("actor"),
            metadata=data.get("metadata") or {},
        )


# Backward-compatible alias for the original scaffold placeholder.
Events = OrderEvent


__all__ = [
    "OrderEvent",
    "Events",
]