"""Deterministic renderer for BIMAP transactional SMS content."""

from __future__ import annotations

from typing import Any

from .sms_models import *
from .sms_templates import *
from .utils.sms_errors import *
from .utils.sms_helpers import ensure_sms_segment_limit
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SMS Renderer")
printer = PrettyPrinter()


class SMSRenderer:
    def __init__(self, branding: SMSBranding | None = None) -> None:
        printer.status("SMS", "Initializing SMS renderer", "info")
        self.branding = branding or SMSBranding()
        if not isinstance(self.branding, SMSBranding):
            raise SMSValidationError(
                "branding must be SMSBranding.",
                component="sms_renderer",
                operation="initialize",
                field="branding",
            )
        logger.debug({"event": "sms_renderer_initialized", "max_segments": self.branding.max_segments})

    def render(self, event_type: SMSNotificationType, data: Any) -> SMSContent:
        printer.status("SMS", "Rendering transactional SMS", "info")
        try:
            event = (
                event_type
                if isinstance(event_type, SMSNotificationType)
                else SMSNotificationType(str(event_type))
            )
        except ValueError as exc:
            raise SMSUnsupportedEventError(
                "Unsupported SMS notification type.",
                component="sms_renderer",
                operation="render",
                field="event_type",
                cause=exc,
            ) from exc

        try:
            if event is SMSNotificationType.SMS_VERIFICATION:
                if not isinstance(data, SMSVerificationData):
                    raise SMSValidationError(
                        "SMS verification requires SMSVerificationData.",
                        component="sms_renderer",
                        operation="render",
                        field="data",
                    )
                content = build_verification_sms(data, self.branding)
            elif event is SMSNotificationType.SECURITY_ALERT:
                if not isinstance(data, SecurityAlertSMSData):
                    raise SMSValidationError(
                        "Security alert SMS requires SecurityAlertSMSData.",
                        component="sms_renderer",
                        operation="render",
                        field="data",
                    )
                content = build_security_alert_sms(data, self.branding)
            elif event in {SMSNotificationType.AUDIT_COMPLETED, SMSNotificationType.AUDIT_FAILED}:
                if not isinstance(data, AuditSMSData):
                    raise SMSValidationError(
                        "Audit SMS requires AuditSMSData.",
                        component="sms_renderer",
                        operation="render",
                        field="data",
                    )
                expected_success = event is SMSNotificationType.AUDIT_COMPLETED
                if data.successful is not expected_success:
                    raise SMSValidationError(
                        "Audit event type conflicts with audit result status.",
                        component="sms_renderer",
                        operation="render",
                        field="data.successful",
                    )
                content = build_audit_result_sms(data, self.branding)
            elif event in {SMSNotificationType.PURCHASE_COMPLETED, SMSNotificationType.PURCHASE_FAILED}:
                if not isinstance(data, PurchaseSMSData):
                    raise SMSValidationError(
                        "Purchase SMS requires PurchaseSMSData.",
                        component="sms_renderer",
                        operation="render",
                        field="data",
                    )
                expected_success = event is SMSNotificationType.PURCHASE_COMPLETED
                if data.successful is not expected_success:
                    raise SMSValidationError(
                        "Purchase event type conflicts with purchase result status.",
                        component="sms_renderer",
                        operation="render",
                        field="data.successful",
                    )
                content = build_purchase_result_sms(data, self.branding)
            elif event in {SMSNotificationType.SERVICE_COMPLETED, SMSNotificationType.SERVICE_FAILED}:
                if not isinstance(data, ServiceSMSData):
                    raise SMSValidationError(
                        "Service SMS requires ServiceSMSData.",
                        component="sms_renderer",
                        operation="render",
                        field="data",
                    )
                expected_success = event is SMSNotificationType.SERVICE_COMPLETED
                if data.successful is not expected_success:
                    raise SMSValidationError(
                        "Service event type conflicts with service result status.",
                        component="sms_renderer",
                        operation="render",
                        field="data.successful",
                    )
                content = build_service_result_sms(data, self.branding)
            else:
                raise SMSUnsupportedEventError(
                    "Unsupported SMS notification type.",
                    component="sms_renderer",
                    operation="render",
                    field="event_type",
                )
        except SMSError:
            raise
        except Exception as exc:
            raise SMSRenderError(
                "SMS template rendering failed unexpectedly.",
                component="sms_renderer",
                operation="render",
                context={"event_type": event.value, "lower_error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        if not isinstance(content, SMSContent):
            raise SMSRenderError(
                "SMS template returned an invalid content object.",
                component="sms_renderer",
                operation="render",
                field="content",
                context={"event_type": event.value},
            )

        length = ensure_sms_segment_limit(content.body, max_segments=self.branding.max_segments)
        logger.debug(
            {
                "event": "sms_render_completed",
                "event_type": event.value,
                "encoding": length.encoding,
                "units": length.units,
                "segments": length.segments,
            }
        )
        return content


__all__ = ["SMSRenderer"]
