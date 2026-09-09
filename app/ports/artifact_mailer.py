"""Provider-neutral binary artifact email-delivery port for BIMAP."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Artifact Mailer Port")
printer = PrettyPrinter()

_COMPONENT = "artifact_mailer"


@dataclass(frozen=True, slots=True)
class ArtifactEmailReceipt:
    provider: str
    message_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            require_app_text(
                self.provider,
                field="provider",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_receipt",
                max_length=128,
            ),
        )
        object.__setattr__(
            self,
            "message_id",
            require_app_text(
                self.message_id,
                field="message_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_receipt",
                max_length=998,
            ),
        )


class ArtifactMailer(ABC):
    @abstractmethod
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
        raise NotImplementedError


__all__ = [
    "ArtifactEmailReceipt",
    "ArtifactMailer",
]
