"""
FastAPI route for BIMAP retention-governed deletion.

The published customer surface is:

    POST /orders/{order_id}/delete

The current application model does *not* contain a durable ``DeletionRequest``
aggregate, pending-deletion repository, legal-hold model, or force-delete
operation.  ``RequestDeletion`` synchronously delegates to
``FulfilmentService.expire_delivery_if_due`` and succeeds only when the
configured retention expiry permits deletion.

Accordingly this route does not claim to enqueue or persist a future deletion
request and does not return HTTP 202.  It executes the currently supported
retention-governed operation and returns the authoritative resulting order.

Security boundary
-----------------
The client is never allowed to submit storage ``object_ids``.  Storage object
identity is infrastructure metadata and the current manifest does not provide a
safe order-to-object derivation.  An injected trusted ``DeletionObjectResolver``
supplies the explicit object IDs after authorization.  A separate
``DeletionAdmissionGate`` owns deployment/legal/accounting checks and must raise
an explicit ``APIError`` when deletion is not permitted.
"""

from __future__ import annotations

import inspect

from collections.abc import Awaitable, Callable, Iterable
from typing import TypeAlias
from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.request_deletion import RequestDeletion
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Deletion")
printer = PrettyPrinter()

_COMPONENT = "api_route_deletion"


DeletionAdmissionGate: TypeAlias = Callable[
    [Request, str, str | None],
    None | Awaitable[None],
]

DeletionObjectResolver: TypeAlias = Callable[
    [Request, str, str | None],
    Iterable[str] | Awaitable[Iterable[str]],
]


def _require_deletion_gate(gate: DeletionAdmissionGate) -> DeletionAdmissionGate:
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating deletion admission gate",
        event="api_route_deletion_gate_validate_start",
    )
    if not callable(gate):
        raise APIConfigurationError(
            "deletion_admission_gate must be callable.",
            component=_COMPONENT,
            operation="initialize",
            field="deletion_admission_gate",
            context={"received_type": type(gate).__name__},
        )
    return gate


def _require_object_resolver(
    resolver: DeletionObjectResolver,
) -> DeletionObjectResolver:
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating deletion object resolver",
        event="api_route_deletion_resolver_validate_start",
    )
    if not callable(resolver):
        raise APIConfigurationError(
            "deletion_object_resolver must be callable.",
            component=_COMPONENT,
            operation="initialize",
            field="deletion_object_resolver",
            context={"received_type": type(resolver).__name__},
        )
    return resolver


class RouteDeletion:
    """Dependency-injected retention-governed deletion route group."""

    __slots__ = (
        "router",
        "_request_deletion",
        "_authorize",
        "_admission_gate",
        "_object_resolver",
    )

    def __init__(
        self,
        request_deletion: RequestDeletion,
        *,
        authorizer: RouteAuthorizer,
        deletion_admission_gate: DeletionAdmissionGate,
        deletion_object_resolver: DeletionObjectResolver,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing deletion API routes",
            event="api_route_deletion_init_start",
        )
        if not isinstance(request_deletion, RequestDeletion):
            raise APIConfigurationError(
                "request_deletion must be a RequestDeletion command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="request_deletion",
                context={"received_type": type(request_deletion).__name__},
            )

        self._request_deletion = request_deletion
        self._authorize = require_route_authorizer(authorizer)
        self._admission_gate = _require_deletion_gate(deletion_admission_gate)
        self._object_resolver = _require_object_resolver(deletion_object_resolver)

        router = APIRouter(prefix="/orders", tags=["deletion"])
        router.add_api_route(
            "/{order_id}/delete",
            self.delete,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="request_order_deletion",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_deletion_initialized",
                "registered_route_count": 1,
            }
        )

    async def _check_admission(
        self,
        request: Request,
        order_id: str,
        actor: str | None,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Checking deletion admission policy",
            event="api_route_deletion_admission_start",
            context={"order_id": order_id},
        )
        try:
            result = self._admission_gate(request, order_id, actor)
            if inspect.isawaitable(result):
                result = await result
        except APIError:
            raise
        except Exception as exc:
            raise APIInternalError(
                "Deletion admission gate failed outside the BIMAP API error contract.",
                component=_COMPONENT,
                operation="check_deletion_admission",
                context={
                    "order_id": order_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if result is not None:
            raise APIInternalError(
                "Deletion admission gate must return None on successful admission.",
                component=_COMPONENT,
                operation="check_deletion_admission",
                field="result",
                context={"received_type": type(result).__name__},
            )

    async def _resolve_object_ids(
        self,
        request: Request,
        order_id: str,
        actor: str | None,
    ) -> tuple[str, ...]:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving deletion storage objects",
            event="api_route_deletion_objects_resolve_start",
            context={"order_id": order_id},
        )
        try:
            result = self._object_resolver(request, order_id, actor)
            if inspect.isawaitable(result):
                result = await result
        except APIError:
            raise
        except Exception as exc:
            raise APIInternalError(
                "Deletion object resolver failed outside the BIMAP API error contract.",
                component=_COMPONENT,
                operation="resolve_deletion_objects",
                context={
                    "order_id": order_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        return normalize_route_texts(
            result,
            field="object_ids",
            allow_empty=True,
            error_type=APIInternalError,
        )

    async def delete(self, request: Request, order_id: str) -> Response:
        """POST ``/orders/{order_id}/delete`` -> execute due retention deletion."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling order deletion request",
            event="api_route_deletion_delete_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="request_deletion",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="request_deletion",
            resource_id=target,
        )
        idempotency_key = require_idempotency_key(request)

        # Empty body or {} is accepted for HTTP-client convenience.  No client
        # field is currently meaningful; in particular object_ids are forbidden.
        payload = await read_json_object(request, required=False)
        validate_object_fields(payload)

        await self._check_admission(request, target, actor)
        object_ids = await self._resolve_object_ids(request, target, actor)

        order = self._request_deletion.execute(
            target,
            object_ids=object_ids,
            idempotency_key=idempotency_key,
            actor=actor,
        )

        logger.info(
            {
                "event": "api_route_deletion_completed",
                "order_id": order.order_id,
                "state": order.state.value,
                "version": order.version,
                "deleted_object_target_count": len(object_ids),
            }
        )
        return json_response(
            order_to_public_dict(order),
            headers={"Cache-Control": "no-store"},
        )


__all__ = [
    "DeletionAdmissionGate",
    "DeletionObjectResolver",
    "RouteDeletion",
]