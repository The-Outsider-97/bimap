"""Canonical BIMAP transactional email templates."""

from __future__ import annotations

from html import escape

from .email_models import *


def _greeting(name: str | None) -> str:
    return f"Hi {name}," if name else "Hi,"


def _html_layout(*, title: str, body: str, branding: EmailBranding) -> str:
    product = escape(branding.product_name, quote=True)
    team = escape(branding.team_name, quote=True)
    support = ""
    if branding.support_email:
        support_email = escape(branding.support_email, quote=True)
        support = (
            f'<p style="margin:24px 0 0;color:#667085;font-size:13px;line-height:1.5;">'
            f'Need help? Contact <a href="mailto:{support_email}" style="color:#344054;">{support_email}</a>.</p>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title, quote=True)}</title>
</head>
<body style="margin:0;padding:0;background:#f6f7f9;font-family:Arial,Helvetica,sans-serif;color:#1d2939;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f7f9;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:640px;background:#ffffff;border:1px solid #eaecf0;border-radius:12px;overflow:hidden;">
<tr><td style="padding:24px 28px;background:#101828;color:#ffffff;font-size:20px;font-weight:700;">{product}</td></tr>
<tr><td style="padding:32px 28px;font-size:15px;line-height:1.65;">{body}{support}</td></tr>
<tr><td style="padding:20px 28px;background:#f9fafb;color:#667085;font-size:12px;line-height:1.5;">Best regards,<br><strong style="color:#475467;">{team}</strong></td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _button(label: str, url: str | None) -> str:
    if not url:
        return ""
    return (
        '<p style="margin:28px 0 8px;">'
        f'<a href="{escape(url, quote=True)}" '
        'style="display:inline-block;padding:12px 18px;background:#101828;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:700;">'
        f'{escape(label, quote=True)}</a></p>'
    )


def _detail_rows(rows: tuple[tuple[str, str], ...]) -> str:
    rendered = []
    for label, value in rows:
        rendered.append(
            "<tr>"
            f'<td style="padding:8px 12px;color:#667085;border-bottom:1px solid #eaecf0;width:34%;">{escape(label, quote=True)}</td>'
            f'<td style="padding:8px 12px;color:#1d2939;border-bottom:1px solid #eaecf0;font-weight:600;">{escape(value, quote=True)}</td>'
            "</tr>"
        )
    return '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:20px 0;border:1px solid #eaecf0;border-radius:8px;border-collapse:separate;border-spacing:0;overflow:hidden;">' + "".join(rendered) + "</table>"


def build_verification_email(data: EmailVerificationData, branding: EmailBranding) -> EmailContent:
    subject = f"Verify your {branding.product_name} email address"
    greeting = _greeting(data.user_name)
    text = (
        f"{greeting}\n\n"
        f"Thanks for signing up for {branding.product_name}! We are excited to have you on board.\n\n"
        "To start using your account, please confirm your email address by copying the verification code below:\n\n"
        f"{data.verification_code}\n\n"
        f"Important: This verification code will expire in {data.expires_in_minutes} minutes. "
        "If you did not create an account with us, you can safely ignore this email.\n\n"
        f"Best regards,\n{branding.team_name}"
    )
    body = (
        f'<p style="margin:0 0 16px;"><strong>{escape(greeting, quote=True)}</strong></p>'
        f'<p style="margin:0 0 16px;">Thanks for signing up for <strong>{escape(branding.product_name, quote=True)}</strong>! We are excited to have you on board.</p>'
        '<p style="margin:0 0 18px;">To start using your account, please confirm your email address by copying the verification code below:</p>'
        f'<div style="margin:20px 0;padding:18px;text-align:center;background:#f2f4f7;border:1px solid #d0d5dd;border-radius:8px;font-family:Consolas,Monaco,monospace;font-size:22px;font-weight:700;letter-spacing:2px;word-break:break-all;">{escape(data.verification_code, quote=True)}</div>'
        f'<p style="margin:18px 0 0;"><strong>Important:</strong> This verification code will expire in <strong>{data.expires_in_minutes} minutes</strong>. If you did not create an account with us, you can safely ignore this email.</p>'
    )
    return EmailContent(subject=subject, text_body=text, html_body=_html_layout(title=subject, body=body, branding=branding))


def build_audit_result_email(data: AuditResultData, branding: EmailBranding) -> EmailContent:
    subject = (
        f"Your {branding.product_name} audit is complete"
        if data.successful
        else f"Your {branding.product_name} audit could not be completed"
    )
    greeting = _greeting(data.user_name)
    outcome = "completed successfully" if data.successful else "could not be completed successfully"
    text = (
        f"{greeting}\n\n"
        f"Your {branding.product_name} audit has {outcome}.\n\n"
        f"Audit: {data.audit_name}\n"
        f"Audit ID: {data.audit_id}\n"
        f"Completed: {data.completed_at}\n"
        f"Result: {data.result_summary}\n"
        + (f"\nView audit results: {data.action_url}\n" if data.action_url else "")
        + f"\nBest regards,\n{branding.team_name}"
    )
    body = (
        f'<p style="margin:0 0 16px;"><strong>{escape(greeting, quote=True)}</strong></p>'
        f'<p style="margin:0 0 16px;">Your <strong>{escape(branding.product_name, quote=True)} audit</strong> has {escape(outcome, quote=True)}.</p>'
        + _detail_rows((
            ("Audit", data.audit_name),
            ("Audit ID", data.audit_id),
            ("Completed", data.completed_at),
            ("Result", data.result_summary),
        ))
        + _button("View Audit Results", data.action_url)
    )
    return EmailContent(subject=subject, text_body=text, html_body=_html_layout(title=subject, body=body, branding=branding))


def build_purchase_result_email(data: PurchaseResultData, branding: EmailBranding) -> EmailContent:
    subject = (
        f"Your {branding.product_name} purchase confirmation"
        if data.successful
        else f"Your {branding.product_name} purchase was not completed"
    )
    greeting = _greeting(data.user_name)
    outcome = "completed successfully" if data.successful else "not completed successfully"
    text = (
        f"{greeting}\n\n"
        f"Your {branding.product_name} purchase was {outcome}.\n\n"
        f"Order: {data.item_name}\n"
        f"Order ID: {data.order_id}\n"
        f"Date: {data.occurred_at}\n"
        f"Amount: {data.amount_display}\n"
        f"Status: {data.status_display}\n"
        + (f"\nView purchase: {data.action_url}\n" if data.action_url else "")
        + f"\nBest regards,\n{branding.team_name}"
    )
    body = (
        f'<p style="margin:0 0 16px;"><strong>{escape(greeting, quote=True)}</strong></p>'
        f'<p style="margin:0 0 16px;">Your <strong>{escape(branding.product_name, quote=True)} purchase</strong> was {escape(outcome, quote=True)}.</p>'
        + _detail_rows((
            ("Order", data.item_name),
            ("Order ID", data.order_id),
            ("Date", data.occurred_at),
            ("Amount", data.amount_display),
            ("Status", data.status_display),
        ))
        + _button("View Purchase", data.action_url)
    )
    return EmailContent(subject=subject, text_body=text, html_body=_html_layout(title=subject, body=body, branding=branding))


def build_service_result_email(data: ServiceResultData, branding: EmailBranding) -> EmailContent:
    subject = (
        f"Your {branding.product_name} service is complete"
        if data.successful
        else f"Your {branding.product_name} service could not be completed"
    )
    greeting = _greeting(data.user_name)
    outcome = "completed successfully" if data.successful else "could not be completed successfully"
    text = (
        f"{greeting}\n\n"
        f"Your {branding.product_name} service request has {outcome}.\n\n"
        f"Service: {data.service_name}\n"
        f"Service ID: {data.service_id}\n"
        f"Completed: {data.completed_at}\n"
        f"Status: {data.status_display}\n"
        f"Result: {data.result_summary}\n"
        + (f"\nView service result: {data.action_url}\n" if data.action_url else "")
        + f"\nBest regards,\n{branding.team_name}"
    )
    body = (
        f'<p style="margin:0 0 16px;"><strong>{escape(greeting, quote=True)}</strong></p>'
        f'<p style="margin:0 0 16px;">Your <strong>{escape(branding.product_name, quote=True)} service request</strong> has {escape(outcome, quote=True)}.</p>'
        + _detail_rows((
            ("Service", data.service_name),
            ("Service ID", data.service_id),
            ("Completed", data.completed_at),
            ("Status", data.status_display),
            ("Result", data.result_summary),
        ))
        + _button("View Service Result", data.action_url)
    )
    return EmailContent(subject=subject, text_body=text, html_body=_html_layout(title=subject, body=body, branding=branding))


__all__ = [
    "build_verification_email",
    "build_audit_result_email",
    "build_purchase_result_email",
    "build_service_result_email",
]
