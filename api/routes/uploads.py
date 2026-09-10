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
import hashlib
import inspect

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, BinaryIO, TypeAlias
from fastapi import APIRouter, Request, Response, status # type: ignore
from starlette.concurrency import run_in_threadpool # type: ignore
from starlette.datastructures import UploadFile # type: ignore

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.create_upload_slot import CreateUploadSlot
from ...app.commands.stage_upload import StageUpload
from ...app.commands.validate_uploads import ValidateUploads
from ...app.utils.app_errors import AppValidationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Uploads")
printer = PrettyPrinter()

_COMPONENT = "api_route_uploads"
_UPLOAD_HASH_CHUNK_BYTES = 1024 * 1024

UploadManifestValidator: TypeAlias = Callable[
    [Request, str, Mapping[str, Any]],
    Mapping[str, Any] | None | Awaitable[Mapping[str, Any] | None],
]


# ===================================================================
# Helpers
# ===================================================================
def _measure_and_hash_source(stream: BinaryIO) -> tuple[int, str]:
    """
    Calculate a stable source SHA-256 using bounded memory and rewind the stream.

    The digest is calculated before storage so the resulting logical object ID
    is deterministic for the exact source content within one order.
    """

    try:
        stream.seek(0)
    except (AttributeError, OSError) as exc:
        raise APIValidationError(
            "Uploaded model stream is not seekable.",
            public_message="The selected model could not be read.",
            component=_COMPONENT,
            operation="stage_model",
            field="source",
            cause=exc,
        ) from exc

    digest = hashlib.sha256()
    size_bytes = 0

    try:
        while True:
            chunk = stream.read(_UPLOAD_HASH_CHUNK_BYTES)

            if not chunk:
                break

            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise APIValidationError(
                    "Uploaded model stream yielded non-binary data.",
                    public_message="The selected model is not a valid binary upload.",
                    component=_COMPONENT,
                    operation="stage_model",
                    field="source",
                )

            payload = bytes(chunk)
            digest.update(payload)
            size_bytes += len(payload)

    except APIError:
        raise
    except Exception as exc:
        raise APIValidationError(
            "Uploaded model could not be read.",
            public_message="The selected model could not be read.",
            component=_COMPONENT,
            operation="stage_model",
            field="source",
            cause=exc,
        ) from exc
    finally:
        try:
            stream.seek(0)
        except (AttributeError, OSError):
            pass

    if size_bytes <= 0:
        raise APIValidationError(
            "Uploaded model cannot be empty.",
            public_message="The selected model file is empty.",
            component=_COMPONENT,
            operation="stage_model",
            field="source",
        )

    return size_bytes, digest.hexdigest()


def _model_object_id(order_id: str, source_sha256: str) -> str:
    """
    Return an opaque deterministic storage identity for one order/source pair.

    Filename and client paths are intentionally excluded from storage identity.
    """
    identity = hashlib.sha256(f"{order_id}\0{source_sha256}".encode("utf-8")).hexdigest()
    return f"upload-{identity}"

# ===================================================================

