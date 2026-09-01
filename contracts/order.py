"""
Versioned external BIMAP order contract.

The contract mirrors the canonical BIMAP order aggregate and its append-only
lifecycle events without importing transition policy. ``domain.orders`` remains
the authority for state identity and aggregate invariants, while
``domain/orders/transitions.py`` remains the sole authority for deciding which
state-to-state movements are legal.

This separation prevents the external contract from becoming a second order
state machine. The contract's job is deterministic validation, serialization,
and lossless conversion of persisted/order-boundary data.

Dependency direction
--------------------
domain.orders.states / events / models
domain.products.models
contracts.utils
contracts.versions
        ↑
contracts/order.py

``contracts/order.py`` MUST NOT import ``domain.orders.transitions``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Any, NoReturn

from .utils.contracts_errors import (
    ContractDeserializationError,
    ContractIntegrityError,
    ContractValidationError,
)
from .utils.contracts_helpers import (
    canonical_json_dumps,
    canonical_json_loads,
    ensure_supported_schema_version,
    to_json_primitive,
    validate_contract_fields,
)
from .versions import (
    ORDER_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ContractName,
)
from ..domain.orders.events import OrderEvent
from ..domain.orders.models import Order
from ..domain.orders.states import OrderState
from ..domain.products.models import ProductCode
from ..domain.utils.domain_errors import DomainError, DomainInvariantError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Order")
printer = PrettyPrinter()

_CONTRACT = ContractName.ORDER.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer order metadata."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "order_contract_method_start", "action": action})


def _translate_domain_error(
    message: str,
    *,
    field: str | None = None,
    cause: BaseException,
) -> NoReturn:
    if isinstance(cause, DomainInvariantError):
        raise ContractIntegrityError(
            message,
            contract=_CONTRACT,
            field=field,
            cause=cause,
        ) from cause

    raise ContractValidationError(
        message,
        contract=_CONTRACT,
        field=field,
        cause=cause,
    ) from cause


@dataclass(frozen=True, slots=True)
class OrderEventContract:
    """Stable nested representation of one append-only order lifecycle event."""

    event_id: str
    order_id: str
    idempotency_key: str
    occurred_at: str | datetime
    from_state: OrderState | str | None
    to_state: OrderState | str
    reason: str | None = None
    actor: str | None = None
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        _announce("Validating order-event contract")
        try:
            occurred_at = (
                self.occurred_at
                if isinstance(self.occurred_at, datetime)
                else datetime.fromisoformat(self.occurred_at)
            )
            from_state = (
                self.from_state
                if self.from_state is None or isinstance(self.from_state, OrderState)
                else OrderState(self.from_state)
            )
            to_state = (
                self.to_state
                if isinstance(self.to_state, OrderState)
                else OrderState(self.to_state)
            )
            domain_event = OrderEvent(
                event_id=self.event_id,
                order_id=self.order_id,
                idempotency_key=self.idempotency_key,
                occurred_at=occurred_at,
                from_state=from_state,
                to_state=to_state,
                reason=self.reason,
                actor=self.actor,
                metadata=self.metadata,
            )
        except (DomainError, TypeError, ValueError) as exc:
            _translate_domain_error(
                "Order-event contract violates a canonical order-event constraint.",
                field="event",
                cause=exc,
            )

        payload = domain_event.to_dict()
        object.__setattr__(self, "event_id", payload["event_id"])
        object.__setattr__(self, "order_id", payload["order_id"])
        object.__setattr__(self, "idempotency_key", payload["idempotency_key"])
        object.__setattr__(self, "occurred_at", payload["occurred_at"])
        object.__setattr__(self, "from_state", domain_event.from_state)
        object.__setattr__(self, "to_state", domain_event.to_state)
        object.__setattr__(self, "reason", payload["reason"])
        object.__setattr__(self, "actor", payload["actor"])
        object.__setattr__(self, "metadata", payload["metadata"])

    def to_dict(self) -> dict[str, Any]:
        """Return the nested JSON-ready lifecycle-event representation."""
        _announce("Serializing order-event contract")
        return {
            "event_id": self.event_id,
            "order_id": self.order_id,
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at,
            "from_state": (
                self.from_state.value if isinstance(self.from_state, OrderState) else None
            ),
            "to_state": self.to_state.value,
            "reason": self.reason,
            "actor": self.actor,
            "metadata": to_json_primitive(
                self.metadata,
                contract=_CONTRACT,
                field="event.metadata",
            ),
        }

    def to_domain(self) -> OrderEvent:
        """Convert the nested contract to the canonical domain OrderEvent."""
        _announce("Converting order-event contract to domain")
        try:
            return OrderEvent(
                event_id=self.event_id,
                order_id=self.order_id,
                idempotency_key=self.idempotency_key,
                occurred_at=self.occurred_at,
                from_state=self.from_state,
                to_state=self.to_state,
                reason=self.reason,
                actor=self.actor,
                metadata=self.metadata,
            )
        except DomainError as exc:
            _translate_domain_error(
                "Order-event contract cannot be converted to domain form.",
                field="event",
                cause=exc,
            )

    @classmethod
    def from_domain(cls, event: OrderEvent) -> "OrderEventContract":
        """Construct a nested order-event contract from a domain event."""
        _announce("Creating order-event contract from domain")
        if not isinstance(event, OrderEvent):
            raise ContractValidationError(
                "event must be an OrderEvent instance.",
                contract=_CONTRACT,
                field="event",
                context={"received_type": type(event).__name__},
            )
        payload = event.to_dict()
        return cls(
            event_id=payload["event_id"],
            order_id=payload["order_id"],
            idempotency_key=payload["idempotency_key"],
            occurred_at=payload["occurred_at"],
            from_state=payload["from_state"],
            to_state=payload["to_state"],
            reason=payload["reason"],
            actor=payload["actor"],
            metadata=payload["metadata"],
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OrderEventContract":
        """Parse a strict nested lifecycle-event mapping."""
        _announce("Deserializing order-event contract")
        data = validate_contract_fields(
            payload,
            required=(
                "event_id",
                "order_id",
                "idempotency_key",
                "occurred_at",
                "from_state",
                "to_state",
                "reason",
                "actor",
                "metadata",
            ),
            contract=_CONTRACT,
        )
        return cls(
            event_id=data["event_id"],
            order_id=data["order_id"],
            idempotency_key=data["idempotency_key"],
            occurred_at=data["occurred_at"],
            from_state=data["from_state"],
            to_state=data["to_state"],
            reason=data["reason"],
            actor=data["actor"],
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True, slots=True)
class OrderContract:
    """Stable versioned representation of the canonical BIMAP Order aggregate."""

    order_id: str
    product_code: ProductCode | str
    state: OrderState | str
    created_at: str | datetime
    updated_at: str | datetime
    version: int

    tier_code: str | None = None
    project_alias: str | None = None
    upload_session_id: str | None = None
    retention_expires_at: str | datetime | None = None
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)
    events: tuple[OrderEventContract | OrderEvent | Mapping[str, Any], ...] = ()
    schema_version: str = ORDER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating order contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        try:
            product_code = ProductCode.parse(self.product_code)
            state = OrderState.parse(self.state)
        except DomainError as exc:
            _translate_domain_error(
                "Order contract contains an invalid product or state identifier.",
                cause=exc,
            )

        normalized_events: list[OrderEventContract] = []
        try:
            iterator = iter(self.events)
        except TypeError as exc:
            raise ContractValidationError(
                "events must be iterable.",
                contract=_CONTRACT,
                field="events",
                context={"received_type": type(self.events).__name__},
                cause=exc,
            ) from exc

        if isinstance(self.events, (str, bytes, bytearray, Mapping)):
            raise ContractValidationError(
                "events must be a sequence of lifecycle events, not a scalar/mapping.",
                contract=_CONTRACT,
                field="events",
                context={"received_type": type(self.events).__name__},
            )

        for index, event in enumerate(iterator):
            if isinstance(event, OrderEventContract):
                normalized = event
            elif isinstance(event, OrderEvent):
                normalized = OrderEventContract.from_domain(event)
            elif isinstance(event, Mapping):
                normalized = OrderEventContract.from_dict(event)
            else:
                raise ContractValidationError(
                    "events contains an unsupported value.",
                    contract=_CONTRACT,
                    field=f"events[{index}]",
                    context={"received_type": type(event).__name__},
                )
            normalized_events.append(normalized)

        try:
            domain_order = Order(
                order_id=self.order_id,
                product_code=product_code.value,
                tier_code=self.tier_code,
                project_alias=self.project_alias,
                state=state,
                created_at=self.created_at,
                updated_at=self.updated_at,
                upload_session_id=self.upload_session_id,
                retention_expires_at=self.retention_expires_at,
                version=self.version,
                metadata=self.metadata,
                events=tuple(event.to_domain() for event in normalized_events),
            )
        except DomainError as exc:
            _translate_domain_error(
                "Order contract violates canonical order aggregate invariants.",
                cause=exc,
            )

        payload = domain_order.to_dict()
        object.__setattr__(self, "schema_version", str(self.schema_version).strip())
        object.__setattr__(self, "order_id", payload["order_id"])
        object.__setattr__(self, "product_code", product_code)
        object.__setattr__(self, "tier_code", payload["tier_code"])
        object.__setattr__(self, "project_alias", payload["project_alias"])
        object.__setattr__(self, "state", domain_order.state)
        object.__setattr__(self, "created_at", payload["created_at"])
        object.__setattr__(self, "updated_at", payload["updated_at"])
        object.__setattr__(self, "upload_session_id", payload["upload_session_id"])
        object.__setattr__(self, "retention_expires_at", payload["retention_expires_at"])
        object.__setattr__(self, "version", payload["version"])
        object.__setattr__(self, "metadata", payload["metadata"])
        object.__setattr__(self, "events", tuple(normalized_events))

        logger.debug(
            {
                "event": "order_contract_validated",
                "order_id": self.order_id,
                "product_code": product_code.value,
                "state": domain_order.state.value,
                "version": self.version,
                "event_count": len(normalized_events),
            }
        )

    @staticmethod
    def _resolve_product_code(value: ProductCode | str) -> ProductCode:
        return value if isinstance(value, ProductCode) else ProductCode.parse(value)

    @staticmethod
    def _resolve_order_state(value: OrderState | str) -> OrderState:
        return value if isinstance(value, OrderState) else OrderState.parse(value)

    @staticmethod
    def _resolve_datetime(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            return value
        # Handle UTC marker 'Z' by converting to ISO offset format
        if isinstance(value, str) and value.endswith('Z'):
            value = value[:-1] + '+00:00'
        return datetime.fromisoformat(value)

    @staticmethod
    def _event_to_dict(
        event: OrderEventContract | OrderEvent | Mapping[str, Any],
    ) -> dict[str, Any]:
        if isinstance(event, OrderEventContract):
            return event.to_dict()
        if isinstance(event, OrderEvent):
            return event.to_dict()
        return OrderEventContract.from_dict(event).to_dict()

    @staticmethod
    def _event_to_domain(
        event: OrderEventContract | OrderEvent | Mapping[str, Any],
    ) -> OrderEvent:
        if isinstance(event, OrderEventContract):
            return event.to_domain()
        if isinstance(event, OrderEvent):
            return event
        return OrderEventContract.from_dict(event).to_domain()

    def to_dict(self) -> dict[str, Any]:
        """Return the full versioned JSON-ready order representation."""
        _announce("Serializing order contract")
        product_code = self._resolve_product_code(self.product_code)
        state = self._resolve_order_state(self.state)
        return {
            "schema_version": self.schema_version,
            "order_id": self.order_id,
            "product_code": product_code.value,
            "tier_code": self.tier_code,
            "project_alias": self.project_alias,
            "state": state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "upload_session_id": self.upload_session_id,
            "retention_expires_at": self.retention_expires_at,
            "version": self.version,
            "metadata": to_json_primitive(
                self.metadata,
                contract=_CONTRACT,
                field="metadata",
            ),
            "events": [self._event_to_dict(event) for event in self.events],
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the order using BIMAP canonical JSON rules."""
        _announce("Encoding order contract JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    def to_domain(self) -> Order:
        """Convert the external order contract to the canonical domain aggregate."""
        _announce("Converting order contract to domain")
        try:
            product_code = self._resolve_product_code(self.product_code)
            state = self._resolve_order_state(self.state)
            created_at = self._resolve_datetime(self.created_at)
            updated_at = self._resolve_datetime(self.updated_at)
            retention_expires_at = self.retention_expires_at
            if isinstance(retention_expires_at, str):
                retention_expires_at = self._resolve_datetime(retention_expires_at)
            return Order(
                order_id=self.order_id,
                product_code=product_code.value,
                tier_code=self.tier_code,
                project_alias=self.project_alias,
                state=state,
                created_at=created_at,
                updated_at=updated_at,
                upload_session_id=self.upload_session_id,
                retention_expires_at=retention_expires_at,
                version=self.version,
                metadata=self.metadata,
                events=tuple(self._event_to_domain(event) for event in self.events),
            )
        except DomainError as exc:
            _translate_domain_error(
                "Order contract cannot be converted to domain form.",
                cause=exc,
            )

    @classmethod
    def from_domain(
        cls,
        order: Order,
        *,
        schema_version: str = ORDER_SCHEMA_VERSION,
    ) -> "OrderContract":
        """Construct an external order contract from a canonical domain Order."""
        _announce("Creating order contract from domain")
        if not isinstance(order, Order):
            raise ContractValidationError(
                "order must be an Order instance.",
                contract=_CONTRACT,
                field="order",
                context={"received_type": type(order).__name__},
            )

        payload = order.to_dict()
        return cls(
            schema_version=schema_version,
            order_id=payload["order_id"],
            product_code=payload["product_code"],
            tier_code=payload["tier_code"],
            project_alias=payload["project_alias"],
            state=payload["state"],
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
            upload_session_id=payload["upload_session_id"],
            retention_expires_at=payload["retention_expires_at"],
            version=payload["version"],
            metadata=payload["metadata"],
            events=tuple(OrderEventContract.from_domain(event) for event in order.events),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OrderContract":
        """Parse a strict versioned order mapping."""
        _announce("Deserializing order contract")
        data = validate_contract_fields(
            payload,
            required=(
                "schema_version",
                "order_id",
                "product_code",
                "state",
                "created_at",
                "updated_at",
                "version",
            ),
            optional=(
                "tier_code",
                "project_alias",
                "upload_session_id",
                "retention_expires_at",
                "metadata",
                "events",
            ),
            contract=_CONTRACT,
        )
        raw_events = data.get("events", ())
        if raw_events is None:
            raw_events = ()
        if isinstance(raw_events, (str, bytes, bytearray, Mapping)):
            raise ContractValidationError(
                "events must be an array of lifecycle-event objects.",
                contract=_CONTRACT,
                field="events",
                context={"received_type": type(raw_events).__name__},
            )
        try:
            events = tuple(raw_events)
        except TypeError as exc:
            raise ContractValidationError(
                "events must be iterable.",
                contract=_CONTRACT,
                field="events",
                context={"received_type": type(raw_events).__name__},
                cause=exc,
            ) from exc

        return cls(
            schema_version=data["schema_version"],
            order_id=data["order_id"],
            product_code=data["product_code"],
            tier_code=data.get("tier_code"),
            project_alias=data.get("project_alias"),
            state=data["state"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            upload_session_id=data.get("upload_session_id"),
            retention_expires_at=data.get("retention_expires_at"),
            version=data["version"],
            metadata=data.get("metadata") or {},
            events=events,
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "OrderContract":
        """Decode canonical JSON and validate an order contract."""
        _announce("Decoding order contract JSON")
        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Order JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


# Backward-compatible name retained from the initial scaffold.
ContractsOrders = OrderContract


__all__ = [
    "OrderEventContract",
    "OrderContract",
    "ContractsOrders",
]


if __name__ == "__main__":
    print("\n=== Running Order Contract Self-Test ===\n")
    printer.status("TEST", "Order contract module initialized", "info")

    contract = OrderContract(
        order_id="ORD-0001",
        product_code="family_audit",
        state="draft",
        created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-01T00:00:00Z",
        version=0,
        metadata={},
        events=(),
    )
    assert OrderContract.from_json(contract.to_json()) == contract
    assert OrderContract.from_domain(contract.to_domain()) == contract
    printer.status("PASS", "Order contract round trip", "success")

    print("\n=== Test ran successfully ===\n")