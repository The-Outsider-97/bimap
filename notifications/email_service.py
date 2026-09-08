"""BIMAP transactional email service and Notifications-port adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..app.ports.notifications import NotificationMessage, Notifications
from .email_models import *
from .email_renderer import EmailRenderer
from .utils.email_errors import *
from .utils.email_helpers import mask_email_address, require_text
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Email Service")
printer = PrettyPrinter()

_PORT_EVENT_MAP: dict[str, EmailNotificationType] = {
    "audit.completed": EmailNotificationType.AUDIT_COMPLETED,
    "audit.failed": EmailNotificationType.AUDIT_FAILED,
    "purchase.completed": EmailNotificationType.PURCHASE_COMPLETED,
    "purchase.failed": EmailNotificationType.PURCHASE_FAILED,
    "service.completed": EmailNotificationType.SERVICE_COMPLETED,
    "service.failed": EmailNotificationType.SERVICE_FAILED,
    "report.available": EmailNotificationType.AUDIT_COMPLETED,
    EmailNotificationType.AUDIT_COMPLETED.value: EmailNotificationType.AUDIT_COMPLETED,
    EmailNotificationType.AUDIT_FAILED.value: EmailNotificationType.AUDIT_FAILED,
    EmailNotificationType.PURCHASE_COMPLETED.value: EmailNotificationType.PURCHASE_COMPLETED,
    EmailNotificationType.PURCHASE_FAILED.value: EmailNotificationType.PURCHASE_FAILED,
    EmailNotificationType.SERVICE_COMPLETED.value: EmailNotificationType.SERVICE_COMPLETED,
    EmailNotificationType.SERVICE_FAILED.value: EmailNotificationType.SERVICE_FAILED,
}


class EmailService(Notifications):
    def __init__(
        self,
        renderer: EmailRenderer,
        transport: EmailTransport,
        *,
        reply_to: EmailAddress | None = None,
    ) -> None:
        printer.status("EMAIL", "Initializing transactional email service", "info")
        if not isinstance(renderer, EmailRenderer):
            raise EmailValidationError(
                "renderer must be EmailRenderer.",
                component="email_service",
                operation="initialize",
                field="renderer",
            )
        if not isinstance(transport, EmailTransport):
            raise EmailValidationError(
                "transport must implement EmailTransport.",
                component="email_service",
                operation="initialize",
                field="transport",
            )
        if reply_to is not None and not isinstance(reply_to, EmailAddress):
            raise EmailValidationError(
                "reply_to must be EmailAddress or None.",
                component="email_service",
                operation="initialize",
                field="reply_to",
            )
        self.renderer = renderer
        self.transport = transport
        self.reply_to = reply_to
        super().__init__()
        logger.info({"event": "email_service_initialized", "transport": type(transport).__name__})

    @staticmethod
    def _recipient(value: EmailAddress | str, *, name: str | None = None) -> EmailAddress:
        if isinstance(value, EmailAddress):
            if name is not None:
                raise EmailValidationError(
                    "Recipient name must not be supplied when recipient is already EmailAddress.",
                    component="email_service",
                    operation="normalize_recipient",
                    field="name",
                )
            return value
        return EmailAddress(email=value, name=name)

    def _deliver(
        self,
        *,
        recipient: EmailAddress,
        event_type: EmailNotificationType,
        data: Any,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> EmailDeliveryReceipt:
        content = self.renderer.render(event_type, data)
        outbound = OutboundEmail(
            recipient=recipient,
            content=content,
            event_type=event_type,
            reply_to=self.reply_to,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        try:
            receipt = self.transport.send(outbound)
        except EmailError:
            raise
        except Exception as exc:
            raise EmailTransportError(
                "Email transport failed unexpectedly.",
                component="email_service",
                operation="deliver",
                context={"event_type": event_type.value, "lower_error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        logger.info(
            {
                "event": "email_delivery_accepted",
                "event_type": event_type.value,
                "recipient": mask_email_address(recipient.email),
                "provider": receipt.provider,
                "message_id": receipt.message_id,
            }
        )
        return receipt

    def send_verification_email(
        self,
        recipient: EmailAddress | str,
        data: EmailVerificationData,
        *,
        recipient_name: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> EmailDeliveryReceipt:
        printer.status("EMAIL", "Sending signup verification email", "info")
        if not isinstance(data, EmailVerificationData):
            raise EmailValidationError(
                "data must be EmailVerificationData.",
                component="email_service",
                operation="send_verification_email",
                field="data",
            )
        target = self._recipient(recipient, name=recipient_name)
        return self._deliver(
            recipient=target,
            event_type=EmailNotificationType.EMAIL_VERIFICATION,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_audit_result_email(
        self,
        recipient: EmailAddress | str,
        data: AuditResultData,
        *,
        recipient_name: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> EmailDeliveryReceipt:
        printer.status("EMAIL", "Sending audit result email", "info")
        if not isinstance(data, AuditResultData):
            raise EmailValidationError(
                "data must be AuditResultData.",
                component="email_service",
                operation="send_audit_result_email",
                field="data",
            )
        event = EmailNotificationType.AUDIT_COMPLETED if data.successful else EmailNotificationType.AUDIT_FAILED
        return self._deliver(
            recipient=self._recipient(recipient, name=recipient_name),
            event_type=event,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_purchase_result_email(
        self,
        recipient: EmailAddress | str,
        data: PurchaseResultData,
        *,
        recipient_name: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> EmailDeliveryReceipt:
        printer.status("EMAIL", "Sending purchase result email", "info")
        if not isinstance(data, PurchaseResultData):
            raise EmailValidationError(
                "data must be PurchaseResultData.",
                component="email_service",
                operation="send_purchase_result_email",
                field="data",
            )
        event = EmailNotificationType.PURCHASE_COMPLETED if data.successful else EmailNotificationType.PURCHASE_FAILED
        return self._deliver(
            recipient=self._recipient(recipient, name=recipient_name),
            event_type=event,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_service_result_email(
        self,
        recipient: EmailAddress | str,
        data: ServiceResultData,
        *,
        recipient_name: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> EmailDeliveryReceipt:
        printer.status("EMAIL", "Sending service result email", "info")
        if not isinstance(data, ServiceResultData):
            raise EmailValidationError(
                "data must be ServiceResultData.",
                component="email_service",
                operation="send_service_result_email",
                field="data",
            )
        event = EmailNotificationType.SERVICE_COMPLETED if data.successful else EmailNotificationType.SERVICE_FAILED
        return self._deliver(
            recipient=self._recipient(recipient, name=recipient_name),
            event_type=event,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _metadata_text(
        metadata: Mapping[str, Any],
        key: str,
        *,
        required: bool = True,
        max_length: int = 4096,
    ) -> str | None:
        value = metadata.get(key)
        if value is None:
            if required:
                raise EmailValidationError(
                    "Notification metadata is missing a required field.",
                    component="email_service",
                    operation="notification_to_email",
                    field=key,
                )
            return None
        return require_text(value, field=key, max_length=max_length, allow_newlines=max_length > 512)

    def _event_from_message(self, message: NotificationMessage) -> EmailNotificationType:
        normalized = require_text(
            message.event_type,
            field="event_type",
            max_length=128,
            allow_newlines=False,
        ).casefold()
        event = _PORT_EVENT_MAP.get(normalized)
        if event is None:
            raise EmailUnsupportedEventError(
                "Notification event has no BIMAP email template.",
                component="email_service",
                operation="notification_to_email",
                field="event_type",
                context={"event_type": normalized},
            )
        return event

    def _send(self, message: NotificationMessage) -> None:
        printer.status("EMAIL", "Adapting BIMAP notification to email", "info")
        event = self._event_from_message(message)
        metadata = dict(message.metadata)
        user_name = self._metadata_text(metadata, "recipient_name", required=False, max_length=128)
        correlation_id = self._metadata_text(metadata, "correlation_id", required=False, max_length=128)
        recipient = EmailAddress(message.target_ref, user_name)

        if event in {EmailNotificationType.AUDIT_COMPLETED, EmailNotificationType.AUDIT_FAILED}:
            audit_id = self._metadata_text(metadata, "audit_id", required=False, max_length=256)
            data = AuditResultData(
                user_name=user_name,
                audit_name=self._metadata_text(metadata, "audit_name", max_length=256) or "",
                audit_id=audit_id or message.report_id or message.order_id,
                completed_at=self._metadata_text(metadata, "completed_at", max_length=128) or "",
                result_summary=self._metadata_text(metadata, "result_summary", max_length=4096) or "",
                action_url=self._metadata_text(metadata, "action_url", required=False, max_length=2048),
                successful=event is EmailNotificationType.AUDIT_COMPLETED,
            )
            self._deliver(
                recipient=recipient,
                event_type=event,
                data=data,
                idempotency_key=message.idempotency_key,
                correlation_id=correlation_id,
            )
            return

        if event in {EmailNotificationType.PURCHASE_COMPLETED, EmailNotificationType.PURCHASE_FAILED}:
            data = PurchaseResultData(
                user_name=user_name,
                item_name=self._metadata_text(metadata, "item_name", max_length=256) or "",
                order_id=message.order_id,
                occurred_at=self._metadata_text(metadata, "occurred_at", max_length=128) or "",
                amount_display=self._metadata_text(metadata, "amount_display", max_length=128) or "",
                status_display=self._metadata_text(metadata, "status_display", max_length=128) or "",
                action_url=self._metadata_text(metadata, "action_url", required=False, max_length=2048),
                successful=event is EmailNotificationType.PURCHASE_COMPLETED,
            )
            self._deliver(
                recipient=recipient,
                event_type=event,
                data=data,
                idempotency_key=message.idempotency_key,
                correlation_id=correlation_id,
            )
            return

        if event in {EmailNotificationType.SERVICE_COMPLETED, EmailNotificationType.SERVICE_FAILED}:
            service_id = self._metadata_text(metadata, "service_id", required=False, max_length=256)
            data = ServiceResultData(
                user_name=user_name,
                service_name=self._metadata_text(metadata, "service_name", max_length=256) or "",
                service_id=service_id or message.order_id,
                completed_at=self._metadata_text(metadata, "completed_at", max_length=128) or "",
                result_summary=self._metadata_text(metadata, "result_summary", max_length=4096) or "",
                status_display=self._metadata_text(metadata, "status_display", max_length=128) or "",
                action_url=self._metadata_text(metadata, "action_url", required=False, max_length=2048),
                successful=event is EmailNotificationType.SERVICE_COMPLETED,
            )
            self._deliver(
                recipient=recipient,
                event_type=event,
                data=data,
                idempotency_key=message.idempotency_key,
                correlation_id=correlation_id,
            )
            return

        raise EmailUnsupportedEventError(
            "Notification event cannot be delivered through the Notifications port.",
            component="email_service",
            operation="notification_to_email",
            field="event_type",
            context={"event_type": event.value},
        )


__all__ = ["EmailService"]
