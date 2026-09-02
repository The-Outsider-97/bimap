"""
Provider-neutral payment application port for BIMAP.

The payment port is a Level-4 dependency-inversion boundary. It exposes the
minimum application-facing concepts required by the existing checkout/payment
workflow without embedding a specific payment provider, SDK, HTTP framework, or
persistence implementation.

Architectural boundaries
------------------------
* Product/tier pricing remains authoritative in ``domain.products``.
* Order lifecycle transitions remain authoritative in
  ``domain.orders.transitions``. This port never mutates an Order or decides a
  legal state transition.
* A provider operation failure is distinct from a valid business outcome such
  as ``failed`` or ``refunded``.
* Webhook/event authentication belongs to the concrete adapter. Successfully
  verified notifications are normalized to :class:`PaymentEvent`.
* Raw provider payloads, signatures, cards, bank details, customer payment
  credentials, and provider SDK objects are never retained in stable results.
* Retry behavior is not implemented here; application/worker orchestration owns
  idempotent retry policy.

The repository currently has no versioned payment contract. Consequently,
``PaymentCheckout`` and ``PaymentEvent`` are application-port value objects,
not invented external contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import Order
from ...domain.products.models import ProductTier
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Payment Port")
printer = PrettyPrinter()

_COMPONENT = "payment"


def _require_amount(value: object, *, field: str, operation: str) -> Decimal:
    """
    Require a canonical Decimal amount at the payment boundary.

    ProductTier already owns configuration-time price normalization. Concrete
    adapters therefore convert provider minor units/strings into Decimal before
    returning BIMAP result objects.
    """
    if not isinstance(value, Decimal):
        raise PaymentValidationError(
            "Payment amount must be a Decimal.",
            component=_COMPONENT,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    if not value.is_finite():
        raise PaymentValidationError(
            "Payment amount must be finite.",
            component=_COMPONENT,
            operation=operation,
            field=field,
        )
    if value < 0:
        raise PaymentValidationError(
            "Payment amount must be non-negative.",
            component=_COMPONENT,
            operation=operation,
            field=field,
        )
    return value


def _require_currency(value: object, *, field: str, operation: str) -> str:
    """Normalize the three-letter currency shape already used by ProductTier."""
    normalized = require_app_text(
        value,
        field=field,
        error_type=PaymentValidationError,
        component=_COMPONENT,
        operation=operation,
        max_length=3,
    ).upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise PaymentValidationError(
            "Payment currency must be a three-letter alphabetic code.",
            component=_COMPONENT,
            operation=operation,
            field=field,
        )
    return normalized


class PaymentStatus(str, Enum):
    """
    Provider-neutral payment outcome.

    These values are deliberately not ``OrderState`` values. The application
    service maps a verified provider outcome through the domain transition
    authority rather than letting the provider control lifecycle state.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"

    @classmethod
    def parse(cls, value: "PaymentStatus | str") -> "PaymentStatus":
        """Normalize a supported status without provider-specific aliases."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Parsing payment status",
            event="payment_status_parse_start",
        )
        if isinstance(value, cls):
            return value

        normalized = require_app_text(
            value,
            field="status",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="parse_status",
        ).casefold()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise PaymentValidationError(
                "Unsupported normalized payment status.",
                component=_COMPONENT,
                operation="parse_status",
                field="status",
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc


@dataclass(frozen=True, slots=True)
class PaymentCheckout:
    """
    Stable application result for one provider checkout.

    ``customer_action_url`` is optional because not every integration requires a
    browser redirect. It must never be placed in diagnostic context because such
    URLs may contain sensitive provider state.
    """

    order_id: str
    checkout_id: str
    provider_name: str
    amount: Decimal
    currency: str
    customer_action_url: str | None = None
    expires_at: datetime | str | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating payment checkout result",
            event="payment_checkout_validate_start",
            context={"order_id": self.order_id},
        )
        order_id = require_app_text(
            self.order_id,
            field="order_id",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_checkout",
        )
        checkout_id = require_app_text(
            self.checkout_id,
            field="checkout_id",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_checkout",
            max_length=512,
        )
        provider_name = require_app_text(
            self.provider_name,
            field="provider_name",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_checkout",
            max_length=256,
        )
        amount = _require_amount(
            self.amount,
            field="amount",
            operation="validate_checkout",
        )
        currency = _require_currency(
            self.currency,
            field="currency",
            operation="validate_checkout",
        )
        customer_action_url = optional_app_text(
            self.customer_action_url,
            field="customer_action_url",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_checkout",
            max_length=4096,
        )
        expires_at = (
            None
            if self.expires_at is None
            else ensure_app_utc_datetime(
                self.expires_at,
                field="expires_at",
                error_type=PaymentValidationError,
                component=_COMPONENT,
                operation="validate_checkout",
            )
        )

        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "checkout_id", checkout_id)
        object.__setattr__(self, "provider_name", provider_name)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "customer_action_url", customer_action_url)
        object.__setattr__(self, "expires_at", expires_at)

        logger.debug(
            {
                "event": "payment_checkout_validated",
                "order_id": order_id,
                "provider_name": provider_name,
                "currency": currency,
                "has_customer_action": customer_action_url is not None,
                "has_expiry": expires_at is not None,
            }
        )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic application-facing checkout metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing payment checkout result",
            event="payment_checkout_to_dict_start",
            context={"order_id": self.order_id},
        )
        expires_at = self.expires_at
        if expires_at is not None:
            assert isinstance(expires_at, datetime)

        return {
            "order_id": self.order_id,
            "checkout_id": self.checkout_id,
            "provider_name": self.provider_name,
            "amount": format(self.amount, "f"),
            "currency": self.currency,
            "customer_action_url": self.customer_action_url,
            "expires_at": (
                format_app_utc_datetime(
                    expires_at,
                    field="expires_at",
                    error_type=PaymentValidationError,
                    component=_COMPONENT,
                    operation="to_dict",
                )
                if expires_at is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """
    Authenticated, provider-neutral payment notification.

    ``event_id`` is the provider event's idempotency identity and
    ``payment_reference`` identifies its transaction. No raw webhook body or
    signature is retained.
    """

    event_id: str
    order_id: str
    payment_reference: str
    status: PaymentStatus | str
    amount: Decimal | None
    currency: str | None
    occurred_at: datetime | str
    provider_name: str

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating verified payment event",
            event="payment_event_validate_start",
            context={"order_id": self.order_id},
        )
        event_id = require_app_text(
            self.event_id,
            field="event_id",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_event",
            max_length=512,
        )
        order_id = require_app_text(
            self.order_id,
            field="order_id",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_event",
        )
        payment_reference = require_app_text(
            self.payment_reference,
            field="payment_reference",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_event",
            max_length=512,
        )
        status = PaymentStatus.parse(self.status)
        if (self.amount is None) != (self.currency is None):
            raise PaymentValidationError(
                "Payment event amount and currency must either both be present or both be absent.",
                component=_COMPONENT,
                operation="validate_event",
                field="amount",
                context={"status": status.value},
            )

        amount = (
            None
            if self.amount is None
            else _require_amount(self.amount, field="amount", operation="validate_event")
        )
        currency = (
            None
            if self.currency is None
            else _require_currency(
                self.currency,
                field="currency",
                operation="validate_event",
            )
        )

        if status in {PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED} and (
            amount is None or currency is None
        ):
            raise PaymentValidationError(
                "Successful/refunded payment events require amount and currency.",
                component=_COMPONENT,
                operation="validate_event",
                field="amount",
                context={"status": status.value},
            )
        occurred_at = ensure_app_utc_datetime(
            self.occurred_at,
            field="occurred_at",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_event",
        )
        provider_name = require_app_text(
            self.provider_name,
            field="provider_name",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="validate_event",
            max_length=256,
        )

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "payment_reference", payment_reference)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "provider_name", provider_name)

        logger.debug(
            {
                "event": "payment_event_validated",
                "event_id": event_id,
                "order_id": order_id,
                "status": status.value,
                "provider_name": provider_name,
                "currency": currency,
            }
        )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic verified-event metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing verified payment event",
            event="payment_event_to_dict_start",
            context={"event_id": self.event_id, "order_id": self.order_id},
        )
        status = self.status
        occurred_at = self.occurred_at
        assert isinstance(status, PaymentStatus)
        assert isinstance(occurred_at, datetime)

        return {
            "event_id": self.event_id,
            "order_id": self.order_id,
            "payment_reference": self.payment_reference,
            "status": status.value,
            "amount": format(self.amount, "f") if self.amount is not None else None,
            "currency": self.currency,
            "occurred_at": format_app_utc_datetime(
                occurred_at,
                field="occurred_at",
                error_type=PaymentValidationError,
                component=_COMPONENT,
                operation="to_dict",
            ),
            "provider_name": self.provider_name,
        }


class Payment(ABC):
    """Abstract BIMAP payment dependency."""

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing payment port",
            event="payment_init_start",
        )
        logger.debug(
            {
                "event": "payment_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _create_checkout(
        self,
        order: Order,
        tier: ProductTier,
        *,
        idempotency_key: str,
    ) -> PaymentCheckout:
        """Create one provider checkout for an already-selected priced tier."""
        raise NotImplementedError

    @abstractmethod
    def _verify_event(
        self,
        payload: bytes,
        *,
        signature: str,
    ) -> PaymentEvent:
        """Authenticate and normalize one provider payment notification."""
        raise NotImplementedError

    def create_checkout(
        self,
        order: Order,
        tier: ProductTier,
        *,
        idempotency_key: str,
    ) -> PaymentCheckout:
        """Create a checkout without performing an Order lifecycle transition."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating payment checkout",
            event="payment_create_checkout_start",
            context={
                "order_id": getattr(order, "order_id", None),
                "tier_code": getattr(tier, "tier_code", None),
            },
        )
        if not isinstance(order, Order):
            raise PaymentValidationError(
                "create_checkout() requires a canonical Order.",
                component=_COMPONENT,
                operation="create_checkout",
                field="order",
                context={"received_type": type(order).__name__},
            )
        if not isinstance(tier, ProductTier):
            raise PaymentValidationError(
                "create_checkout() requires a ProductTier.",
                component=_COMPONENT,
                operation="create_checkout",
                field="tier",
                context={"received_type": type(tier).__name__},
            )
        if not tier.is_priced or tier.unit_price is None or tier.currency is None:
            raise PaymentValidationError(
                "The selected product tier is not priced.",
                component=_COMPONENT,
                operation="create_checkout",
                field="tier",
                context={"tier_code": tier.tier_code},
            )
        if order.product_code != tier.product_code.value:
            raise PaymentValidationError(
                "Order product does not match the selected payment tier.",
                component=_COMPONENT,
                operation="create_checkout",
                field="tier.product_code",
                context={
                    "order_product_code": order.product_code,
                    "tier_product_code": tier.product_code.value,
                },
            )
        if order.tier_code != tier.tier_code:
            raise PaymentValidationError(
                "Order tier does not match the selected payment tier.",
                component=_COMPONENT,
                operation="create_checkout",
                field="tier.tier_code",
                context={
                    "order_tier_code": order.tier_code,
                    "tier_code": tier.tier_code,
                },
            )

        key = require_app_text(
            idempotency_key,
            field="idempotency_key",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="create_checkout",
            max_length=512,
        )

        try:
            result = self._create_checkout(order, tier, idempotency_key=key)
        except PaymentError:
            raise
        except TimeoutError as exc:
            raise PaymentTimeoutError(
                "Payment provider timed out while creating checkout.",
                component=_COMPONENT,
                operation="create_checkout",
                context={"order_id": order.order_id},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise PaymentUnavailableError(
                "Payment provider is unavailable while creating checkout.",
                component=_COMPONENT,
                operation="create_checkout",
                context={"order_id": order.order_id},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise PaymentOperationError(
                "Payment adapter failed while creating checkout.",
                component=_COMPONENT,
                operation="create_checkout",
                context={
                    "order_id": order.order_id,
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(result, PaymentCheckout):
            raise PaymentValidationError(
                "Payment adapter returned an unsupported checkout result.",
                component=_COMPONENT,
                operation="create_checkout",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.order_id != order.order_id:
            raise PaymentValidationError(
                "Checkout result belongs to a different order.",
                component=_COMPONENT,
                operation="create_checkout",
                field="result.order_id",
                context={
                    "requested_order_id": order.order_id,
                    "returned_order_id": result.order_id,
                },
            )
        if result.amount != tier.unit_price or result.currency != tier.currency:
            raise PaymentVerificationError(
                "Checkout amount/currency contradicts the selected tier.",
                component=_COMPONENT,
                operation="create_checkout",
                field="result.amount",
                context={
                    "order_id": order.order_id,
                    "expected_currency": tier.currency,
                    "returned_currency": result.currency,
                },
            )

        logger.info(
            {
                "event": "payment_checkout_created",
                "order_id": order.order_id,
                "provider_name": result.provider_name,
                "currency": result.currency,
            }
        )
        return result

    def verify_event(
        self,
        payload: bytes | bytearray | memoryview,
        *,
        signature: str,
    ) -> PaymentEvent:
        """
        Authenticate and normalize one payment-provider notification.

        Signature interpretation is adapter-specific. Neither raw payload nor
        signature is added to diagnostics.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Verifying payment provider event",
            event="payment_verify_event_start",
        )
        raw = bytes(
            require_bytes_like(
                payload,
                field="payload",
                error_type=PaymentValidationError,
                component=_COMPONENT,
                operation="verify_event",
            )
        )
        normalized_signature = require_app_text(
            signature,
            field="signature",
            error_type=PaymentValidationError,
            component=_COMPONENT,
            operation="verify_event",
            max_length=4096,
        )

        try:
            result = self._verify_event(raw, signature=normalized_signature)
        except PaymentError:
            raise
        except TimeoutError as exc:
            raise PaymentTimeoutError(
                "Payment provider timed out while verifying an event.",
                component=_COMPONENT,
                operation="verify_event",
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise PaymentUnavailableError(
                "Payment provider is unavailable while verifying an event.",
                component=_COMPONENT,
                operation="verify_event",
                cause=exc,
            ) from exc
        except Exception as exc:
            raise PaymentOperationError(
                "Payment adapter failed while verifying an event.",
                component=_COMPONENT,
                operation="verify_event",
                context={
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(result, PaymentEvent):
            raise PaymentValidationError(
                "Payment adapter returned an unsupported verified-event result.",
                component=_COMPONENT,
                operation="verify_event",
                field="result",
                context={"received_type": type(result).__name__},
            )

        logger.info(
            {
                "event": "payment_event_verified",
                "event_id": result.event_id,
                "order_id": result.order_id,
                "status": PaymentStatus.parse(result.status).value,
                "provider_name": result.provider_name,
            }
        )
        return result

    def validate_event_for_order(
        self,
        event: PaymentEvent,
        order: Order,
        tier: ProductTier,
    ) -> PaymentEvent:
        """
        Bind a verified event to authoritative order/tier identity and price.

        This method performs no lifecycle mutation.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating payment event against order",
            event="payment_validate_event_for_order_start",
            context={
                "event_id": getattr(event, "event_id", None),
                "order_id": getattr(order, "order_id", None),
            },
        )
        if not isinstance(event, PaymentEvent):
            raise PaymentValidationError(
                "event must be a PaymentEvent.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="event",
                context={"received_type": type(event).__name__},
            )
        if not isinstance(order, Order):
            raise PaymentValidationError(
                "order must be a canonical Order.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="order",
                context={"received_type": type(order).__name__},
            )
        if not isinstance(tier, ProductTier):
            raise PaymentValidationError(
                "tier must be a ProductTier.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="tier",
                context={"received_type": type(tier).__name__},
            )
        if tier.unit_price is None or tier.currency is None:
            raise PaymentValidationError(
                "The selected tier has no configured payment amount.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="tier",
                context={"tier_code": tier.tier_code},
            )
        if event.order_id != order.order_id:
            raise PaymentVerificationError(
                "Verified payment event belongs to a different order.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="event.order_id",
                context={
                    "expected_order_id": order.order_id,
                    "received_order_id": event.order_id,
                },
            )
        if order.product_code != tier.product_code.value or order.tier_code != tier.tier_code:
            raise PaymentValidationError(
                "Authoritative order and tier selection are inconsistent.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="tier",
                context={
                    "order_id": order.order_id,
                    "order_product_code": order.product_code,
                    "tier_product_code": tier.product_code.value,
                    "order_tier_code": order.tier_code,
                    "tier_code": tier.tier_code,
                },
            )
        if event.amount is not None or event.currency is not None:
            if event.amount != tier.unit_price or event.currency != tier.currency:
                raise PaymentVerificationError(
                    "Verified payment event amount/currency does not match the order tier.",
                    component=_COMPONENT,
                    operation="validate_event_for_order",
                    field="event.amount",
                    context={
                        "order_id": order.order_id,
                        "status": PaymentStatus.parse(event.status).value,
                        "expected_currency": tier.currency,
                        "received_currency": event.currency,
                    },
                )
        elif event.status in {PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED}:
            # Defensive guard; PaymentEvent validation already requires these values.
            raise PaymentVerificationError(
                "Material payment event is missing amount/currency.",
                component=_COMPONENT,
                operation="validate_event_for_order",
                field="event.amount",
                context={
                    "order_id": order.order_id,
                    "status": PaymentStatus.parse(event.status).value,
                },
            )
        return event


__all__ = [
    "PaymentStatus",
    "PaymentCheckout",
    "PaymentEvent",
    "Payment",
]