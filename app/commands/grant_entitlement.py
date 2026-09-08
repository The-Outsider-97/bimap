"""
Grant-entitlement application command for BIMAP.

Purpose
-------
Coordinate the transition of one upload-validated audit order onto the
plan-entitlement execution path.

The command owns only the cross-service coordination required to:

1. load the authoritative Order;
2. require that the order is eligible for plan entitlement;
3. consume exactly one AUDIT entitlement for the owning account;
4. bind entitlement consumption to the audit order identity;
5. transition the order from UPLOAD_VALIDATED to ENTITLED; and
6. return the authoritative resulting Order.

Commercial entitlement policy itself is deliberately not implemented here.

The entitlement service remains responsible for:

- resolving the account's active plan;
- resolving the applicable recurring quota window;
- distinguishing finite from unlimited plans;
- checking recurring quota availability;
- applying recurring quota before redeemed bonus credits;
- atomically recording usage;
- enforcing source/idempotency uniqueness;
- detecting quota exhaustion; and
- returning the authoritative entitlement source.

OrderService remains responsible for:

- loading/persisting Order aggregates;
- lifecycle legality;
- transition event idempotency;
- optimistic concurrency; and
- canonical lifecycle metadata normalization.

Idempotency
-----------
Entitlement consumption is bound to ``Order.order_id`` through ``source_id``.
The entitlement persistence boundary MUST enforce that one audit order cannot
consume more than one audit entitlement, even when a caller retries using a
different request idempotency key.

The caller-supplied idempotency key is used for entitlement consumption.
A deterministic ``:order`` suffix is used for the Order lifecycle transition
so the two independently idempotent operations do not share a semantic key.

If entitlement consumption succeeds but the order transition fails, this
command deliberately does not invent a compensating quota operation. A retry
with the same audit order must resolve the existing entitlement consumption
rather than consume another unit.

This coordination does not claim distributed transactional atomicity between
an entitlement store and the Order repository. Production persistence should
therefore either:

- place both writes inside one deployment-owned transactional boundary; or
- guarantee source-id idempotency and provide reconciliation for interrupted
  cross-store coordination.

Lifecycle
---------
Supported path::

    UPLOAD_VALIDATED
        |
        | consume AUDIT entitlement
        v
    ENTITLED

``ENTITLED`` is also accepted as an idempotent completed state. No further
entitlement consumption occurs once the authoritative order is already
ENTITLED.

This command must not be used for the paid audit path::

    UPLOAD_VALIDATED
        -> PAYMENT_PENDING
        -> PAID
        -> QUEUED

Dependency direction
--------------------
app/commands/grant_entitlement.py
    -> app/services contract
    -> app/services/order_service.py
    -> domain/accounts/plans.py
    -> domain/orders

No domain, service, port, API, worker, or infrastructure module imports this
command.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from ..services.order_service import OrderService
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.accounts.plans import UsageKind
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Grant Entitlement Command")
printer = PrettyPrinter()

_COMPONENT = "grant_entitlement_command"

_ORDER_TRANSITION_SUFFIX = ":order"

# OrderService currently permits idempotency keys up to 512 characters.
# Reserve enough space for the deterministic transition suffix rather than
# allowing this command to create a derived key that OrderService rejects.
_MAX_ORDER_IDEMPOTENCY_KEY_LENGTH = 512
_MAX_BASE_IDEMPOTENCY_KEY_LENGTH = (
    _MAX_ORDER_IDEMPOTENCY_KEY_LENGTH
    - len(_ORDER_TRANSITION_SUFFIX)
)


class _EntitlementConsumption(Protocol):
    """
    Minimal result contract required from entitlement consumption.

    The command intentionally depends only on ``source`` because ownership,
    quota-window calculation, remaining balances, and usage-ledger details are
    concerns of the entitlement service/query layers rather than the order
    lifecycle transition.
    """

    source: Any


class _EntitlementConsumer(Protocol):
    """Minimal application-service contract consumed by this command."""

    def consume(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        source_id: str,
        idempotency_key: str,
    ) -> _EntitlementConsumption:
        """Consume or idempotently resolve one usage entitlement."""
        ...


def _require_entitlement_consumer(
    value: Any,
) -> _EntitlementConsumer:
    """
    Validate the narrow entitlement-service dependency.

    ``EntitlementService`` is not yet implemented in the current repository.
    Structural validation keeps this command independently importable without
    weakening its runtime dependency contract or inventing that service here.
    """
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating entitlement consumer dependency",
        event="grant_entitlement_consumer_validate_start",
    )

    consume = getattr(value, "consume", None)

    if not callable(consume):
        raise AppConfigurationError(
            "entitlement_service must provide a callable consume() method.",
            component=_COMPONENT,
            operation="initialize",
            field="entitlement_service",
            context={
                "received_type": type(value).__name__,
            },
        )

    return value


def _build_order_transition_key(
    idempotency_key: str,
) -> str:
    """
    Validate the caller key and derive the order-transition idempotency key.

    Reserving suffix capacity here prevents an otherwise valid 512-character
    caller key from producing a transition key longer than OrderService's
    existing 512-character boundary.
    """
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Deriving entitlement order idempotency key",
        event="grant_entitlement_idempotency_key_build_start",
    )

    base_key = require_app_text(
        idempotency_key,
        field="idempotency_key",
        error_type=AppValidationError,
        component=_COMPONENT,
        operation="execute",
        max_length=_MAX_BASE_IDEMPOTENCY_KEY_LENGTH,
    )

    transition_key = (
        f"{base_key}{_ORDER_TRANSITION_SUFFIX}"
    )

    if (
        len(transition_key)
        > _MAX_ORDER_IDEMPOTENCY_KEY_LENGTH
    ):
        # Defensive invariant. The preceding validation should make this
        # unreachable unless the constants become internally inconsistent.
        raise AppIntegrityError(
            "Derived order-transition idempotency key exceeds "
            "the supported application boundary.",
            component=_COMPONENT,
            operation="execute",
            field="idempotency_key",
            context={
                "maximum_length":
                    _MAX_ORDER_IDEMPOTENCY_KEY_LENGTH,
                "derived_length":
                    len(transition_key),
            },
        )

    return transition_key


def _normalize_entitlement_source(
    consumption: Any,
) -> str:
    """
    Extract a deterministic lifecycle-safe entitlement source.

    The entitlement service owns the source vocabulary. This command therefore
    validates only that the returned value can be represented as non-empty text;
    it deliberately does not duplicate source enumeration or quota policy.
    """
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating entitlement consumption result",
        event="grant_entitlement_consumption_validate_start",
    )

    if consumption is None:
        raise AppIntegrityError(
            "Entitlement service returned no consumption result.",
            component=_COMPONENT,
            operation="execute",
            field="entitlement_result",
        )

    if not hasattr(consumption, "source"):
        raise AppIntegrityError(
            "Entitlement service result does not expose its "
            "authoritative entitlement source.",
            component=_COMPONENT,
            operation="execute",
            field="entitlement_result.source",
            context={
                "received_type": type(consumption).__name__,
            },
        )

    source: Any = getattr(
        consumption,
        "source",
    )

    # Permit the eventual service to use a strongly typed Enum without leaking
    # an Enum object into Order event metadata.
    if isinstance(source, Enum):
        source = source.value

    primitive = to_app_primitive(
        source,
        field="entitlement_source",
    )

    if not isinstance(primitive, str):
        raise AppIntegrityError(
            "Entitlement source must normalize to text.",
            component=_COMPONENT,
            operation="execute",
            field="entitlement_result.source",
            context={
                "received_type": type(source).__name__,
                "normalized_type":
                    type(primitive).__name__,
            },
        )

    return require_app_text(
        primitive,
        field="entitlement_result.source",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="execute",
        max_length=128,
    )


class GrantEntitlement:
    """
    Grant one account-backed audit entitlement to an eligible BIMAP order.

    This is an application command, not an entitlement-policy implementation.
    It coordinates two existing application responsibilities:

    ``entitlement_service.consume(...)``
        establishes the authoritative commercial usage event;

    ``OrderService.transition(...)``
        establishes the authoritative order lifecycle event.

    The returned value is the canonical ``Order`` rather than a second command-
    specific wrapper. The entitlement source is already bound to the lifecycle
    event metadata, so another result model would duplicate authoritative state.
    """

    __slots__ = (
        "_entitlement_service",
        "_order_service",
    )

    def __init__(
        self,
        entitlement_service: _EntitlementConsumer,
        order_service: OrderService,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing grant-entitlement command",
            event="grant_entitlement_command_init_start",
        )

        self._entitlement_service = (
            _require_entitlement_consumer(
                entitlement_service
            )
        )

        if not isinstance(
            order_service,
            OrderService,
        ):
            raise AppConfigurationError(
                "order_service must be an OrderService.",
                component=_COMPONENT,
                operation="initialize",
                field="order_service",
                context={
                    "received_type":
                        type(order_service).__name__,
                },
            )

        self._order_service = order_service

        logger.debug(
            {
                "event":
                    "grant_entitlement_command_initialized",
                "entitlement_service_type":
                    type(
                        entitlement_service
                    ).__name__,
                "order_service_type":
                    type(order_service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        actor: str | None = None,
    ) -> Order:
        """
        Consume one audit entitlement and establish ``ENTITLED`` order state.

        Parameters
        ----------
        order_id:
            Stable identifier of the authoritative audit order.

        idempotency_key:
            Caller-supplied operation key. It is used unchanged for entitlement
            consumption and deterministically suffixed for the Order transition.

            Persistent entitlement storage must additionally bind usage to
            ``order_id`` so a different retry key cannot charge the same audit
            twice.

        actor:
            Optional authenticated/system actor forwarded to OrderService for
            lifecycle provenance. Actor validation remains owned by
            OrderService.

        Returns
        -------
        Order
            The authoritative order in ``ENTITLED`` state.

        Raises
        ------
        AppValidationError
            When the order is not currently eligible for plan entitlement.

        AppIntegrityError
            When a dependency violates its application contract or returned
            entitlement/order identity is inconsistent.

        AppError
            Existing application/port failures are preserved without being
            flattened into generic exceptions.

        Notes
        -----
        An already-ENTITLED order is treated as an idempotent completed result.
        The entitlement service is not called again in that case. This prevents
        a second commercial mutation even when a caller supplies another request
        idempotency key after a previous successful grant.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing grant-entitlement command",
            event="grant_entitlement_command_execute_start",
            context={
                "order_id": order_id,
            },
        )

        # Validate this before commercial mutation because the derived
        # transition key is required after entitlement consumption. A malformed
        # key must never result in successfully consumed quota followed by a
        # predictable local validation failure.
        transition_idempotency_key = (
            _build_order_transition_key(
                idempotency_key
            )
        )

        # The validated base key is recovered without duplicating validation
        # rules. Since the derived key is ``base + suffix`` and the suffix is
        # constant, this operation is deterministic.
        entitlement_idempotency_key = (
            transition_idempotency_key[
                :-len(_ORDER_TRANSITION_SUFFIX)
            ]
        )

        entitlement_confirmed = False

        try:
            order = self._order_service.get_order(
                order_id
            )

            if not isinstance(order, Order):
                raise AppIntegrityError(
                    "OrderService returned an unsupported order type.",
                    component=_COMPONENT,
                    operation="execute",
                    field="order",
                    context={
                        "received_type":
                            type(order).__name__,
                    },
                )

            # Completed-state idempotency is intentionally evaluated before
            # entitlement consumption. A successfully entitled order must never
            # consume another allowance merely because a client repeats the
            # command with another request idempotency key.
            if order.state is OrderState.ENTITLED:
                logger.info(
                    {
                        "event":
                            "grant_entitlement_already_entitled",
                        "order_id":
                            order.order_id,
                        "account_id":
                            order.account_id,
                        "state":
                            order.state.value,
                    }
                )
                return order

            if (
                order.state
                is not OrderState.UPLOAD_VALIDATED
            ):
                raise AppValidationError(
                    "Order is not eligible for plan entitlement.",
                    component=_COMPONENT,
                    operation="execute",
                    field="order.state",
                    context={
                        "order_id":
                            order.order_id,
                        "state":
                            order.state.value,
                        "required_state":
                            OrderState.UPLOAD_VALIDATED.value,
                    },
                )

            # Commercial mutation occurs only after lifecycle eligibility has
            # been established from the authoritative Order.
            consumption = (
                self._entitlement_service.consume(
                    account_id=order.account_id,
                    kind=UsageKind.AUDIT,
                    source_id=order.order_id,
                    idempotency_key=(
                        entitlement_idempotency_key
                    ),
                )
            )

            entitlement_confirmed = True

            entitlement_source = (
                _normalize_entitlement_source(
                    consumption
                )
            )

            entitled_order = (
                self._order_service.transition(
                    order.order_id,
                    OrderState.ENTITLED,
                    idempotency_key=(
                        transition_idempotency_key
                    ),
                    actor=actor,
                    metadata={
                        "entitlement_source":
                            entitlement_source,
                    },
                )
            )

            if not isinstance(
                entitled_order,
                Order,
            ):
                raise AppIntegrityError(
                    "OrderService returned an unsupported "
                    "transition result type.",
                    component=_COMPONENT,
                    operation="execute",
                    field="result",
                    context={
                        "received_type":
                            type(
                                entitled_order
                            ).__name__,
                    },
                )

            if (
                entitled_order.order_id
                != order.order_id
            ):
                raise AppIntegrityError(
                    "OrderService changed order identity "
                    "during entitlement transition.",
                    component=_COMPONENT,
                    operation="execute",
                    field="result.order_id",
                    context={
                        "expected_order_id":
                            order.order_id,
                        "returned_order_id":
                            entitled_order.order_id,
                    },
                )

            if (
                entitled_order.account_id
                != order.account_id
            ):
                raise AppIntegrityError(
                    "OrderService changed account ownership "
                    "during entitlement transition.",
                    component=_COMPONENT,
                    operation="execute",
                    field="result.account_id",
                    context={
                        "order_id":
                            order.order_id,
                        "expected_account_id":
                            order.account_id,
                        "returned_account_id":
                            entitled_order.account_id,
                    },
                )

            if (
                entitled_order.state
                is not OrderState.ENTITLED
            ):
                raise AppIntegrityError(
                    "OrderService did not establish the "
                    "canonical entitled state.",
                    component=_COMPONENT,
                    operation="execute",
                    field="result.state",
                    context={
                        "order_id":
                            order.order_id,
                        "returned_state":
                            entitled_order.state.value,
                    },
                )

        except AppError:
            if entitlement_confirmed:
                # Do not compensate here. The entitlement operation is required
                # to be source-idempotent, making retry/reconciliation safer
                # than an uncoordinated quota restoration.
                logger.warning(
                    {
                        "event":
                            "grant_entitlement_transition_incomplete",
                        "order_id":
                            order_id,
                        "entitlement_consumption_confirmed":
                            True,
                    }
                )

            raise

        except Exception as exc:
            if entitlement_confirmed:
                logger.warning(
                    {
                        "event":
                            "grant_entitlement_transition_incomplete",
                        "order_id":
                            order_id,
                        "entitlement_consumption_confirmed":
                            True,
                        **lower_error_context(exc),
                    }
                )

            raise AppIntegrityError(
                "Grant-entitlement coordination failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={
                    "order_id":
                        order_id,
                    "entitlement_consumption_confirmed":
                        entitlement_confirmed,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        logger.info(
            {
                "event":
                    "grant_entitlement_command_completed",
                "order_id":
                    entitled_order.order_id,
                "account_id":
                    entitled_order.account_id,
                "usage_kind":
                    UsageKind.AUDIT.value,
                "entitlement_source":
                    entitlement_source,
                "state":
                    entitled_order.state.value,
                "version":
                    entitled_order.version,
            }
        )

        return entitled_order


__all__ = [
    "GrantEntitlement",
]


if __name__ == "__main__":
    print("\n=== Running BIMAP Grant Entitlement Self-Test ===\n")
    printer.status("TEST", "Grant Entitlement module initialized", "info")

    assert (_build_order_transition_key("grant-entitlement-test") == "grant-entitlement-test:order")
    printer.status("PASS", "Order transition idempotency derivation", "success")

    assert (_normalize_entitlement_source(type("_Consumption", (), {"source": "recurring_quota"})()) == "recurring_quota")
    printer.status("PASS", "Entitlement source normalization",  "success")

    try:
        _build_order_transition_key("x" * (_MAX_BASE_IDEMPOTENCY_KEY_LENGTH + 1))
    except AppValidationError:
        printer.status("PASS", "Oversized idempotency key rejected", "success")
    else:
        raise AssertionError(
            "Oversized grant-entitlement "
            "idempotency key was accepted."
        )

    assert callable(
        getattr(GrantEntitlement, "execute", None))

    printer.status("PASS", "Grant Entitlement command surface", "success")
    print("\n=== Grant Entitlement self-test ran successfully ===\n")
