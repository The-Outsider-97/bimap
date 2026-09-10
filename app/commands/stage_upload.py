"""
Stage-upload application command for BIMAP.

This command exposes the existing secure UploadService staging boundary to
outer transports without duplicating storage, hashing, malware-scanning, or
order-lifecycle behavior.

It deliberately does not:

- infer product-specific file requirements;
- convert a raw model into canonical audit evidence;
- manufacture EvidenceContract identifiers;
- validate a complete upload manifest; or
- change the order lifecycle after the source has been staged.

Those responsibilities remain with their existing owning layers.
"""

from __future__ import annotations

from typing import BinaryIO

from ..services.upload_service import UploadService, ValidatedUpload
from ..utils.app_errors import (
    AppConfigurationError,
    AppError,
    AppIntegrityError,
)
from ..utils.app_helpers import (
    announce_app_action,
    lower_error_context,
)

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Stage Upload Command")
printer = PrettyPrinter()

_COMPONENT = "stage_upload_command"


class StageUpload:
    """Stage and malware-validate one upload through UploadService."""

    __slots__ = ("_service",)

    def __init__(self, service: UploadService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing stage-upload command",
            event="stage_upload_command_init_start",
        )

        if not isinstance(service, UploadService):
            raise AppConfigurationError(
                "service must be an UploadService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={
                    "received_type": type(service).__name__,
                },
            )

        self._service = service

        logger.debug(
            {
                "event": "stage_upload_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        stream: BinaryIO,
        *,
        object_id: str,
        filename: str | None = None,
        content_type: str | None = None,
        expected_size_bytes: int | None = None,
        expected_hash: str | None = None,
        hash_algorithm: str = "sha256",
    ) -> ValidatedUpload:
        """
        Stage one source object and return it only after a clean malware verdict.

        Storage and malware semantics remain authoritative in UploadService.
        """

        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing stage-upload command",
            event="stage_upload_command_execute_start",
            context={
                "order_id": order_id,
                "object_id": object_id,
            },
        )

        try:
            result = self._service.stage_and_validate(
                order_id,
                stream,
                object_id=object_id,
                filename=filename,
                content_type=content_type,
                expected_size_bytes=expected_size_bytes,
                expected_hash=expected_hash,
                hash_algorithm=hash_algorithm,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "UploadService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={
                    "order_id": order_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if not isinstance(result, ValidatedUpload):
            raise AppIntegrityError(
                "Stage-upload service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={
                    "received_type": type(result).__name__,
                },
            )

        logger.info(
            {
                "event": "stage_upload_command_completed",
                "order_id": result.order_id,
                "object_id": result.stored_object.object_id,
                "size_bytes": result.stored_object.size_bytes,
                "hash_algorithm": result.stored_object.hash_algorithm,
                "malware_verdict": result.malware_scan.verdict.value,
            }
        )

        return result


__all__ = ["StageUpload"]
