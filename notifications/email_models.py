"""Provider-neutral email models for BIMAP transactional notifications."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .utils.email_errors import EmailValidationError
from .utils.email_helpers import *


class EmailNotificationType(str, Enum):
    EMAIL_VERIFICATION = "email_verification"
    AUDIT_COMPLETED = "audit_completed"
    AUDIT_FAILED = "audit_failed"
    PURCHASE_COMPLETED = "purchase_completed"
    PURCHASE_FAILED = "purchase_failed"
    SERVICE_COMPLETED = "service_completed"
    SERVICE_FAILED = "service_failed"


class EmailDeliveryStatus(str, Enum):
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class EmailAttachment:
    """Attachment payload accepted by the email service."""

    filename: str
    content_type: str
    payload: bytes
    content_sha256: str


@dataclass(frozen=True, slots=True)
class EmailAddress:
    email: str = field(repr=False)
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "email", normalize_email_address(self.email))
        object.__setattr__(self, "name", normalize_display_name(self.name, field="name"))


@dataclass(frozen=True, slots=True)
class EmailBranding:
    product_name: str = "BIMAP"
    team_name: str = "The Remy3Design Team"
    support_email: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_name",
            require_text(self.product_name, field="product_name", max_length=128, allow_newlines=False),
        )
        object.__setattr__(
            self,
            "team_name",
            require_text(self.team_name, field="team_name", max_length=128, allow_newlines=False),
        )
        if self.support_email is not None:
            object.__setattr__(
                self,
                "support_email",
                normalize_email_address(self.support_email, field="support_email"),
            )


@dataclass(frozen=True, slots=True)
class EmailContent:
    subject: str
    text_body: str = field(repr=False)
    html_body: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject",
            require_header_value(self.subject, field="subject", max_length=256),
        )
        object.__setattr__(
            self,
            "text_body",
            require_text(self.text_body, field="text_body", max_length=200_000),
        )
        object.__setattr__(
            self,
            "html_body",
            require_text(self.html_body, field="html_body", max_length=500_000),
        )


@dataclass(frozen=True, slots=True)
class OutboundEmail:
    recipient: EmailAddress = field(repr=False)
    content: EmailContent = field(repr=False)
    event_type: EmailNotificationType
    reply_to: EmailAddress | None = field(default=None, repr=False)
    idempotency_key: str | None = None
    correlation_id: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.recipient, EmailAddress):
            raise EmailValidationError(
                "recipient must be an EmailAddress.",
                component="email_models",
                operation="validate_outbound_email",
                field="recipient",
            )
        if not isinstance(self.content, EmailContent):
            raise EmailValidationError(
                "content must be EmailContent.",
                component="email_models",
                operation="validate_outbound_email",
                field="content",
            )
        try:
            event_type = (
                self.event_type
                if isinstance(self.event_type, EmailNotificationType)
                else EmailNotificationType(str(self.event_type))
            )
        except ValueError as exc:
            raise EmailValidationError(
                "Unsupported email notification type.",
                component="email_models",
                operation="validate_outbound_email",
                field="event_type",
                cause=exc,
            ) from exc
        if self.reply_to is not None and not isinstance(self.reply_to, EmailAddress):
            raise EmailValidationError(
                "reply_to must be an EmailAddress or None.",
                component="email_models",
                operation="validate_outbound_email",
                field="reply_to",
            )
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(
            self,
            "idempotency_key",
            optional_text(
                self.idempotency_key,
                field="idempotency_key",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "correlation_id",
            optional_text(
                self.correlation_id,
                field="correlation_id",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "headers",
            MappingProxyType(normalize_metadata_headers(self.headers)),
        )


@dataclass(frozen=True, slots=True)
class EmailDeliveryReceipt:
    provider: str
    message_id: str
    status: EmailDeliveryStatus
    accepted_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            require_text(self.provider, field="provider", max_length=64, allow_newlines=False),
        )
        object.__setattr__(
            self,
            "message_id",
            require_header_value(self.message_id, field="message_id", max_length=998),
        )
        if not isinstance(self.status, EmailDeliveryStatus):
            try:
                object.__setattr__(self, "status", EmailDeliveryStatus(str(self.status)))
            except ValueError as exc:
                raise EmailValidationError(
                    "Unsupported email delivery status.",
                    component="email_models",
                    operation="validate_delivery_receipt",
                    field="status",
                    cause=exc,
                ) from exc
        if not isinstance(self.accepted_at, datetime) or self.accepted_at.tzinfo is None:
            raise EmailValidationError(
                "accepted_at must be a timezone-aware datetime.",
                component="email_models",
                operation="validate_delivery_receipt",
                field="accepted_at",
            )
        object.__setattr__(self, "accepted_at", self.accepted_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class EmailVerificationData:
    user_name: str | None
    verification_code: str = field(repr=False)
    expires_in_minutes: int = 15

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "user_name",
            optional_text(self.user_name, field="user_name", max_length=128, allow_newlines=False),
        )
        object.__setattr__(self, "verification_code", require_verification_code(self.verification_code))
        if isinstance(self.expires_in_minutes, bool) or not isinstance(self.expires_in_minutes, int):
            raise EmailValidationError(
                "expires_in_minutes must be an integer.",
                component="email_models",
                operation="validate_verification_data",
                field="expires_in_minutes",
            )
        if not 1 <= self.expires_in_minutes <= 1440:
            raise EmailValidationError(
                "expires_in_minutes must be between 1 and 1440.",
                component="email_models",
                operation="validate_verification_data",
                field="expires_in_minutes",
            )


@dataclass(frozen=True, slots=True)
class AuditResultData:
    user_name: str | None
    audit_name: str
    audit_id: str
    completed_at: str
    result_summary: str
    action_url: str | None = None
    successful: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "user_name",
            optional_text(
                self.user_name,
                field="user_name",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "audit_name",
            require_text(
                self.audit_name,
                field="audit_name",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "audit_id",
            require_text(
                self.audit_id,
                field="audit_id",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "completed_at",
            require_text(
                self.completed_at,
                field="completed_at",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "result_summary",
            require_text(
                self.result_summary,
                field="result_summary",
                max_length=4096,
            ),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
        if not isinstance(self.successful, bool):
            raise EmailValidationError(
                "successful must be boolean.",
                component="email_models",
                operation="validate_audit_result",
                field="successful",
            )


@dataclass(frozen=True, slots=True)
class PurchaseResultData:
    user_name: str | None
    item_name: str
    order_id: str
    occurred_at: str
    amount_display: str
    status_display: str
    action_url: str | None = None
    successful: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "user_name",
            optional_text(
                self.user_name,
                field="user_name",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "item_name",
            require_text(
                self.item_name,
                field="item_name",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "order_id",
            require_text(
                self.order_id,
                field="order_id",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "occurred_at",
            require_text(
                self.occurred_at,
                field="occurred_at",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "amount_display",
            require_text(
                self.amount_display,
                field="amount_display",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "status_display",
            require_text(
                self.status_display,
                field="status_display",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
        if not isinstance(self.successful, bool):
            raise EmailValidationError(
                "successful must be boolean.",
                component="email_models",
                operation="validate_purchase_result",
                field="successful",
            )


@dataclass(frozen=True, slots=True)
class ServiceResultData:
    user_name: str | None
    service_name: str
    service_id: str
    completed_at: str
    result_summary: str
    status_display: str
    action_url: str | None = None
    successful: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "user_name",
            optional_text(
                self.user_name,
                field="user_name",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "service_name",
            require_text(
                self.service_name,
                field="service_name",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "service_id",
            require_text(
                self.service_id,
                field="service_id",
                max_length=256,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "completed_at",
            require_text(
                self.completed_at,
                field="completed_at",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "result_summary",
            require_text(
                self.result_summary,
                field="result_summary",
                max_length=4096,
            ),
        )
        object.__setattr__(
            self,
            "status_display",
            require_text(
                self.status_display,
                field="status_display",
                max_length=128,
                allow_newlines=False,
            ),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
        if not isinstance(self.successful, bool):
            raise EmailValidationError(
                "successful must be boolean.",
                component="email_models",
                operation="validate_service_result",
                field="successful",
            )


@runtime_checkable
class EmailTransport(Protocol):
    def send(self, message: OutboundEmail) -> EmailDeliveryReceipt:
        ...


__all__ = [
    "EmailNotificationType",
    "EmailDeliveryStatus",
    "EmailAddress",
    "EmailBranding",
    "EmailContent",
    "OutboundEmail",
    "EmailDeliveryReceipt",
    "EmailVerificationData",
    "AuditResultData",
    "PurchaseResultData",
    "ServiceResultData",
    "EmailTransport",
]
