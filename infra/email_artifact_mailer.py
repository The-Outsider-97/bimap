"""EmailService-backed binary artifact mailer for BIMAP service deliverables."""

from __future__ import annotations

from dataclasses import dataclass

from ..app.ports.artifact_mailer import ArtifactEmailReceipt, ArtifactMailer
from ..app.utils.app_errors import *
from ..app.utils.app_helpers import *
from ..notifications.email_models import ServiceResultData, EmailAttachment
from ..notifications.email_service import EmailService
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Email Artifact Mailer")
printer = PrettyPrinter()

_COMPONENT = "email_artifact_mailer"



class EmailArtifactMailer(ArtifactMailer):
    __slots__ = ("_email",)

    def __init__(self, email: EmailService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing email artifact mailer",
            event="email_artifact_mailer_init_start",
        )
        if not isinstance(email, EmailService):
            raise AppConfigurationError(
                "email must be an EmailService.",
                component=_COMPONENT,
                operation="initialize",
                field="email",
                context={"received_type": type(email).__name__},
            )
        self._email = email

    def send_data_extraction_package(
        self,
        *,
        recipient_email: str,
        recipient_name: str | None,
        extraction_id: str,
        completed_at: str,
        source_filename: str,
        package_filename: str,
        package_content_type: str,
        package_bytes: bytes,
        package_sha256: str,
        idempotency_key: str,
    ) -> ArtifactEmailReceipt:
        printer.status("EMAIL", "Sending data-extraction package", "info")

        normalized_extraction_id = require_app_text(
            extraction_id,
            field="extraction_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="send_data_extraction_package",
            max_length=128,
        )
        normalized_source = require_app_text(
            source_filename,
            field="source_filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="send_data_extraction_package",
            max_length=255,
        )
        normalized_hash = require_app_text(
            package_sha256,
            field="package_sha256",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="send_data_extraction_package",
            max_length=64,
        ).casefold()

        if (
            len(normalized_hash) != 64
            or any(character not in "0123456789abcdef" for character in normalized_hash)
        ):
            raise AppValidationError(
                "package_sha256 must be a SHA-256 hexadecimal digest.",
                component=_COMPONENT,
                operation="send_data_extraction_package",
                field="package_sha256",
            )

        attachment = EmailAttachment(
            filename=package_filename,
            content_type=package_content_type,
            payload=package_bytes,
            content_sha256=normalized_hash,
        )
        data = ServiceResultData(
            user_name=recipient_name,
            service_name="BIM data extraction",
            service_id=normalized_extraction_id,
            completed_at=completed_at,
            result_summary=(
                f"Data extraction from {normalized_source} completed. "
                "The attached ZIP contains the PDF summary and complete JSON dataset."
            ),
            status_display="Completed",
            action_url=None,
            successful=True,
        )

        receipt = self._email.send_service_result_email(
            recipient_email,
            data,
            recipient_name=recipient_name,
            idempotency_key=idempotency_key,
            attachments=(attachment,), # type: ignore
        )

        return ArtifactEmailReceipt(
            provider=receipt.provider,
            message_id=receipt.message_id,
        )


__all__ = ["EmailArtifactMailer"]
