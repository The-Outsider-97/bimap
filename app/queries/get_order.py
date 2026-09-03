"""
Read-only application query for one BIMAP order.

``GetOrder`` is a Level-5 query handler.  It depends only on the Level-4
``Repository`` port and the stable ``OrderContract`` boundary; it never mutates
an order, opens configuration files, accesses a concrete database, or embeds API
transport semantics.

The repository owns persistence and returns the canonical domain ``Order``.
This query projects that aggregate into the versioned external
``OrderContract`` so API/SDK callers do not need to depend on mutable
application internals.  ``find()`` preserves the repository convention that an
absent record is a normal ``None`` result, while ``execute()`` provides the
required-record form and raises a BIMAP application validation error when the
order does not exist.
"""

from __future__ import annotations

from ..ports.repositories import Repository
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.order import OrderContract
from ...contracts.utils.contracts_errors import ContractError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Get Order Query")
printer = PrettyPrinter()

_COMPONENT = "get_order_query"


class GetOrder:
    """Load one authoritative order and expose it as an ``OrderContract``."""

    def __init__(self, repository: Repository) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing get-order query",
            event="get_order_query_init_start",
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
        logger.debug(
            {
                "event": "get_order_query_initialized",
                "repository_implementation": type(repository).__name__,
            }
        )

    def find(self, order_id: str) -> OrderContract | None:
        """Return one order contract, or ``None`` when the order is absent."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding order",
            event="get_order_query_find_start",
            context={"order_id": order_id},
        )
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="find",
        )

        order = self.repository.get_order(target)
        if order is None:
            logger.debug(
                {
                    "event": "get_order_query_not_found",
                    "order_id": target,
                }
            )
            return None

        try:
            contract = OrderContract.from_domain(order)
        except ContractError as exc:
            raise AppIntegrityError(
                "Authoritative order could not be projected to the external order contract.",
                component=_COMPONENT,
                operation="find",
                field="order",
                context={"order_id": target, **lower_error_context(exc)},
                cause=exc,
            ) from exc

        if contract.order_id != target:
            raise AppIntegrityError(
                "Projected order identity does not match the requested order.",
                component=_COMPONENT,
                operation="find",
                field="result.order_id",
                context={
                    "requested_order_id": target,
                    "returned_order_id": contract.order_id,
                },
            )

        logger.info(
            {
                "event": "get_order_query_completed",
                "order_id": contract.order_id,
                "state": contract.state.value,
                "version": contract.version,
            }
        )
        return contract

    def execute(self, order_id: str) -> OrderContract:
        """Return one order contract and fail explicitly when it does not exist."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing get-order query",
            event="get_order_query_execute_start",
            context={"order_id": order_id},
        )
        result = self.find(order_id)
        if result is None:
            raise AppValidationError(
                "Order does not exist.",
                component=_COMPONENT,
                operation="execute",
                field="order_id",
                context={"order_id": order_id},
            )
        return result


__all__ = ["GetOrder"]