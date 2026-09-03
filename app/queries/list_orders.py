"""
Bounded read-only application query for a known set of BIMAP orders.

The current Level-4 ``Repository`` port intentionally defines point reads and
writes only; it does not define an unbounded ``list_orders`` persistence API,
customer ownership filters, database ordering, cursor semantics, or pagination.
This Level-5 query therefore does not invent those missing persistence semantics.
Instead, the caller supplies the ordered set of already-authorized order
identifiers to resolve.

This design keeps the current dependency hierarchy valid and makes missing
records explicit without silently dropping them.  If BIMAP later requires
server-side search/pagination over large datasets, that capability should be
introduced as an explicit read-model/query port rather than hidden behind this
module or guessed on the existing repository contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ...contracts.order import OrderContract
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import Repository
from .get_order import GetOrder
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP List Orders Query")
printer = PrettyPrinter()

_COMPONENT = "list_orders_query"


def _normalize_order_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize and stable-deduplicate caller-authorized order identifiers."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing order identifiers",
        event="list_orders_query_ids_normalize_start",
    )
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedAppInputError(
            "order_ids must be an iterable of order identifier strings.",
            component=_COMPONENT,
            operation="normalize_order_ids",
            field="order_ids",
            context={"received_type": type(values).__name__},
        )

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise UnsupportedAppInputError(
            "order_ids must be iterable.",
            component=_COMPONENT,
            operation="normalize_order_ids",
            field="order_ids",
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(iterator):
        order_id = require_app_text(
            value,
            field=f"order_ids[{index}]",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="normalize_order_ids",
        )
        if order_id not in seen:
            seen.add(order_id)
            result.append(order_id)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class OrderListResult:
    """Deterministic result of resolving a caller-supplied order-id set."""

    items: tuple[OrderContract, ...]
    missing_order_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating order-list result",
            event="list_orders_query_result_validate_start",
        )
        if not isinstance(self.items, tuple):
            raise AppValidationError(
                "items must be a tuple of OrderContract values.",
                component=_COMPONENT,
                operation="validate_result",
                field="items",
                context={"received_type": type(self.items).__name__},
            )
        for index, item in enumerate(self.items):
            if not isinstance(item, OrderContract):
                raise AppValidationError(
                    "items contains a non-OrderContract value.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=f"items[{index}]",
                    context={"received_type": type(item).__name__},
                )

        item_ids = tuple(item.order_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise AppValidationError(
                "items contains duplicate order identifiers.",
                component=_COMPONENT,
                operation="validate_result",
                field="items",
            )

        missing = _normalize_order_ids(self.missing_order_ids)
        found_ids = {item.order_id for item in self.items}
        overlap = tuple(order_id for order_id in missing if order_id in found_ids)
        if overlap:
            raise AppValidationError(
                "An order identifier cannot be both found and missing.",
                component=_COMPONENT,
                operation="validate_result",
                field="missing_order_ids",
                context={"overlap": overlap},
            )
        object.__setattr__(self, "missing_order_ids", missing)

    @property
    def found_count(self) -> int:
        return len(self.items)

    @property
    def missing_count(self) -> int:
        return len(self.missing_order_ids)

    @property
    def requested_count(self) -> int:
        return self.found_count + self.missing_count

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-ready list-query data."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing order-list result",
            event="list_orders_query_result_to_dict_start",
        )
        return {
            "items": [item.to_dict() for item in self.items],
            "missing_order_ids": list(self.missing_order_ids),
            "found_count": self.found_count,
            "missing_count": self.missing_count,
            "requested_count": self.requested_count,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Encode the result using BIMAP's canonical application JSON rules."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Encoding order-list result JSON",
            event="list_orders_query_result_to_json_start",
        )
        return canonical_app_json(self.to_dict(), pretty=pretty)


class ListOrders:
    """Resolve a stable ordered set of order identifiers through ``Repository``."""

    def __init__(self, repository: Repository) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing list-orders query",
            event="list_orders_query_init_start",
        )
        if not isinstance(repository, Repository):
            raise AppConfigurationError(
                "repository must implement the BIMAP Repository port.",
                component=_COMPONENT,
                operation="initialize",
                field="repository",
                context={"received_type": type(repository).__name__},
            )
        self.repository = repository
        self._get_order = GetOrder(repository)
        logger.debug(
            {
                "event": "list_orders_query_initialized",
                "repository_implementation": type(repository).__name__,
            }
        )

    def execute(self, order_ids: Iterable[str]) -> OrderListResult:
        """Resolve unique identifiers in first-seen order and report absences."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing list-orders query",
            event="list_orders_query_execute_start",
        )
        targets = _normalize_order_ids(order_ids)
        items: list[OrderContract] = []
        missing: list[str] = []

        for order_id in targets:
            contract = self._get_order.find(order_id)
            if contract is None:
                missing.append(order_id)
            else:
                items.append(contract)

        result = OrderListResult(
            items=tuple(items),
            missing_order_ids=tuple(missing),
        )
        logger.info(
            {
                "event": "list_orders_query_completed",
                "requested_count": result.requested_count,
                "found_count": result.found_count,
                "missing_count": result.missing_count,
            }
        )
        return result


# Backward-compatible name retained from the original scaffold without creating
# a second implementation.
ListOrder = ListOrders


__all__ = ["OrderListResult", "ListOrders", "ListOrder"]