"""FastAPI routes for authenticated BIMAP IFC data extraction."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Request, status
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.responses import Response, StreamingResponse

from ._shared import *
from .auth import resolve_authenticated_account
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.extract_model_data import ExtractModelData
from ...app.services.authentication_service import AuthenticationService
from ...app.utils.app_errors import (
    AppConfigurationError,
    AppValidationError,
    UnsupportedAppInputError,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Data Extractions")
printer = PrettyPrinter()

_COMPONENT = "api_route_data_extractions"
_STREAM_CHUNK_BYTES = 1024 * 1024


def _stream_package(stream) -> Iterator[bytes]:
    try:
        while True:
            chunk = stream.read(_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            yield bytes(chunk)
    finally:
        stream.close()


def _parse_email_result(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise APIValidationError(
        "email_result must be a boolean form value.",
        public_message="Email delivery selection is invalid.",
        component=_COMPONENT,
        operation="extract",
        field="email_result",
    )


def _parse_datasets(value: str) -> tuple[str, ...]:
    datasets: list[str] = []
    for raw in value.split(","):
        normalized = raw.strip().casefold()
        if not normalized:
            continue
        if normalized not in datasets:
            datasets.append(normalized)

    if not datasets:
        raise APIValidationError(
            "At least one data-extraction dataset is required.",
            public_message="Select at least one dataset to extract.",
            component=_COMPONENT,
            operation="extract",
            field="datasets",
        )

    return tuple(datasets)


class RouteDataExtractions:
    __slots__ = ("router", "_extract_model_data", "_authentication")

    def __init__(
        self,
        extract_model_data: ExtractModelData,
        authentication: AuthenticationService,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing data-extraction API routes",
            event="api_route_data_extractions_init_start",
        )

        if not isinstance(extract_model_data, ExtractModelData):
            raise APIConfigurationError(
                "extract_model_data must be an ExtractModelData command handler.",
                component=_COMPONENT,
                operation="initialize",
                field="extract_model_data",
                context={"received_type": type(extract_model_data).__name__},
            )
        if not isinstance(authentication, AuthenticationService):
            raise APIConfigurationError(
                "authentication must be an AuthenticationService.",
                component=_COMPONENT,
                operation="initialize",
                field="authentication",
                context={"received_type": type(authentication).__name__},
            )

        self._extract_model_data = extract_model_data
        self._authentication = authentication

        router = APIRouter(
            prefix="/data-extractions",
            tags=["data-extraction"],
        )
        router.add_api_route(
            "/capabilities",
            self.capabilities,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="data_extraction_capabilities",
        )
        router.add_api_route(
            "",
            self.extract,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            name="extract_model_data",
        )
        self.router = router

    async def capabilities(self) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Returning data-extraction capabilities",
            event="api_route_data_extractions_capabilities_start",
        )

        return Response(
            content=json_bytes(
                {
                    "sources": tuple(
                        item.to_dict()
                        for item in self._extract_model_data.capabilities
                    ),
                    "email_available": self._extract_model_data.email_available,
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
                "Account data-extraction entitlement is exhausted.",
                public_message=(
                    "Your data-extraction allowance is exhausted for the "
                    "current renewal period."
                ),
                component=_COMPONENT,
                operation="extract",
                field=field,
                cause=exc,
            )

        if field == "malware":
            return APIValidationError(
                "Extraction source did not pass the required malware gate.",
                public_message=(
                    "The uploaded model did not pass the required safety scan."
                ),
                component=_COMPONENT,
                operation="extract",
                field=field,
                cause=exc,
            )

        if field == "source_size":
            return APIValidationError(
                "Extraction source exceeds the configured size limit.",
                public_message=(
                    "The uploaded model is larger than the configured "
                    "data-extraction limit."
                ),
                component=_COMPONENT,
                operation="extract",
                field=field,
                cause=exc,
            )

        return APIValidationError(
            "Data-extraction request contains invalid or unsupported model data.",
            public_message=(
                "The IFC source or requested extraction scope is invalid "
                "or unsupported."
            ),
            component=_COMPONENT,
            operation="extract",
            field=field,
            cause=exc,
        )

    async def extract(self, request: Request) -> StreamingResponse:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling data-extraction request",
            event="api_route_data_extractions_extract_start",
        )

        # Authentication intentionally precedes multipart parsing so an
        # unauthenticated request cannot force large upload spooling first.
        account = resolve_authenticated_account(
            self._authentication,
            request,
        )
        idempotency_key = require_idempotency_key(request)

        try:
            form = await request.form(
                max_files=1,
                max_fields=3,
            )
        except Exception as exc:
            raise APIValidationError(
                "Data-extraction multipart body could not be parsed.",
                public_message="The data-extraction upload is malformed.",
                component=_COMPONENT,
                operation="extract",
                field="body",
                cause=exc,
            ) from exc

        allowed_fields = {
            "source",
            "extraction_id",
            "datasets",
            "email_result",
        }
        unexpected = tuple(
            sorted(set(form.keys()) - allowed_fields)
        )
        if unexpected:
            raise APIValidationError(
                "Data-extraction request contains unsupported form fields.",
                public_message=(
                    "The data-extraction request contains unsupported fields."
                ),
                component=_COMPONENT,
                operation="extract",
                field="body",
                context={"unexpected_fields": unexpected},
            )

        for field_name in allowed_fields:
            if len(form.getlist(field_name)) != 1:
                raise APIValidationError(
                    "Data-extraction form field must occur exactly once.",
                    public_message=(
                        "The data-extraction request is missing or duplicates "
                        "a required field."
                    ),
                    component=_COMPONENT,
                    operation="extract",
                    field=field_name,
                )

        source = form.get("source")
        if not isinstance(source, UploadFile):
            raise APIValidationError(
                "source must be a multipart file upload.",
                public_message=(
                    "Choose an IFC source file before starting extraction."
                ),
                component=_COMPONENT,
                operation="extract",
                field="source",
            )

        extraction_id = require_api_text(
            form.get("extraction_id"),
            field="extraction_id",
            component=_COMPONENT,
            operation="extract",
            max_length=128,
        )
        datasets = _parse_datasets(
            require_api_text(
                form.get("datasets"),
                field="datasets",
                component=_COMPONENT,
                operation="extract",
                max_length=256,
            )
        )
        email_result = _parse_email_result(
            require_api_text(
                form.get("email_result"),
                field="email_result",
                component=_COMPONENT,
                operation="extract",
                max_length=16,
            )
        )

        filename = source.filename or ""
        if not filename.strip():
            raise APIValidationError(
                "Uploaded source filename is missing.",
                public_message=(
                    "Choose an IFC source file before starting extraction."
                ),
                component=_COMPONENT,
                operation="extract",
                field="source.filename",
            )

        try:
            try:
                result = await run_in_threadpool(
                    self._extract_model_data.execute,
                    account_id=account.account_id,
                    extraction_id=extraction_id,
                    idempotency_key=idempotency_key,
                    source=source.file,
                    filename=filename,
                    content_type=source.content_type,
                    datasets=datasets,
                    email_result=email_result,
                )
            except (UnsupportedAppInputError, AppValidationError) as exc:
                raise self._map_validation_error(exc) from exc
            except AppConfigurationError as exc:
                raise APIServiceUnavailableError(
                    "Data-extraction delivery dependency is not configured.",
                    public_message=(
                        "Data extraction is temporarily unavailable for the "
                        "requested delivery option."
                    ),
                    component=_COMPONENT,
                    operation="extract",
                    cause=exc,
                ) from exc
        finally:
            await source.close()

        package = result.package
        headers = {
            "Cache-Control": "no-store",
            "Content-Disposition": require_header_value(
                f'attachment; filename="{package.filename}"'
            ),
            "X-BIMAP-Extraction-ID": require_header_value(
                result.extraction_id
            ),
            "X-BIMAP-Package-Filename": require_header_value(
                package.filename
            ),
            "X-BIMAP-Package-SHA256": require_header_value(
                package.content_hash
            ),
            "X-BIMAP-Source-SHA256": require_header_value(
                result.source_hash
            ),
            "X-BIMAP-IFC-Schema": require_header_value(
                result.inspection.schema
            ),
            "X-BIMAP-Product-Count": str(
                result.inspection.product_count
            ),
            "X-BIMAP-Package-Bytes": str(package.size_bytes),
            "X-BIMAP-JSON-Filename": require_header_value(
                package.json_filename
            ),
            "X-BIMAP-PDF-Filename": require_header_value(
                package.pdf_filename
            ),
            "X-BIMAP-Email-Status": require_header_value(
                result.email_status.value
            ),
        }

        logger.info(
            {
                "event": "api_route_data_extractions_extract_completed",
                "account_id": account.account_id,
                "extraction_id": result.extraction_id,
                "package_size_bytes": package.size_bytes,
                "ifc_schema": result.inspection.schema,
                "email_status": result.email_status.value,
            }
        )

        return StreamingResponse(
            _stream_package(package.stream),
            status_code=status.HTTP_200_OK,
            media_type=package.content_type,
            headers=headers,
        )


__all__ = ["RouteDataExtractions"]
