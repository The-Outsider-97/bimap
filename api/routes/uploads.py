"""
FastAPI routes for BIMAP upload lifecycle admission.

The documented HTTP surface contains:

* ``POST /orders/{order_id}/uploads`` to prepare the order for uploads; and
* ``POST /orders/{order_id}/validate`` to commit a fully validated upload set.

The current ``CreateUploadSlot`` command deliberately does not fabricate a
presigned URL because the current Storage port has no provider-neutral upload-
slot contract.  This route therefore returns the authoritative order state it
can actually produce rather than pretending a signed slot exists.

Likewise, ``ValidateUploads`` is only the lifecycle commit point after the full
staged upload set has been verified.  To prevent an untrusted client from self-
certifying its own uploads, this route requires an injected
``UploadManifestValidator``.  The validator owns the missing deployment-
specific manifest/completeness admission check and may return safe lifecycle
metadata.  No permissive default exists.
"""

from __future__ import annotations

import inspect

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeAlias
from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.create_upload_slot import CreateUploadSlot
from ...app.commands.validate_uploads import ValidateUploads
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Uploads")
printer = PrettyPrinter()

_COMPONENT = "api_route_uploads"

UploadManifestValidator: TypeAlias = Callable[
    [Request, str, Mapping[str, Any]],
    Mapping[str, Any] | None | Awaitable[Mapping[str, Any] | None],
]


class RouteUploads:
    """Dependency-injected upload preparation and validation route group."""

    __slots__ = (
        "router",
        "_create_upload_slot",
        "_validate_uploads",
        "_authorize",
        "_manifest_validator",
    )

    def __init__(
        self,
        create_upload_slot: CreateUploadSlot,
        validate_uploads: ValidateUploads,
        *,
        authorizer: RouteAuthorizer,
        manifest_validator: UploadManifestValidator,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing uploads API routes",
            event="api_route_uploads_init_start",
        )
        if not isinstance(create_upload_slot, CreateUploadSlot):
            raise APIConfigurationError(
                "create_upload_slot must be a CreateUploadSlot command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="create_upload_slot",
                context={"received_type": type(create_upload_slot).__name__},
            )
        if not isinstance(validate_uploads, ValidateUploads):
            raise APIConfigurationError(
                "validate_uploads must be a ValidateUploads command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="validate_uploads",
                context={"received_type": type(validate_uploads).__name__},
            )
        if not callable(manifest_validator):
            raise APIConfigurationError(
                "manifest_validator must be a callable upload-set validation gate.",
                component=_COMPONENT,
                operation="initialize",
                field="manifest_validator",
                context={"received_type": type(manifest_validator).__name__},
            )

        self._create_upload_slot = create_upload_slot
        self._validate_uploads = validate_uploads
        self._authorize = require_route_authorizer(authorizer)
        self._manifest_validator = manifest_validator

        router = APIRouter(prefix="/orders", tags=["uploads"])
        router.add_api_route(
            "/{order_id}/uploads",
            self.begin,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="create_upload_slot",
        )
        router.add_api_route(
            "/{order_id}/validate",
            self.validate,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="validate_uploads",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_uploads_initialized",
                "registered_route_count": 2,
            }
        )

    async def begin(self, request: Request, order_id: str) -> Response:
        """POST ``/orders/{order_id}/uploads`` -> enter canonical uploading state."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling begin-upload request",
            event="api_route_uploads_begin_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="begin_upload",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="begin_upload",
            resource_id=target,
        )
        idempotency_key = require_idempotency_key(request)

        # No client-selected upload_session_id is accepted here.  The current
        # command explicitly permits the outer composition/infrastructure layer
        # to supply one, but BIMAP does not yet define a public session-ID policy.
        order = self._create_upload_slot.execute(
            target,
            idempotency_key=idempotency_key,
            upload_session_id=None,
            actor=actor,
        )
        logger.info(
            {
                "event": "api_route_uploads_begin_completed",
                "order_id": order.order_id,
                "state": order.state.value,
                "version": order.version,
                "has_upload_session": order.upload_session_id is not None,
            }
        )
        return json_response(
            order_to_public_dict(order),
            headers={"Cache-Control": "no-store"},
        )

    async def _validate_manifest(
        self,
        request: Request,
        order_id: str,
        manifest: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Run the injected complete-upload admission gate and normalize its result."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating staged upload manifest",
            event="api_route_uploads_manifest_validate_start",
            context={"order_id": order_id},
        )
        try:
            result = self._manifest_validator(request, order_id, manifest)
            if inspect.isawaitable(result):
                result = await result
        except APIError:
            raise
        except Exception as exc:
            raise APIInternalError(
                "Upload manifest validator failed outside the BIMAP API error contract.",
                component=_COMPONENT,
                operation="validate_manifest",
                context={"order_id": order_id, **lower_error_context(exc)},
                cause=exc,
            ) from exc

        if result is None:
            return None
        if not isinstance(result, Mapping):
            raise APIInternalError(
                "Upload manifest validator returned an unsupported result type.",
                component=_COMPONENT,
                operation="validate_manifest",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return dict(result)

    async def validate(self, request: Request, order_id: str) -> Response:
        """POST ``/orders/{order_id}/validate`` -> validate then commit lifecycle."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling validate-uploads request",
            event="api_route_uploads_validate_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="validate_uploads",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="validate_uploads",
            resource_id=target,
        )
        idempotency_key = require_idempotency_key(request)
        manifest = await read_json_object(request)
        metadata = await self._validate_manifest(request, target, manifest)

        order = self._validate_uploads.execute(
            target,
            idempotency_key=idempotency_key,
            actor=actor,
            metadata=metadata,
        )
        logger.info(
            {
                "event": "api_route_uploads_validate_completed",
                "order_id": order.order_id,
                "state": order.state.value,
                "version": order.version,
            }
        )
        return json_response(
            order_to_public_dict(order),
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["UploadManifestValidator", "RouteUploads"]
