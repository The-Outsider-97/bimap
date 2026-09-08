"""Deterministic renderer for BIMAP transactional email content."""

from __future__ import annotations

from typing import Any

from .email_models import *
from .email_templates import *
from .utils.email_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Email Renderer")
printer = PrettyPrinter()


class EmailRenderer:
    def __init__(self, branding: EmailBranding | None = None) -> None:
        printer.status("EMAIL", "Initializing email renderer", "info")
        self.branding = branding or EmailBranding()
        if not isinstance(self.branding, EmailBranding):
            raise EmailValidationError(
                "branding must be EmailBranding.",
                component="email_renderer",
                operation="initialize",
                field="branding",
            )
        logger.debug({"event": "email_renderer_initialized"})

    def render(self, event_type: EmailNotificationType, data: Any) -> EmailContent:
        printer.status("EMAIL", "Rendering transactional email", "info")
        try:
            event = event_type if isinstance(event_type, EmailNotificationType) else EmailNotificationType(str(event_type))
        except ValueError as exc:
            raise EmailUnsupportedEventError(
                "Unsupported email notification type.",
                component="email_renderer",
                operation="render",
                field="event_type",
                cause=exc,
            ) from exc

        try:
            if event is EmailNotificationType.EMAIL_VERIFICATION:
                if not isinstance(data, EmailVerificationData):
                    raise EmailValidationError(
                        "Email verification requires EmailVerificationData.",
                        component="email_renderer",
                        operation="render",
                        field="data",
                    )
                content = build_verification_email(data, self.branding)
            elif event in {EmailNotificationType.AUDIT_COMPLETED, EmailNotificationType.AUDIT_FAILED}:
                if not isinstance(data, AuditResultData):
                    raise EmailValidationError(
                        "Audit email requires AuditResultData.",
                        component="email_renderer",
                        operation="render",
                        field="data",
                    )
                expected_success = event is EmailNotificationType.AUDIT_COMPLETED
                if data.successful is not expected_success:
                    raise EmailValidationError(
                        "Audit event type conflicts with audit result status.",
                        component="email_renderer",
                        operation="render",
                        field="data.successful",
                    )
                content = build_audit_result_email(data, self.branding)
            elif event in {EmailNotificationType.PURCHASE_COMPLETED, EmailNotificationType.PURCHASE_FAILED}:
                if not isinstance(data, PurchaseResultData):
                    raise EmailValidationError(
                        "Purchase email requires PurchaseResultData.",
                        component="email_renderer",
                        operation="render",
                        field="data",
                    )
                expected_success = event is EmailNotificationType.PURCHASE_COMPLETED
                if data.successful is not expected_success:
                    raise EmailValidationError(
                        "Purchase event type conflicts with purchase result status.",
                        component="email_renderer",
                        operation="render",
                        field="data.successful",
                    )
                content = build_purchase_result_email(data, self.branding)
            elif event in {EmailNotificationType.SERVICE_COMPLETED, EmailNotificationType.SERVICE_FAILED}:
                if not isinstance(data, ServiceResultData):
                    raise EmailValidationError(
                        "Service email requires ServiceResultData.",
                        component="email_renderer",
                        operation="render",
                        field="data",
                    )
                expected_success = event is EmailNotificationType.SERVICE_COMPLETED
                if data.successful is not expected_success:
                    raise EmailValidationError(
                        "Service event type conflicts with service result status.",
                        component="email_renderer",
                        operation="render",
                        field="data.successful",
                    )
                content = build_service_result_email(data, self.branding)
            else:
                raise EmailUnsupportedEventError(
                    "Unsupported email notification type.",
                    component="email_renderer",
                    operation="render",
                    field="event_type",
                    context={"event_type": event.value},
                )
        except (EmailValidationError, EmailUnsupportedEventError):
            raise
        except Exception as exc:
            raise EmailRenderError(
                "Transactional email rendering failed.",
                component="email_renderer",
                operation="render",
                context={"event_type": event.value, "lower_error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        logger.info({"event": "email_render_completed", "event_type": event.value})
        return content


__all__ = ["EmailRenderer"]
