"""Provider-neutral SMS models for BIMAP transactional notifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, runtime_checkable

from .utils.sms_errors import SMSValidationError
from .utils.sms_helpers import *


class SMSNotificationType(str, Enum):
    SMS_VERIFICATION = "sms_verification"
    SECURITY_ALERT = "security_alert"
    AUDIT_COMPLETED = "audit_completed"
    AUDIT_FAILED = "audit_failed"
    PURCHASE_COMPLETED = "purchase_completed"
    PURCHASE_FAILED = "purchase_failed"
    SERVICE_COMPLETED = "service_completed"
    SERVICE_FAILED = "service_failed"


class SMSDeliveryStatus(str, Enum):
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class SMSRecipient:
    phone_e164: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "phone_e164", normalize_e164_number(self.phone_e164))


@dataclass(frozen=True, slots=True)
class SMSBranding:
    product_name: str = "BIMAP"
    team_name: str = "Remy3Design"
    max_segments: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "product_name",
            require_text(
                self.product_name,
                field="product_name",
                max_length=32,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "team_name",
            require_text(
                self.team_name,
                field="team_name",
                max_length=32,
                allow_newlines=False,
            ),
        )
        if isinstance(self.max_segments, bool) or not isinstance(self.max_segments, int):
            raise SMSValidationError(
                "max_segments must be an integer.",
                component="sms_models",
                operation="validate_branding",
                field="max_segments",
            )
        if not 1 <= self.max_segments <= 10:
            raise SMSValidationError(
                "max_segments must be between 1 and 10.",
                component="sms_models",
                operation="validate_branding",
                field="max_segments",
            )


@dataclass(frozen=True, slots=True)
class SMSContent:
    body: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "body", require_text(self.body, field="body", max_length=10_000))

    @property
    def encoding(self) -> str:
        return analyze_sms_length(self.body).encoding

    @property
    def unit_count(self) -> int:
        return analyze_sms_length(self.body).units

    @property
    def segment_count(self) -> int:
        return analyze_sms_length(self.body).segments


@dataclass(frozen=True, slots=True)
class OutboundSMS:
    recipient: SMSRecipient = field(repr=False)
    content: SMSContent = field(repr=False)
    event_type: SMSNotificationType
    idempotency_key: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.recipient, SMSRecipient):
            raise SMSValidationError(
                "recipient must be SMSRecipient.",
                component="sms_models",
                operation="validate_outbound_sms",
                field="recipient",
            )
        if not isinstance(self.content, SMSContent):
            raise SMSValidationError(
                "content must be SMSContent.",
                component="sms_models",
                operation="validate_outbound_sms",
                field="content",
            )
        try:
            event_type = (
                self.event_type
                if isinstance(self.event_type, SMSNotificationType)
                else SMSNotificationType(str(self.event_type))
            )
        except ValueError as exc:
            raise SMSValidationError(
                "Unsupported SMS notification type.",
                component="sms_models",
                operation="validate_outbound_sms",
                field="event_type",
                cause=exc,
            ) from exc
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


@dataclass(frozen=True, slots=True)
class SMSDeliveryReceipt:
    provider: str
    message_id: str
    status: SMSDeliveryStatus
    accepted_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider",
            require_text(
                self.provider,
                field="provider",
                max_length=64,
                allow_newlines=False,
            ),
        )
        object.__setattr__(
            self,
            "message_id",
            require_text(
                self.message_id,
                field="message_id",
                max_length=512,
                allow_newlines=False,
            ),
        )
        if not isinstance(self.status, SMSDeliveryStatus):
            try:
                object.__setattr__(self, "status", SMSDeliveryStatus(str(self.status)))
            except ValueError as exc:
                raise SMSValidationError(
                    "Unsupported SMS delivery status.",
                    component="sms_models",
                    operation="validate_delivery_receipt",
                    field="status",
                    cause=exc,
                ) from exc
        if not isinstance(self.accepted_at, datetime) or self.accepted_at.tzinfo is None:
            raise SMSValidationError(
                "accepted_at must be a timezone-aware datetime.",
                component="sms_models",
                operation="validate_delivery_receipt",
                field="accepted_at",
            )
        object.__setattr__(self, "accepted_at", self.accepted_at.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class SMSVerificationData:
    verification_code: str = field(repr=False)
    expires_in_minutes: int = 15
    user_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "verification_code", require_sms_verification_code(self.verification_code))
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
        if isinstance(self.expires_in_minutes, bool) or not isinstance(self.expires_in_minutes, int):
            raise SMSValidationError(
                "expires_in_minutes must be an integer.",
                component="sms_models",
                operation="validate_verification_data",
                field="expires_in_minutes",
            )
        if not 1 <= self.expires_in_minutes <= 1440:
            raise SMSValidationError(
                "expires_in_minutes must be between 1 and 1440.",
                component="sms_models",
                operation="validate_verification_data",
                field="expires_in_minutes",
            )


@dataclass(frozen=True, slots=True)
class SecurityAlertSMSData:
    alert_name: str
    occurred_at: str
    action_url: str | None = None
    user_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "alert_name",
            compact_sms_text(self.alert_name, field="alert_name", max_length=192),
        )
        object.__setattr__(
            self,
            "occurred_at",
            compact_sms_text(self.occurred_at, field="occurred_at", max_length=128),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
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


@dataclass(frozen=True, slots=True)
class AuditSMSData:
    audit_name: str
    audit_id: str
    action_url: str | None = None
    successful: bool = True
    user_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "audit_name",
            compact_sms_text(self.audit_name, field="audit_name", max_length=192),
        )
        object.__setattr__(
            self,
            "audit_id",
            compact_sms_text(self.audit_id, field="audit_id", max_length=128),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
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
        if not isinstance(self.successful, bool):
            raise SMSValidationError(
                "successful must be boolean.",
                component="sms_models",
                operation="validate_audit_data",
                field="successful",
            )


@dataclass(frozen=True, slots=True)
class PurchaseSMSData:
    item_name: str
    order_id: str
    amount_display: str
    status_display: str
    action_url: str | None = None
    successful: bool = True
    user_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "item_name",
            compact_sms_text(self.item_name, field="item_name", max_length=192),
        )
        object.__setattr__(
            self,
            "order_id",
            compact_sms_text(self.order_id, field="order_id", max_length=128),
        )
        object.__setattr__(
            self,
            "amount_display",
            compact_sms_text(self.amount_display, field="amount_display", max_length=64),
        )
        object.__setattr__(
            self,
            "status_display",
            compact_sms_text(self.status_display, field="status_display", max_length=64),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
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
        if not isinstance(self.successful, bool):
            raise SMSValidationError(
                "successful must be boolean.",
                component="sms_models",
                operation="validate_purchase_data",
                field="successful",
            )


@dataclass(frozen=True, slots=True)
class ServiceSMSData:
    service_name: str
    service_id: str
    status_display: str
    action_url: str | None = None
    successful: bool = True
    user_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "service_name",
            compact_sms_text(self.service_name, field="service_name", max_length=192),
        )
        object.__setattr__(
            self,
            "service_id",
            compact_sms_text(self.service_id, field="service_id", max_length=128),
        )
        object.__setattr__(
            self,
            "status_display",
            compact_sms_text(self.status_display, field="status_display", max_length=64),
        )
        object.__setattr__(self, "action_url", normalize_action_url(self.action_url))
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
        if not isinstance(self.successful, bool):
            raise SMSValidationError(
                "successful must be boolean.",
                component="sms_models",
                operation="validate_service_data",
                field="successful",
            )


@runtime_checkable
class SMSTransport(Protocol):
    def send(self, message: OutboundSMS) -> SMSDeliveryReceipt:
        ...


__all__ = [
    "SMSNotificationType",
    "SMSDeliveryStatus",
    "SMSRecipient",
    "SMSBranding",
    "SMSContent",
    "OutboundSMS",
    "SMSDeliveryReceipt",
    "SMSVerificationData",
    "SecurityAlertSMSData",
    "AuditSMSData",
    "PurchaseSMSData",
    "ServiceSMSData",
    "SMSTransport",
]