class RouteUploads:
    """Dependency-injected upload preparation and validation route group."""

    __slots__ = (
        "router",
        "_create_upload_slot",
        "_stage_upload",
        "_validate_uploads",
        "_authorize",
        "_manifest_validator",
    )

    def __init__(
        self,
        create_upload_slot: CreateUploadSlot,
        stage_upload: StageUpload,
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
        if not isinstance(stage_upload, StageUpload):
            raise APIConfigurationError(
                "stage_upload must be a StageUpload command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="stage_upload",
                context={
                    "received_type": type(stage_upload).__name__,
                },
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
        self._stage_upload = stage_upload
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
            "/{order_id}/uploads/model",
            self.stage_model,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            response_class=Response,
            name="stage_model_upload",
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
                "registered_route_count": 3,
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

    async def stage_model(self, request: Request, order_id: str) -> Response:
        """
        POST /orders/{order_id}/uploads/model

        Stream one customer-selected model into BIMAP storage and admit it only
        after integrity verification and an explicit clean malware verdict.

        The returned source_ref identifies the validated raw source object. It is
        deliberately not represented as an EvidenceContract evidence_id.
        """

        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling model-upload request",
            event="api_route_uploads_stage_model_start",
            context={"order_id": order_id},
        )

        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="stage_model",
        )

        # Authorization deliberately precedes multipart parsing so an unauthorized
        # request cannot force a potentially large BIM upload to be spooled first.
        await authorize_request(
            self._authorize,
            request,
            operation="stage_model",
            resource_id=target,
        )

        try:
            form = await request.form(
                max_files=1,
                max_fields=1,
            )
        except Exception as exc:
            raise APIValidationError(
                "Model-upload multipart body could not be parsed.",
                public_message="The model upload is malformed.",
                component=_COMPONENT,
                operation="stage_model",
                field="body",
                cause=exc,
            ) from exc

        unexpected = tuple(
            sorted(set(form.keys()) - {"source"})
        )

        if unexpected:
            raise APIValidationError(
                "Model-upload request contains unsupported form fields.",
                public_message="The model upload contains unsupported fields.",
                component=_COMPONENT,
                operation="stage_model",
                field="body",
                context={
                    "unexpected_fields": unexpected,
                },
            )

        if len(form.getlist("source")) != 1:
            raise APIValidationError(
                "source must occur exactly once.",
                public_message="Choose exactly one model file to upload.",
                component=_COMPONENT,
                operation="stage_model",
                field="source",
            )

        source = form.get("source")

        if not isinstance(source, UploadFile):
            raise APIValidationError(
                "source must be a multipart file upload.",
                public_message="Choose a model file before uploading.",
                component=_COMPONENT,
                operation="stage_model",
                field="source",
            )

        filename = (source.filename or "").strip()

        if not filename:
            await source.close()

            raise APIValidationError(
                "Uploaded source filename is missing.",
                public_message="The selected model has no valid filename.",
                component=_COMPONENT,
                operation="stage_model",
                field="source.filename",
            )

        try:
            size_bytes, source_sha256 = await run_in_threadpool(
                _measure_and_hash_source,
                source.file,
            )

            object_id = _model_object_id(
                target,
                source_sha256,
            )

            try:
                result = await run_in_threadpool(
                    self._stage_upload.execute,
                    target,
                    source.file,
                    object_id=object_id,
                    filename=filename,
                    content_type=source.content_type,
                    expected_size_bytes=size_bytes,
                    expected_hash=source_sha256,
                    hash_algorithm="sha256",
                )
            except AppValidationError as exc:
                if getattr(exc, "field", None) == "malware_verdict":
                    raise APIValidationError(
                        "Uploaded model did not pass the required malware gate.",
                        public_message=(
                            "The uploaded model did not pass the required "
                            "safety scan."
                        ),
                        component=_COMPONENT,
                        operation="stage_model",
                        field="source",
                        cause=exc,
                    ) from exc

                raise

        finally:
            await source.close()

        logger.info(
            {
                "event": "api_route_uploads_stage_model_completed",
                "order_id": result.order_id,
                "object_id": result.stored_object.object_id,
                "size_bytes": result.stored_object.size_bytes,
                "content_hash": result.stored_object.content_hash,
                "hash_algorithm": result.stored_object.hash_algorithm,
                "malware_verdict": result.malware_scan.verdict.value,
            }
        )

        return json_response(
            {
                "order_id": result.order_id,

                # Deliberately named source_ref rather than evidence_id.
                "source_ref": result.stored_object.object_id,

                "filename": filename,
                "stored_object": result.stored_object.to_dict(),
                "malware_scan": result.malware_scan.to_dict(),
            },
            status_code=status.HTTP_201_CREATED,
            headers={
                "Cache-Control": "no-store",
            },
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
