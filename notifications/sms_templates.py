"""Canonical BIMAP transactional SMS templates."""

from __future__ import annotations

from .sms_models import *
from .utils.sms_helpers import compact_sms_text


def _signature(branding: SMSBranding) -> str:
    return compact_sms_text(branding.team_name, field="team_name", max_length=32)


def _append_url(message: str, url: str | None, *, label: str) -> str:
    if not url:
        return message
    return f"{message} {label}: {url}"


def build_verification_sms(data: SMSVerificationData, branding: SMSBranding) -> SMSContent:
    body = (
        f"{branding.product_name} verification code: {data.verification_code}\n"
        f"Expires in {data.expires_in_minutes} minutes. Do not share this code.\n"
        f"{_signature(branding)}"
    )
    return SMSContent(body=body)


def build_security_alert_sms(data: SecurityAlertSMSData, branding: SMSBranding) -> SMSContent:
    message = (
        f"{branding.product_name} security alert: {data.alert_name}. "
        f"Time: {data.occurred_at}."
    )
    message = _append_url(message, data.action_url, label="Review account")
    return SMSContent(body=f"{message}\n{_signature(branding)}")


def build_audit_result_sms(data: AuditSMSData, branding: SMSBranding) -> SMSContent:
    outcome = "completed" if data.successful else "could not be completed"
    message = (
        f"{branding.product_name}: Audit {data.audit_name} ({data.audit_id}) {outcome}."
    )
    message = _append_url(
        message,
        data.action_url,
        label="View results" if data.successful else "Review status",
    )
    return SMSContent(body=f"{message}\n{_signature(branding)}")


def build_purchase_result_sms(data: PurchaseSMSData, branding: SMSBranding) -> SMSContent:
    if data.successful:
        message = (
            f"{branding.product_name}: Purchase confirmed for {data.item_name}, "
            f"{data.amount_display}. Order {data.order_id}. Status: {data.status_display}."
        )
        label = "View purchase"
    else:
        message = (
            f"{branding.product_name}: Purchase for {data.item_name} was not completed. "
            f"Order {data.order_id}. Status: {data.status_display}."
        )
        label = "Review purchase"
    message = _append_url(message, data.action_url, label=label)
    return SMSContent(body=f"{message}\n{_signature(branding)}")


def build_service_result_sms(data: ServiceSMSData, branding: SMSBranding) -> SMSContent:
    outcome = "completed" if data.successful else "could not be completed"
    message = (
        f"{branding.product_name}: Service {data.service_name} ({data.service_id}) {outcome}. "
        f"Status: {data.status_display}."
    )
    message = _append_url(
        message,
        data.action_url,
        label="View result" if data.successful else "Review status",
    )
    return SMSContent(body=f"{message}\n{_signature(branding)}")


__all__ = [
    "build_verification_sms",
    "build_security_alert_sms",
    "build_audit_result_sms",
    "build_purchase_result_sms",
    "build_service_result_sms",
]
