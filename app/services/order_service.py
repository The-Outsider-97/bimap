"""
Application service for BIMAP order lifecycle and payment coordination.

``OrderService`` is the Level-5 owner of application-level order orchestration.
It delegates lifecycle legality and event/idempotency semantics to
``OrderTransitions``; persistence and optimistic concurrency to ``Repository``;
commercial product/tier identity to ``ProductCatalog``; configured scope limits
to ``ProductLimits``; current time to ``Clock``; and provider interaction to the
``Payment`` port.

No product prices, currency rules, commercial limits, retention durations,
refund-provider operations, or retry loops are hard-coded here.  In particular,
the current payment port has no provider-refund command, so a structural
``REFUNDED`` order transition is never presented as proof that money was moved
by a payment provider.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..ports.clock import Clock
from ..ports.payment import Payment, PaymentCheckout, PaymentEvent, PaymentStatus
from ..ports.repositories import Repository
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from ...domain.orders.transitions import OrderTransitions
from ...domain.products.limits import LimitEvaluation, ProductLimits
from ...domain.products.models import ProductCatalog, ProductCode, ProductTier
from ...domain.utils.domain_errors import DomainError, DomainInvariantError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Order Service")
printer = PrettyPrinter()

_COMPONENT = "order_service"


def _translate_domain_error(
    exc: DomainError,
    *,
    operation: str,
    message: str,
    field: str | None = None,
) -> AppError:
    """Translate a lower domain failure without string-matching its semantics."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Translating order-domain failure",
        event="order_service_domain_error_translate_start",
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


