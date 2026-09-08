"""
HTTP admission boundary for BIMAP account-backed audit entitlement.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.grant_entitlement import GrantEntitlement
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Entitlements")
printer = PrettyPrinter()

_COMPONENT = "api_route_entitlements"


class RouteEntitlements:
    """Account-backed audit entitlement route group."""

    __slots__ = ("router", "_grant_entitlement", "_authorize")

    def __init__(
        self,
        grant_entitlement: GrantEntitlement,
        *,
        authorizer: RouteAuthorizer,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action=("Initializing entitlement API routes"),
            event=("api_route_entitlements_init_start"),
        )

        if not isinstance(
            grant_entitlement,
            GrantEntitlement,
        ):
            raise APIConfigurationError(
                "grant_entitlement must be a "
                "GrantEntitlement command.",
                component=_COMPONENT,
                operation="initialize",
                field="grant_entitlement",
                context={"received_type": type(grant_entitlement).__name__},
            )

        self._grant_entitlement = (grant_entitlement)
        self._authorize = (require_route_authorizer(authorizer))

        router = APIRouter(prefix="/orders", tags=["entitlements"])
        router.add_api_route(
            "/{order_id}/entitle",
            self.grant,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="grant_order_entitlement",
        )

        self.router = router

    async def grant(self, request: Request, order_id: str) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action=("Handling grant-entitlement request"),
            event=("api_route_entitlements_grant_start"),
            context={"order_id": order_id},
        )

        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="grant_entitlement",
        )

        actor = await authorize_request(
            self._authorize,
            request,
            operation="grant_entitlement",
            resource_id=target,
        )

        if actor is None:
            raise APIUnauthorizedError(
                "Authenticated account identity "
                "is required.",
                component=_COMPONENT,
                operation="grant_entitlement",
            )

        idempotency_key = (require_idempotency_key(request))

        order = (
            self._grant_entitlement
            .execute(target, idempotency_key=(idempotency_key), actor=actor))

        return json_response(
            order_to_public_dict(order),
            headers={"Cache-Control": "no-store"},
        )


__all__ = [
    "RouteEntitlements",
]