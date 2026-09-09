"""BIMAP transactional SMS service and Notifications-port adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..app.ports.notifications import NotificationMessage, Notifications
from .sms_models import *
from .sms_renderer import SMSRenderer
from .utils.sms_errors import *
from .utils.sms_helpers import mask_phone_number, require_text
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SMS Service")
printer = PrettyPrinter()

_PORT_EVENT_MAP: dict[str, SMSNotificationType] = {
    "audit.completed": SMSNotificationType.AUDIT_COMPLETED,
    "audit.failed": SMSNotificationType.AUDIT_FAILED,
    "purchase.completed": SMSNotificationType.PURCHASE_COMPLETED,
    "purchase.failed": SMSNotificationType.PURCHASE_FAILED,
    "service.completed": SMSNotificationType.SERVICE_COMPLETED,
    "service.failed": SMSNotificationType.SERVICE_FAILED,
    "report.available": SMSNotificationType.AUDIT_COMPLETED,
    SMSNotificationType.AUDIT_COMPLETED.value: SMSNotificationType.AUDIT_COMPLETED,
    SMSNotificationType.AUDIT_FAILED.value: SMSNotificationType.AUDIT_FAILED,
    SMSNotificationType.PURCHASE_COMPLETED.value: SMSNotificationType.PURCHASE_COMPLETED,
    SMSNotificationType.PURCHASE_FAILED.value: SMSNotificationType.PURCHASE_FAILED,
    SMSNotificationType.SERVICE_COMPLETED.value: SMSNotificationType.SERVICE_COMPLETED,
    SMSNotificationType.SERVICE_FAILED.value: SMSNotificationType.SERVICE_FAILED,
}


class SMSService(Notifications):
    def __init__(self, renderer: SMSRenderer, transport: SMSTransport) -> None:
        printer.status("SMS", "Initializing transactional SMS service", "info")
        if not isinstance(renderer, SMSRenderer):
            raise SMSValidationError(
                "renderer must be SMSRenderer.",
                component="sms_service",
                operation="initialize",
                field="renderer",
            )
        if not isinstance(transport, SMSTransport):
            raise SMSValidationError(
                "transport must implement SMSTransport.",
                component="sms_service",
                operation="initialize",
                field="transport",
            )
        self.renderer = renderer
        self.transport = transport
        super().__init__()
        logger.info({"event": "sms_service_initialized", "transport": type(transport).__name__})

    @staticmethod
    def _recipient(value: SMSRecipient | str) -> SMSRecipient:
        if isinstance(value, SMSRecipient):
            return value
        return SMSRecipient(phone_e164=value)

    def _deliver(
        self,
        *,
        recipient: SMSRecipient,
        event_type: SMSNotificationType,
        data: Any,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SMSDeliveryReceipt:
        content = self.renderer.render(event_type, data)
        outbound = OutboundSMS(
            recipient=recipient,
            content=content,
            event_type=event_type,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        try:
            receipt = self.transport.send(outbound)
        except SMSError:
            raise
        except TimeoutError as exc:
            raise SMSTransportTimeoutError(
                "SMS transport timed out.",
                component="sms_service",
                operation="deliver",
                context={"event_type": event_type.value},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise SMSTransportUnavailableError(
                "SMS transport is unavailable.",
                component="sms_service",
                operation="deliver",
                context={"event_type": event_type.value},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise SMSTransportError(
                "SMS transport failed unexpectedly.",
                component="sms_service",
                operation="deliver",
                context={"event_type": event_type.value, "lower_error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        if not isinstance(receipt, SMSDeliveryReceipt):
            raise SMSTransportError(
                "SMS transport returned an invalid delivery receipt.",
                component="sms_service",
                operation="deliver",
                field="receipt",
                context={"received_type": type(receipt).__name__},
            )

        logger.info(
            {
                "event": "sms_delivery_accepted",
                "event_type": event_type.value,
                "recipient": mask_phone_number(recipient.phone_e164),
                "provider": receipt.provider,
                "message_id": receipt.message_id,
                "segments": content.segment_count,
                "encoding": content.encoding,
            }
        )
        return receipt

    def send_verification_sms(
        self,
        recipient: SMSRecipient | str,
        data: SMSVerificationData,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SMSDeliveryReceipt:
        printer.status("SMS", "Sending signup verification SMS", "info")
        if not isinstance(data, SMSVerificationData):
            raise SMSValidationError(
                "data must be SMSVerificationData.",
                component="sms_service",
                operation="send_verification_sms",
                field="data",
            )
        return self._deliver(
            recipient=self._recipient(recipient),
            event_type=SMSNotificationType.SMS_VERIFICATION,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_security_alert_sms(
        self,
        recipient: SMSRecipient | str,
        data: SecurityAlertSMSData,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SMSDeliveryReceipt:
        printer.status("SMS", "Sending security alert SMS", "info")
        if not isinstance(data, SecurityAlertSMSData):
            raise SMSValidationError(
                "data must be SecurityAlertSMSData.",
                component="sms_service",
                operation="send_security_alert_sms",
                field="data",
            )
        return self._deliver(
            recipient=self._recipient(recipient),
            event_type=SMSNotificationType.SECURITY_ALERT,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_audit_result_sms(
        self,
        recipient: SMSRecipient | str,
        data: AuditSMSData,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SMSDeliveryReceipt:
        printer.status("SMS", "Sending audit result SMS", "info")
        if not isinstance(data, AuditSMSData):
            raise SMSValidationError(
                "data must be AuditSMSData.",
                component="sms_service",
                operation="send_audit_result_sms",
                field="data",
            )
        event = SMSNotificationType.AUDIT_COMPLETED if data.successful else SMSNotificationType.AUDIT_FAILED
        return self._deliver(
            recipient=self._recipient(recipient),
            event_type=event,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_purchase_result_sms(
        self,
        recipient: SMSRecipient | str,
        data: PurchaseSMSData,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SMSDeliveryReceipt:
        printer.status("SMS", "Sending purchase result SMS", "info")
        if not isinstance(data, PurchaseSMSData):
            raise SMSValidationError(
                "data must be PurchaseSMSData.",
                component="sms_service",
                operation="send_purchase_result_sms",
                field="data",
            )
        event = SMSNotificationType.PURCHASE_COMPLETED if data.successful else SMSNotificationType.PURCHASE_FAILED
        return self._deliver(
            recipient=self._recipient(recipient),
            event_type=event,
            data=data,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    def send_service_result_sms(
        self,
        recipient: SMSRecipient | str,
        data: ServiceSMSData,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> SMSDeliveryReceipt:
        printer.status("SMS", "Sending service result SMS", "info")
        if not isinstance(data, ServiceSMSData):
            raise SMSValidationError(
                "data must be ServiceSMSData.",
                component="sms_service",
                operation="send_service_result_sms",
                field="data",
            )
        event = SMSNotificationType.SERVICE_COMPLETED if data.successful else SMSNotificationType.SERVICE_FAILED
        return self._deliver(
            recipient=self._recipient(recipient),
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
        max_length: int = 512,
    ) -> str | None:
        value = metadata.get(key)
        if value is None:
            if required:
                raise SMSValidationError(
                    "Notification metadata is missing a required field.",
                    component="sms_service",
                    operation="notification_to_sms",
                    field=key,
                )
            return None
        return require_text(
            value,
            field=key,
            max_length=max_length,
            allow_newlines=False,
        )

    def _event_from_message(self, message: NotificationMessage) -> SMSNotificationType:
        normalized = require_text(
            message.event_type,
            field="event_type",
            max_length=128,
            allow_newlines=False,
        ).casefold()
        event = _PORT_EVENT_MAP.get(normalized)
        if event is None:
            raise SMSUnsupportedEventError(
                "Notification event has no BIMAP SMS template.",
                component="sms_service",
                operation="notification_to_sms",
                field="event_type",
                context={"event_type": normalized},
            )
        return event

    def _send(self, message: NotificationMessage) -> None:
        printer.status("SMS", "Adapting BIMAP notification to SMS", "info")
        event = self._event_from_message(message)
        metadata = dict(message.metadata)
        user_name = self._metadata_text(metadata, "recipient_name", required=False, max_length=128)
        correlation_id = self._metadata_text(metadata, "correlation_id", required=False, max_length=128)
        action_url = self._metadata_text(metadata, "action_url", required=False, max_length=256)
        recipient = SMSRecipient(message.target_ref)

        if event in {SMSNotificationType.AUDIT_COMPLETED, SMSNotificationType.AUDIT_FAILED}:
            audit_id = self._metadata_text(metadata, "audit_id", required=False, max_length=128)
            data = AuditSMSData(
                user_name=user_name,
                audit_name=self._metadata_text(metadata, "audit_name", max_length=192) or "",
                audit_id=audit_id or message.report_id or message.order_id,
                action_url=action_url,
                successful=event is SMSNotificationType.AUDIT_COMPLETED,
            )
            self._deliver(
                recipient=recipient,
                event_type=event,
                data=data,
                idempotency_key=message.idempotency_key,
                correlation_id=correlation_id,
            )
            return

        if event in {SMSNotificationType.PURCHASE_COMPLETED, SMSNotificationType.PURCHASE_FAILED}:
            data = PurchaseSMSData(
                user_name=user_name,
                item_name=self._metadata_text(metadata, "item_name", max_length=192) or "",
                order_id=message.order_id,
                amount_display=self._metadata_text(metadata, "amount_display", max_length=64) or "",
                status_display=self._metadata_text(metadata, "status_display", max_length=64) or "",
                action_url=action_url,
                successful=event is SMSNotificationType.PURCHASE_COMPLETED,
            )
            self._deliver(
                recipient=recipient,
                event_type=event,
                data=data,
                idempotency_key=message.idempotency_key,
                correlation_id=correlation_id,
            )
            return

        if event in {SMSNotificationType.SERVICE_COMPLETED, SMSNotificationType.SERVICE_FAILED}:
            service_id = self._metadata_text(metadata, "service_id", required=False, max_length=128)
            data = ServiceSMSData(
                user_name=user_name,
                service_name=self._metadata_text(metadata, "service_name", max_length=192) or "",
                service_id=service_id or message.order_id,
                status_display=self._metadata_text(metadata, "status_display", max_length=64) or "",
                action_url=action_url,
                successful=event is SMSNotificationType.SERVICE_COMPLETED,
            )
            self._deliver(
                recipient=recipient,
                event_type=event,
                data=data,
                idempotency_key=message.idempotency_key,
                correlation_id=correlation_id,
            )
            return

        raise SMSUnsupportedEventError(
            "Notification event cannot be delivered through the Notifications port.",
            component="sms_service",
            operation="notification_to_sms",
            field="event_type",
            context={"event_type": event.value},
        )


__all__ = ["SMSService"]