def _normalize_metadata(value: Mapping[str, Any] | None, *, operation: str) -> dict[str, Any]:
    """Normalize optional lifecycle metadata as deterministic JSON data."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing order service metadata",
        event="order_service_metadata_normalize_start",
        context={"operation": operation},
    )
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UnsupportedAppInputError(
            "Order service metadata must be a mapping or None.",
            component=_COMPONENT,
            operation=operation,
            field="metadata",
            context={"received_type": type(value).__name__},
        )
    primitive = to_app_primitive(dict(value), field=f"{operation}.metadata")
    if not isinstance(primitive, dict):
        raise AppIntegrityError(
            "Order service metadata did not normalize to a JSON object.",
            component=_COMPONENT,
            operation=operation,
            field="metadata",
        )
    return primitive


@dataclass(frozen=True, slots=True)
class PaymentHandlingResult:
    """Bind one verified payment event to the authoritative resulting order."""

    event: PaymentEvent
    order: Order
    state_changed: bool

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating payment handling result",
            event="order_service_payment_result_validate_start",
            context={
                "order_id": getattr(self.order, "order_id", None),
                "event_id": getattr(self.event, "event_id", None),
            },
        )
        if not isinstance(self.event, PaymentEvent):
            raise UnsupportedAppInputError(
                "PaymentHandlingResult requires a PaymentEvent.",
                component=_COMPONENT,
                operation="validate_payment_result",
                field="event",
                context={"received_type": type(self.event).__name__},
            )
        if not isinstance(self.order, Order):
            raise UnsupportedAppInputError(
                "PaymentHandlingResult requires an Order.",
                component=_COMPONENT,
                operation="validate_payment_result",
                field="order",
                context={"received_type": type(self.order).__name__},
            )
        if self.event.order_id != self.order.order_id:
            raise AppIntegrityError(
                "Payment event/result order identity is inconsistent.",
                component=_COMPONENT,
                operation="validate_payment_result",
                field="event.order_id",
                context={
                    "event_order_id": self.event.order_id,
                    "order_id": self.order.order_id,
                },
            )
        if not isinstance(self.state_changed, bool):
            raise AppValidationError(
                "state_changed must be boolean.",
                component=_COMPONENT,
                operation="validate_payment_result",
                field="state_changed",
                context={"received_type": type(self.state_changed).__name__},
            )


class OrderService:
    """Coordinate authoritative order lifecycle, product scope, and payments."""

    def __init__(
        self,
        repository: Repository,
        payment: Payment,
        clock: Clock,
        *,
        catalog: ProductCatalog,
        product_limits: Iterable[ProductLimits] = (),
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing order service",
            event="order_service_init_start",
        )

        if not isinstance(repository, Repository):
            raise AppConfigurationError(
                "repository must implement the BIMAP Repository port.",
                component=_COMPONENT,
                operation="initialize",
                field="repository",
                context={"received_type": type(repository).__name__},
            )
        if not isinstance(payment, Payment):
            raise AppConfigurationError(
                "payment must implement the BIMAP Payment port.",
                component=_COMPONENT,
                operation="initialize",
                field="payment",
                context={"received_type": type(payment).__name__},
            )
        if not isinstance(clock, Clock):
            raise AppConfigurationError(
                "clock must implement the BIMAP Clock port.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
                context={"received_type": type(clock).__name__},
            )
        if not isinstance(catalog, ProductCatalog):
            raise AppConfigurationError(
                "catalog must be a ProductCatalog.",
                component=_COMPONENT,
                operation="initialize",
                field="catalog",
                context={"received_type": type(catalog).__name__},
            )

        if isinstance(product_limits, (str, bytes, bytearray, Mapping)):
            raise AppConfigurationError(
                "product_limits must be an iterable of ProductLimits.",
                component=_COMPONENT,
                operation="initialize",
                field="product_limits",
                context={"received_type": type(product_limits).__name__},
            )
        try:
            limits = tuple(product_limits)
        except TypeError as exc:
            raise AppConfigurationError(
                "product_limits must be iterable.",
                component=_COMPONENT,
                operation="initialize",
                field="product_limits",
                context={"received_type": type(product_limits).__name__},
                cause=exc,
            ) from exc

        seen_limit_keys: set[tuple[ProductCode, str | None]] = set()
        for index, configured in enumerate(limits):
            if not isinstance(configured, ProductLimits):
                raise AppConfigurationError(
                    "product_limits contains a non-ProductLimits value.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"product_limits[{index}]",
                    context={"received_type": type(configured).__name__},
                )
            try:
                configured.assert_catalog_membership(catalog)
            except DomainError as exc:
                raise AppConfigurationError(
                    "Configured product limits do not belong to the supplied catalog.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"product_limits[{index}]",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc

            key = (configured.product_code, configured.tier_code)
            if key in seen_limit_keys:
                raise AppConfigurationError(
                    "Duplicate ProductLimits configuration for one product/tier scope.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="product_limits",
                    context={
                        "product_code": configured.product_code.value,
                        "tier_code": configured.tier_code,
                    },
                )
            seen_limit_keys.add(key)

        self.repository = repository
        self.payment = payment
        self.clock = clock
        self.catalog = catalog
        self.product_limits = limits

        logger.info(
            {
                "event": "order_service_initialized",
                "configured_product_count": len(catalog.products),
                "configured_tier_count": len(catalog.tiers),
                "configured_limit_scope_count": len(limits),
            }
        )

    def find_order(self, order_id: str) -> Order | None:
        """Return one order, or ``None`` when it does not exist."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding order",
            event="order_service_find_start",
            context={"order_id": order_id},
        )
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="find_order",
        )
        return self.repository.get_order(target)

    def get_order(self, order_id: str) -> Order:
        """Return one authoritative order or fail when absent."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading required order",
            event="order_service_get_start",
            context={"order_id": order_id},
        )
        order = self.find_order(order_id)
        if order is None:
            raise AppValidationError(
                "Order does not exist.",
                component=_COMPONENT,
                operation="get_order",
                field="order_id",
                context={"order_id": order_id},
            )
        return order

    def create_order(
        self,
        *,
        product_code: ProductCode | str,
        tier_code: str | None = None,
        order_id: str | None = None,
        project_alias: str | None = None,
        upload_session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Order:
        """Create and persist one catalog-backed draft order."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating order",
            event="order_service_create_start",
            context={"product_code": str(product_code), "has_tier": tier_code is not None},
        )

        try:
            product = self.catalog.get_product(product_code)
            tier: ProductTier | None = None
            if tier_code is not None:
                tier = self.catalog.get_tier(product.code, tier_code)

            order = Order.create(
                order_id=order_id,
                product_code=product.code.value,
                tier_code=None if tier is None else tier.tier_code,
                project_alias=project_alias,
                upload_session_id=upload_session_id,
                created_at=self.clock.now(),
                metadata=_normalize_metadata(metadata, operation="create_order"),
            )
        except AppError:
            raise
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="create_order",
                message="Order creation input does not satisfy product/order constraints.",
            ) from exc

        existing = self.repository.get_order(order.order_id)
        if existing is not None:
            raise AppIntegrityError(
                "An order with the requested identifier already exists.",
                component=_COMPONENT,
                operation="create_order",
                field="order_id",
                context={"order_id": order.order_id},
            )

        persisted = self.repository.save_order(order, expected_version=None)
        self._require_persisted_order(persisted, expected=order, operation="create_order")
        logger.info(
            {
                "event": "order_service_order_created",
                "order_id": persisted.order_id,
                "product_code": persisted.product_code,
                "tier_code": persisted.tier_code,
            }
        )
        return persisted

    def _require_persisted_order(
        self,
        persisted: Order,
        *,
        expected: Order,
        operation: str,
    ) -> None:
        """Validate repository write-back against the aggregate being persisted."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating persisted order",
            event="order_service_persisted_validate_start",
            context={"operation": operation, "order_id": expected.order_id},
        )
        if not isinstance(persisted, Order):
            raise AppIntegrityError(
                "Repository returned an unsupported persisted order.",
                component=_COMPONENT,
                operation=operation,
                field="persisted_order",
                context={"received_type": type(persisted).__name__},
            )
        if (
            persisted.order_id != expected.order_id
            or persisted.version != expected.version
            or persisted.state is not expected.state
        ):
            raise AppIntegrityError(
                "Repository write-back changed order identity, version, or lifecycle state.",
                component=_COMPONENT,
                operation=operation,
                field="persisted_order",
                context={
                    "expected_order_id": expected.order_id,
                    "returned_order_id": persisted.order_id,
                    "expected_version": expected.version,
                    "returned_version": persisted.version,
                    "expected_state": expected.state.value,
                    "returned_state": persisted.state.value,
                },
            )

    def transition(
        self,
        order_id: str,
        target: OrderState | str,
        *,
        idempotency_key: str,
        occurred_at: datetime | str | None = None,
        reason: str | None = None,
        actor: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Order:
        """Apply and atomically persist one domain-authorized lifecycle transition."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Transitioning order lifecycle",
            event="order_service_transition_start",
            context={"order_id": order_id, "target": str(target)},
        )

        order = self.get_order(order_id)
        return self._transition_loaded(
            order,
            target,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            reason=reason,
            actor=actor,
            metadata=metadata,
            operation="transition",
        )

    def _transition_loaded(
        self,
        order: Order,
        target: OrderState | str,
        *,
        idempotency_key: str,
        occurred_at: datetime | str | None,
        reason: str | None,
        actor: str | None,
        metadata: Mapping[str, Any] | None,
        operation: str,
    ) -> Order:
        """Apply one transition to an already-loaded order using optimistic CAS."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Applying loaded order transition",
            event="order_service_transition_loaded_start",
            context={"operation": operation, "order_id": order.order_id, "target": str(target)},
        )

        key = require_app_text(
            idempotency_key,
            field="idempotency_key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
            max_length=512,
        )
        normalized_reason = optional_app_text(
            reason,
            field="reason",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )
        normalized_actor = optional_app_text(
            actor,
            field="actor",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )
        timestamp = (
            self.clock.now()
            if occurred_at is None
            else ensure_app_utc_datetime(
                occurred_at,
                field="occurred_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation=operation,
            )
        )
        event_metadata = _normalize_metadata(metadata, operation=operation)

        try:
            transition = OrderTransitions.apply(
                order,
                target,
                idempotency_key=key,
                occurred_at=timestamp,
                expected_version=order.version,
                reason=normalized_reason,
                actor=normalized_actor,
                metadata=event_metadata,
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation=operation,
                message="Requested order lifecycle transition is not valid.",
                field="target_state",
            ) from exc

        if not transition.applied:
            return transition.order

        persisted = self.repository.save_order(
            transition.order,
            expected_version=order.version,
        )
        self._require_persisted_order(
            persisted,
            expected=transition.order,
            operation=operation,
        )
        return persisted

    def start_upload(self, order_id: str, *, idempotency_key: str, actor: str | None = None) -> Order:
        """Move a draft order into the canonical uploading state."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Starting order upload phase",
            event="order_service_start_upload_start",
            context={"order_id": order_id},
        )
        return self.transition(
            order_id,
            OrderState.UPLOADING,
            idempotency_key=idempotency_key,
            actor=actor,
        )

    def validate_order_usage(self, order_id: str, usage: Mapping[str, int]) -> tuple[LimitEvaluation, ...]:
        """Evaluate an order against all configured applicable product limits."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating order usage against product limits",
            event="order_service_usage_validate_start",
            context={"order_id": order_id},
        )

        order = self.get_order(order_id)
        if not isinstance(usage, Mapping):
            raise UnsupportedAppInputError(
                "usage must be a mapping of configured limit key to observed integer.",
                component=_COMPONENT,
                operation="validate_order_usage",
                field="usage",
                context={"received_type": type(usage).__name__},
            )

        try:
            product = ProductCode.parse(order.product_code)
            applicable = tuple(
                item
                for item in self.product_limits
                if item.product_code is product
                and (item.tier_code is None or item.tier_code == order.tier_code)
            )
            if not applicable:
                return ()

            configured_keys = {
                constraint.key
                for configured in applicable
                for constraint in configured.constraints
            }
            supplied_keys = set(usage)
            unknown = tuple(sorted(supplied_keys - configured_keys))
            if unknown:
                raise AppValidationError(
                    "usage contains keys that are not configured for this order's product/tier.",
                    component=_COMPONENT,
                    operation="validate_order_usage",
                    field="usage",
                    context={"unknown_keys": unknown},
                )

            evaluations: list[LimitEvaluation] = []
            for configured in applicable:
                scoped_usage = {
                    constraint.key: usage[constraint.key]
                    for constraint in configured.constraints
                    if constraint.key in usage
                }
                configured.assert_within(scoped_usage)
                evaluations.extend(configured.evaluate(scoped_usage))
            return tuple(evaluations)
        except AppError:
            raise
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="validate_order_usage",
                message="Order usage violates configured product-limit policy.",
                field="usage",
            ) from exc

    def begin_checkout(self, order_id: str, *, idempotency_key: str, actor: str | None = None) -> PaymentCheckout:
        """Enter payment-pending state and idempotently create provider checkout.

        The same caller-supplied idempotency key is intentionally used for the
        lifecycle transition and provider checkout.  If provider invocation
        fails after the order transition is persisted, retrying the method with
        the same key replays the already-recorded transition and retries the
        payment port without creating a new lifecycle event.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Beginning order checkout",
            event="order_service_checkout_start",
            context={"order_id": order_id},
        )

        order = self.get_order(order_id)
        if order.tier_code is None:
            raise AppValidationError(
                "Order has no selected product tier for checkout.",
                component=_COMPONENT,
                operation="begin_checkout",
                field="order.tier_code",
                context={"order_id": order.order_id},
            )

        try:
            tier = self.catalog.get_tier(order.product_code, order.tier_code)
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="begin_checkout",
                message="Order references a product tier that is not available for checkout.",
                field="order.tier_code",
            ) from exc

        if not tier.is_priced:
            raise AppValidationError(
                "Selected order tier is not priced and cannot start checkout.",
                component=_COMPONENT,
                operation="begin_checkout",
                field="tier.unit_price",
                context={
                    "order_id": order.order_id,
                    "product_code": order.product_code,
                    "tier_code": order.tier_code,
                },
            )

        pending = self._transition_loaded(
            order,
            OrderState.PAYMENT_PENDING,
            idempotency_key=idempotency_key,
            occurred_at=None,
            reason=None,
            actor=actor,
            metadata=None,
            operation="begin_checkout",
        )
        return self.payment.create_checkout(
            pending,
            tier,
            idempotency_key=idempotency_key,
        )

    def handle_payment_event(
        self,
        payload: bytes | bytearray | memoryview,
        *,
        signature: str,
    ) -> PaymentHandlingResult:
        """Verify one provider event and apply its supported lifecycle effect."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling verified payment event",
            event="order_service_payment_event_start",
        )

        event = self.payment.verify_event(payload, signature=signature)
        order = self.get_order(event.order_id)
        if order.tier_code is None:
            raise AppIntegrityError(
                "Payment event targets an order without a selected product tier.",
                component=_COMPONENT,
                operation="handle_payment_event",
                field="order.tier_code",
                context={"order_id": order.order_id, "event_id": event.event_id},
            )

        try:
            tier = self.catalog.get_tier(order.product_code, order.tier_code)
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="handle_payment_event",
                message="Payment event order references an unavailable product tier.",
                field="order.tier_code",
            ) from exc

        self.payment.validate_event_for_order(event, order, tier)
        status = PaymentStatus.parse(event.status)

        if status is PaymentStatus.PENDING:
            return PaymentHandlingResult(event=event, order=order, state_changed=False)

        target_by_status = {
            PaymentStatus.SUCCEEDED: OrderState.PAID,
            PaymentStatus.FAILED: OrderState.PAYMENT_FAILED,
            PaymentStatus.REFUNDED: OrderState.REFUNDED,
        }
        target = target_by_status[status]

        # Providers can emit multiple independently identified notifications for
        # the same material state.  Once the authoritative order already reflects
        # that state, another equivalent notification is a validated state no-op.
        if order.state is target:
            logger.info(
                {
                    "event": "order_service_payment_state_already_applied",
                    "order_id": order.order_id,
                    "event_id": event.event_id,
                    "status": status.value,
                }
            )
            return PaymentHandlingResult(event=event, order=order, state_changed=False)

        updated = self._transition_loaded(
            order,
            target,
            idempotency_key=event.event_id,
            occurred_at=event.occurred_at,
            reason=None,
            actor=event.provider_name,
            metadata={
                "payment_event_id": event.event_id,
                "payment_status": status.value,
                "provider_name": event.provider_name,
            },
            operation="handle_payment_event",
        )
        return PaymentHandlingResult(event=event, order=updated, state_changed=True)

    def cancel_order(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        actor: str | None = None,
    ) -> Order:
        """Request the canonical pre-payment cancellation transition."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Cancelling order",
            event="order_service_cancel_start",
            context={"order_id": order_id},
        )
        return self.transition(
            order_id,
            OrderState.CANCELLED,
            idempotency_key=idempotency_key,
            reason=reason,
            actor=actor,
        )

    def set_retention_expiry(self, order_id: str, expires_at: datetime | str | None) -> Order:
        """Persist an explicit retention expiry without inventing its duration."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Setting order retention expiry",
            event="order_service_retention_set_start",
            context={"order_id": order_id, "has_expiry": expires_at is not None},
        )

        order = self.get_order(order_id)
        normalized_expiry = (
            None
            if expires_at is None
            else ensure_app_utc_datetime(
                expires_at,
                field="expires_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="set_retention_expiry",
            )
        )
        try:
            updated = order.with_retention_expiry(normalized_expiry, changed_at=self.clock.now())
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="set_retention_expiry",
                message="Retention expiry is inconsistent with the order timeline.",
                field="expires_at",
            ) from exc

        if updated is order:
            return order

        persisted = self.repository.save_order(updated, expected_version=order.version)
        self._require_persisted_order(
            persisted,
            expected=updated,
            operation="set_retention_expiry",
        )
        return persisted

    def expire_order_if_due(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        actor: str | None = None,
    ) -> Order:
        """Transition an order to EXPIRED only when its configured expiry is due."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Evaluating order expiry",
            event="order_service_expiry_check_start",
            context={"order_id": order_id},
        )

        order = self.get_order(order_id)
        if order.retention_expires_at is None:
            raise AppValidationError(
                "Order has no configured retention expiry.",
                component=_COMPONENT,
                operation="expire_order_if_due",
                field="order.retention_expires_at",
                context={"order_id": order.order_id},
            )
        if not self.clock.is_expired(order.retention_expires_at):
            return order
        if order.state is OrderState.EXPIRED:
            return order

        return self._transition_loaded(
            order,
            OrderState.EXPIRED,
            idempotency_key=idempotency_key,
            occurred_at=None,
            reason=None,
            actor=actor,
            metadata=None,
            operation="expire_order_if_due",
        )


__all__ = [
    "PaymentHandlingResult",
    "OrderService",
]