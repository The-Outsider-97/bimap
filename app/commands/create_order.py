"""
Create-order application command for BIMAP.

This module is intentionally thin.  ``OrderService`` already owns the
application-level orchestration required to create a catalog-backed draft
``Order``: product/tier lookup, clock usage, domain aggregate construction,
duplicate-order detection, repository persistence, and write-back integrity.

The command therefore does not reimplement product validation, generate prices,
choose retention policy, manipulate order state directly, or access concrete
persistence/payment adapters.  Its responsibilities are limited to:

* expose one explicit command entry point for API/worker composition;
* require the configured ``OrderService`` dependency;
* preserve the service's application-error vocabulary;
* fail closed if the service violates its declared result contract; and
* emit content-safe method-start diagnostics through BIMAP's shared helper.

Dependency direction
--------------------
app/commands/create_order.py
    -> app/services/order_service.py
    -> app/ports + domain

No lower layer imports this command.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.order_service import OrderService
from ...domain.orders.models import Order
from ...domain.products.models import ProductCode
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Create Order Command")
printer = PrettyPrinter()

_COMPONENT = "create_order_command"


class CreateOrder:
    """
    Execute the BIMAP create-order use case through ``OrderService``.

    The command accepts the same authoritative inputs as
    :meth:`OrderService.create_order`.  Validation of product membership, tier
    membership, metadata serializability, and canonical order construction
    remains owned by the service/domain layers rather than being duplicated
    here.
    """

    __slots__ = ("_service",)

    def __init__(self, service: OrderService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing create-order command",
            event="create_order_command_init_start",
        )

        if not isinstance(service, OrderService):
            raise AppConfigurationError(
                "service must be an OrderService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )

        self._service = service
        logger.debug(
            {
                "event": "create_order_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        *,
        product_code: ProductCode | str,
        tier_code: str | None = None,
        order_id: str | None = None,
        project_alias: str | None = None,
        upload_session_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Order:
        """
        Create and persist one authoritative draft order.

        ``OrderService`` remains the source of truth for all business and
        persistence rules.  This boundary deliberately does not inspect or log
        project aliases, upload-session identifiers, or metadata content.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing create-order command",
            event="create_order_command_execute_start",
            context={
                "product_code_type": type(product_code).__name__,
                "has_tier": tier_code is not None,
                "has_requested_order_id": order_id is not None,
            },
        )

        try:
            result = self._service.create_order(
                product_code=product_code,
                tier_code=tier_code,
                order_id=order_id,
                project_alias=project_alias,
                upload_session_id=upload_session_id,
                metadata=metadata,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "OrderService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if not isinstance(result, Order):
            raise AppIntegrityError(
                "Create-order service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        logger.info(
            {
                "event": "create_order_command_completed",
                "order_id": result.order_id,
                "product_code": result.product_code,
                "tier_code": result.tier_code,
                "state": result.state.value,
                "version": result.version,
            }
        )
        return result


__all__ = ["CreateOrder"]