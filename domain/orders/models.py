"""
Canonical BIMAP Order aggregate.

The aggregate intentionally contains order-domain state only. Payment-provider
objects, queue jobs, storage clients, HTTP models and SLAI runtime objects
belong to higher architectural layers.

Dependency direction
--------------------
domain.utils
    ↑
states.py
    ↑
events.py
    ↑
models.py

``models.py`` must never import ``transitions.py``. Valid state movement is
owned exclusively by ``OrderTransitions`` so the aggregate cannot create a
circular dependency with the transition authority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .events import OrderEvent
from .states import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Orders Models")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("ORDERS", action, "info")
    logger.debug(action)


@dataclass(frozen=True, slots=True)
class Order:
    """
    Canonical BIMAP order aggregate.

    ``product_code`` and optional ``tier_code`` are stored as stable
    identifiers rather than importing a product implementation. The current
    ``domain/products/models.py`` scaffold is not yet implemented, and product
    catalog validation belongs to the product/application layer rather than the
    order state machine.

    ``version`` is an internal aggregate revision for optimistic concurrency.
    ``events`` is immutable and append-only from the aggregate's perspective.
    Cross-process idempotency still requires persistence to enforce uniqueness
    for ``(order_id, idempotency_key)`` atomically.
    """

    order_id: str
    product_code: str

    state: OrderState = OrderState.DRAFT

    created_at: datetime = dataclass_field(default_factory=utc_now)
    updated_at: datetime = dataclass_field(default_factory=utc_now)

    tier_code: str | None = None
    project_alias: str | None = None
    upload_session_id: str | None = None
    retention_expires_at: datetime | None = None

    version: int = 0

    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)
    events: tuple[OrderEvent, ...] = ()

    def __post_init__(self) -> None:
        _announce("Validating order aggregate")

        object.__setattr__(self, "order_id", require_text(self.order_id, field="order_id"))
        object.__setattr__(self, "product_code",require_text(self.product_code, field="product_code"))
        object.__setattr__(self, "state", coerce_order_state(self.state))

        created_at = ensure_utc_datetime(self.created_at, field="created_at")
        updated_at = ensure_utc_datetime(self.updated_at, field="updated_at")

        if updated_at < created_at:
            raise DomainInvariantError(
                "updated_at cannot precede created_at.",
                field="updated_at",
                context={"order_id": self.order_id},
            )

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

        object.__setattr__(self, "tier_code", optional_text(self.tier_code, field="tier_code"))
        object.__setattr__(self, "project_alias", optional_text(self.project_alias, field="project_alias"))
        object.__setattr__( self, "upload_session_id", optional_text(self.upload_session_id, field="upload_session_id"))

        if self.retention_expires_at is not None:
            retention_expires_at = ensure_utc_datetime(self.retention_expires_at, field="retention_expires_at")

            if retention_expires_at < created_at:
                raise DomainInvariantError(
                    "retention_expires_at cannot precede created_at.",
                    field="retention_expires_at",
                    context={"order_id": self.order_id},
                )

            object.__setattr__(self, "retention_expires_at", retention_expires_at)

        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError(
                "Order version must be an integer.",
                field="version",
                context={"received_type": type(self.version).__name__},
            )

        if self.version < 0:
            raise DomainValidationError(
                "Order version must be non-negative.",
                field="version",
                context={"received": self.version},
            )

        source_metadata = require_mapping(self.metadata, field="metadata")
        frozen_metadata = freeze_json_value(source_metadata, field="metadata")

        if not isinstance(frozen_metadata, Mapping):
            raise DomainValidationError(
                "Order metadata normalization did not produce a mapping.",
                field="metadata",
            )

        object.__setattr__(self, "metadata", frozen_metadata)

        normalized_events = self._normalize_events(self.events)
        object.__setattr__(self, "events", normalized_events)
        self._validate_event_history()

    @classmethod
    def create(
        cls,
        *,
        product_code: str,
        order_id: str | None = None,
        tier_code: str | None = None,
        project_alias: str | None = None,
        upload_session_id: str | None = None,
        created_at: datetime | str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "Order":
        """
        Create a new draft order.

        The application layer remains responsible for validating that the
        product/tier identifiers exist in the configured product catalog.
        """
        _announce("Creating draft order")

        timestamp = (
            utc_now()
            if created_at is None
            else ensure_utc_datetime(created_at, field="created_at")
        )

        return cls(
            order_id=order_id or uuid4().hex,
            product_code=product_code,
            state=OrderState.DRAFT,
            created_at=timestamp,
            updated_at=timestamp,
            tier_code=tier_code,
            project_alias=project_alias,
            upload_session_id=upload_session_id,
            version=0,
            metadata=metadata or {},
            events=(),
        )

    @staticmethod
    def _normalize_events(
        value: Iterable[OrderEvent] | None,
    ) -> tuple[OrderEvent, ...]:
        _announce("Normalizing order event history")

        if value is None:
            return ()

        if isinstance(value, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError(
                "events must be an iterable of OrderEvent objects.",
                field="events",
                context={"received_type": type(value).__name__},
            )

        try:
            iterator = iter(value)
        except TypeError as exc:
            raise DomainValidationError(
                "events must be iterable.",
                field="events",
                context={"received_type": type(value).__name__},
            ) from exc

        normalized: list[OrderEvent] = []
        for index, event in enumerate(iterator):
            if not isinstance(event, OrderEvent):
                raise DomainValidationError(
                    "events must contain only OrderEvent objects.",
                    field=f"events[{index}]",
                    context={"received_type": type(event).__name__},
                )
            normalized.append(event)

        return tuple(normalized)

    def _validate_event_history(self) -> None:
        _announce("Validating append-only order event history")

        if not self.events:
            return

        seen_event_ids: set[str] = set()
        seen_idempotency_keys: set[str] = set()
        previous_event: OrderEvent | None = None

        for index, event in enumerate(self.events):
            if event.order_id != self.order_id:
                raise DomainInvariantError(
                    "Order event belongs to a different order.",
                    field=f"events[{index}].order_id",
                    context={
                        "order_id": self.order_id,
                        "event_order_id": event.order_id,
                    },
                )

            if event.event_id in seen_event_ids:
                raise DomainInvariantError(
                    "Duplicate event_id in order event history.",
                    field=f"events[{index}].event_id",
                    context={"event_id": event.event_id},
                )

            if event.idempotency_key in seen_idempotency_keys:
                raise DomainInvariantError(
                    "Duplicate idempotency_key in order event history.",
                    field=f"events[{index}].idempotency_key",
                    context={"idempotency_key": event.idempotency_key},
                )

            seen_event_ids.add(event.event_id)
            seen_idempotency_keys.add(event.idempotency_key)

            if event.from_state is None:
                raise DomainInvariantError(
                    "Aggregate transition history must not contain creation events.",
                    field=f"events[{index}].from_state",
                    context={"event_id": event.event_id},
                )

            if previous_event is not None:
                if event.from_state is not previous_event.to_state:
                    raise DomainInvariantError(
                        "Order event history contains a discontinuous state chain.",
                        field=f"events[{index}].from_state",
                        context={
                            "previous_to_state": previous_event.to_state.value,
                            "received_from_state": event.from_state.value,
                        },
                    )

                if event.occurred_at < previous_event.occurred_at:
                    raise DomainInvariantError(
                        "Order event timestamps must be non-decreasing.",
                        field=f"events[{index}].occurred_at",
                    )

            previous_event = event

        last_event = self.events[-1]

        if last_event.to_state is not self.state:
            raise DomainInvariantError(
                "Order state does not match the last lifecycle event.",
                field="state",
                context={
                    "state": self.state.value,
                    "last_event_to_state": last_event.to_state.value,
                },
            )

        if self.updated_at < last_event.occurred_at:
            raise DomainInvariantError(
                "updated_at cannot precede the most recent lifecycle event.",
                field="updated_at",
                context={"order_id": self.order_id},
            )

        if self.version < len(self.events):
            raise DomainInvariantError(
                "Order version cannot be lower than retained state-event count.",
                field="version",
                context={
                    "version": self.version,
                    "event_count": len(self.events),
                },
            )

    def event_for_idempotency_key(self, idempotency_key: str) -> OrderEvent | None:
        """Return the event previously recorded for an idempotency key."""
        _announce("Looking up order idempotency key")

        normalized_key = require_text(idempotency_key, field="idempotency_key")

        for event in reversed(self.events):
            if event.idempotency_key == normalized_key:
                return event

        return None

    def with_upload_session(
        self,
        upload_session_id: str,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Order":
        """Return a new aggregate with an assigned upload-session identifier."""
        _announce("Assigning upload session to order")

        normalized_session_id = require_text(
            upload_session_id,
            field="upload_session_id",
        )
        timestamp = (
            utc_now()
            if changed_at is None
            else ensure_utc_datetime(changed_at, field="changed_at")
        )

        if timestamp < self.updated_at:
            raise DomainInvariantError(
                "changed_at cannot precede the current order update time.",
                field="changed_at",
                context={"order_id": self.order_id},
            )

        if self.upload_session_id == normalized_session_id:
            return self

        return replace(
            self,
            upload_session_id=normalized_session_id,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def with_retention_expiry(
        self,
        retention_expires_at: datetime | str | None,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Order":
        """Return a new aggregate with a revised retention-expiry timestamp."""
        _announce("Updating order retention expiry")

        timestamp = (
            utc_now()
            if changed_at is None
            else ensure_utc_datetime(changed_at, field="changed_at")
        )

        if timestamp < self.updated_at:
            raise DomainInvariantError(
                "changed_at cannot precede the current order update time.",
                field="changed_at",
                context={"order_id": self.order_id},
            )

        normalized_expiry = (
            ensure_utc_datetime(
                retention_expires_at,
                field="retention_expires_at",
            )
            if retention_expires_at is not None
            else None
        )

        if normalized_expiry is not None and normalized_expiry < self.created_at:
            raise DomainInvariantError(
                "retention_expires_at cannot precede created_at.",
                field="retention_expires_at",
                context={"order_id": self.order_id},
            )

        if self.retention_expires_at == normalized_expiry:
            return self

        return replace(
            self,
            retention_expires_at=normalized_expiry,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def _apply_state_event(self, event: OrderEvent) -> "Order":
        """
        Append an already-authorized state event and return the new aggregate.

        This validates aggregate invariants but intentionally does not decide
        whether the state transition is legal. Only ``OrderTransitions.apply``
        should authorize and call it.
        """
        _announce("Applying authorized order state event")

        if not isinstance(event, OrderEvent):
            raise DomainValidationError(
                "event must be an OrderEvent.",
                field="event",
                context={"received_type": type(event).__name__},
            )

        if event.order_id != self.order_id:
            raise DomainInvariantError(
                "Cannot apply an event belonging to another order.",
                field="event.order_id",
                context={
                    "order_id": self.order_id,
                    "event_order_id": event.order_id,
                },
            )

        existing = self.event_for_idempotency_key(event.idempotency_key)
        if existing is not None:
            if existing == event:
                return self

            raise DomainInvariantError(
                "Idempotency key is already bound to a different lifecycle event.",
                field="event.idempotency_key",
                context={
                    "idempotency_key": event.idempotency_key,
                    "existing_event_id": existing.event_id,
                    "received_event_id": event.event_id,
                },
            )

        if event.from_state is None:
            raise DomainInvariantError(
                "State transition event must have a from_state.",
                field="event.from_state",
            )

        if event.from_state is not self.state:
            raise DomainInvariantError(
                "State event does not start from the current order state.",
                field="event.from_state",
                context={
                    "current_state": self.state.value,
                    "event_from_state": event.from_state.value,
                },
            )

        if event.occurred_at < self.updated_at:
            raise DomainInvariantError(
                "Lifecycle event timestamp cannot precede updated_at.",
                field="event.occurred_at",
                context={"order_id": self.order_id},
            )

        return replace(
            self,
            state=event.to_state,
            updated_at=event.occurred_at,
            version=self.version + 1,
            events=self.events + (event,),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the aggregate into deterministic JSON-ready data."""
        _announce("Serializing order aggregate")

        return {
            "order_id": self.order_id,
            "product_code": self.product_code,
            "tier_code": self.tier_code,
            "project_alias": self.project_alias,
            "state": self.state.value,
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "upload_session_id": self.upload_session_id,
            "retention_expires_at": (
                format_utc_datetime(self.retention_expires_at)
                if self.retention_expires_at is not None
                else None
            ),
            "version": self.version,
            "metadata": thaw_json_value(self.metadata),
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "Order":
        """Reconstruct an order from its canonical internal representation."""
        _announce("Rehydrating order aggregate")

        data = require_mapping(payload, field="order")
        raw_events = data.get("events", ())

        if raw_events is None:
            raw_events = ()

        if isinstance(raw_events, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError(
                "events must be a sequence of event mappings.",
                field="events",
                context={"received_type": type(raw_events).__name__},
            )

        try:
            events = tuple(
                event
                if isinstance(event, OrderEvent)
                else OrderEvent.from_dict(event)
                for event in raw_events
            )
        except TypeError as exc:
            raise DomainValidationError(
                "events must be an iterable of event mappings.",
                field="events",
                context={"received_type": type(raw_events).__name__},
            ) from exc

        return cls(
            order_id=data.get("order_id"),
            product_code=data.get("product_code"),
            tier_code=data.get("tier_code"),
            project_alias=data.get("project_alias"),
            state=coerce_order_state(
                data.get("state", OrderState.DRAFT.value)
            ),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            upload_session_id=data.get("upload_session_id"),
            retention_expires_at=data.get("retention_expires_at"),
            version=data.get("version", 0),
            metadata=data.get("metadata") or {},
            events=events,
        )


# Backward-compatible alias for the original scaffold placeholder.
OrdersModels = Order


__all__ = [
    "Order",
    "OrdersModels",
]