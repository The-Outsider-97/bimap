"""FastAPI routes for BIMAP model conversion."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Request, status
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse

from .auth import resolve_authenticated_account
from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.convert_model import ConvertModel
from ...app.services.authentication_service import AuthenticationService
from ...app.utils.app_errors import AppValidationError, UnsupportedAppInputError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Conversions")
printer = PrettyPrinter()

_COMPONENT = "api_route_conversions"
_STREAM_CHUNK_BYTES = 1024 * 1024


def _stream_artifact(stream) -> Iterator[bytes]:
    try:
        while True:
            chunk = stream.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            yield bytes(chunk)
    finally:
        stream.close()


class RouteConversions:
    __slots__ = ("router", "_convert_model", "_authentication")

    def __init__(
        self,
        convert_model: ConvertModel,
        authentication: AuthenticationService,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing model-conversion API routes",
            event="api_route_conversions_init_start",
        )
        if not isinstance(convert_model, ConvertModel):
            raise APIConfigurationError(
                "convert_model must be a ConvertModel command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="convert_model",
                context={"received_type": type(convert_model).__name__},
            )
        if not isinstance(authentication, AuthenticationService):
            raise APIConfigurationError(
                "authentication must be an AuthenticationService.",
                component=_COMPONENT,
                operation="initialize",
                field="authentication",
                context={"received_type": type(authentication).__name__},
            )

        self._convert_model = convert_model
        self._authentication = authentication

        router = APIRouter(prefix="/conversions", tags=["model-conversion"])
        router.add_api_route(
            "/capabilities",
            self.capabilities,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="model_conversion_capabilities",
        )
        router.add_api_route(
            "",
            self.convert,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            name="convert_model",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_conversions_initialized",
                "registered_route_count": 2,
            }
        )

    async def capabilities(self) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Returning model-conversion capabilities",
            event="api_route_conversions_capabilities_start",
        )
        return Response(
            content=json_bytes(
                {
                    "sources": tuple(
                        item.to_dict() for item in self._convert_model.capabilities
                    ),
                }
            ),
            status_code=status.HTTP_200_OK,
            headers={"Cache-Control": "public, max-age=300"},
            media_type="application/json",
        )

    @staticmethod
    def _map_validation_error(exc: AppValidationError) -> APIError:
        field = getattr(exc, "field", None)
        if field == "entitlement":
            return APIForbiddenError(
                "Account conversion entitlement is exhausted.",
                public_message=(
                    "Your conversion allowance is exhausted for the current renewal period."
                ),
                component=_COMPONENT,
                operation="convert",
                field=field,
                cause=exc,
            )
        if field == "malware":
            return APIValidationError(
                "Conversion source did not pass the required malware gate.",
                public_message=(
                    "The uploaded model did not pass the required safety scan."
                ),
                component=_COMPONENT,
                operation="convert",
                field=field,
                cause=exc,
            )
        if field == "source_size":
            return APIValidationError(
                "Conversion source exceeds the configured size limit.",
                public_message="The uploaded model is larger than the configured conversion limit.",
                component=_COMPONENT,
                operation="convert",
                field=field,
                cause=exc,
            )
        return APIValidationError(
            "Conversion request contains an invalid or unsupported model.",
            public_message=(
                "The source model is invalid, unsupported, or has no convertible 3D geometry."
            ),
            component=_COMPONENT,
            operation="convert",
            field=field,
            cause=exc,
        )

    async def convert(
        self,
        request: Request,
    ) -> StreamingResponse:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling model-conversion request",
            event="api_route_conversions_convert_start",
        )
        account = resolve_authenticated_account(self._authentication, request)
        idempotency_key = require_idempotency_key(request)

        try:
            form = await request.form(max_files=1, max_fields=2)
        except Exception as exc:
            raise APIValidationError(
                "Model-conversion multipart body could not be parsed.",
                public_message="The model-conversion upload is malformed.",
                component=_COMPONENT,
                operation="convert",
                field="body",
                cause=exc,
            ) from exc

        allowed_fields = {"source", "target_format", "conversion_id"}
        unexpected = tuple(sorted(set(form.keys()) - allowed_fields))
        if unexpected:
            raise APIValidationError(
                "Model-conversion request contains unsupported form fields.",
                public_message="The model-conversion request contains unsupported fields.",
                component=_COMPONENT,
                operation="convert",
                field="body",
                context={"unexpected_fields": unexpected},
            )

        for field_name in allowed_fields:
            if len(form.getlist(field_name)) != 1:
                raise APIValidationError(
                    "Model-conversion form field must occur exactly once.",
                    public_message="The model-conversion request is missing or duplicates a required field.",
                    component=_COMPONENT,
                    operation="convert",
                    field=field_name,
                )

        source = form.get("source")
        if not isinstance(source, UploadFile):
            raise APIValidationError(
                "source must be a multipart file upload.",
                public_message="Choose an IFC source file before starting conversion.",
                component=_COMPONENT,
                operation="convert",
                field="source",
            )

        target_format = require_api_text(
            form.get("target_format"),
            field="target_format",
            component=_COMPONENT,
            operation="convert",
            max_length=16,
        )
        conversion_id = require_api_text(
            form.get("conversion_id"),
            field="conversion_id",
            component=_COMPONENT,
            operation="convert",
            max_length=128,
        )

        filename = source.filename or ""
        if not filename.strip():
            raise APIValidationError(
                "Uploaded source filename is missing.",
                public_message="Choose an IFC source file before starting conversion.",
                component=_COMPONENT,
                operation="convert",
                field="source.filename",
            )

        try:
            try:
                result = await run_in_threadpool(
                    self._convert_model.execute,
                    account_id=account.account_id,
                    conversion_id=conversion_id,
                    idempotency_key=idempotency_key,
                    source=source.file,
                    filename=filename,
                    content_type=source.content_type,
                    target_format=target_format,
                )
            except (UnsupportedAppInputError, AppValidationError) as exc:
                raise self._map_validation_error(exc) from exc
        finally:
            await source.close()

        artifact = result.artifact
        headers = {
            "Cache-Control": "no-store",
            "Content-Disposition": require_header_value(
                f'attachment; filename="{artifact.filename}"'
            ),
            "X-BIMAP-Conversion-ID": require_header_value(result.conversion_id),
            "X-BIMAP-Output-Filename": require_header_value(artifact.filename),
            "X-BIMAP-Output-SHA256": require_header_value(artifact.content_hash),
            "X-BIMAP-Source-SHA256": require_header_value(result.source_hash),
            "X-BIMAP-IFC-Schema": require_header_value(result.inspection.schema),
            "X-BIMAP-Output-Bytes": str(artifact.size_bytes),
        }

        logger.info(
            {
                "event": "api_route_conversions_convert_completed",
                "account_id": account.account_id,
                "conversion_id": result.conversion_id,
                "output_size_bytes": artifact.size_bytes,
                "ifc_schema": result.inspection.schema,
            }
        )

        return StreamingResponse(
            _stream_artifact(artifact.stream),
            status_code=status.HTTP_200_OK,
            media_type=artifact.content_type,
            headers=headers,
        )


__all__ = ["RouteConversions"]
