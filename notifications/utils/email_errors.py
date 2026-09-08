"""Structured BIMAP email and SMTP failure vocabulary."""

from __future__ import annotations

from ...app.utils.app_errors import AppError


class EmailError(AppError):
    code = "BIMAP.EMAIL.ERROR"


class EmailConfigurationError(EmailError):
    code = "BIMAP.EMAIL.CONFIGURATION"


class EmailValidationError(EmailError):
    code = "BIMAP.EMAIL.VALIDATION"


class EmailTemplateError(EmailError):
    code = "BIMAP.EMAIL.TEMPLATE"


class EmailRenderError(EmailError):
    code = "BIMAP.EMAIL.RENDER"


class EmailUnsupportedEventError(EmailError):
    code = "BIMAP.EMAIL.EVENT.UNSUPPORTED"


class EmailTransportError(EmailError):
    code = "BIMAP.EMAIL.TRANSPORT"


class EmailTransportUnavailableError(EmailTransportError):
    code = "BIMAP.EMAIL.TRANSPORT.UNAVAILABLE"
    retryable = True


class EmailTransportTimeoutError(EmailTransportError):
    code = "BIMAP.EMAIL.TRANSPORT.TIMEOUT"
    retryable = True


class SMTPError(EmailTransportError):
    code = "BIMAP.EMAIL.SMTP"


class SMTPConnectionError(SMTPError):
    code = "BIMAP.EMAIL.SMTP.CONNECTION"
    retryable = True


class SMTPAuthenticationError(SMTPError):
    code = "BIMAP.EMAIL.SMTP.AUTHENTICATION"


class SMTPRecipientRejectedError(SMTPError):
    code = "BIMAP.EMAIL.SMTP.RECIPIENT_REJECTED"


class SMTPSenderRejectedError(SMTPError):
    code = "BIMAP.EMAIL.SMTP.SENDER_REJECTED"


class SMTPDataRejectedError(SMTPError):
    code = "BIMAP.EMAIL.SMTP.DATA_REJECTED"


class SMTPTemporaryFailureError(SMTPError):
    code = "BIMAP.EMAIL.SMTP.TEMPORARY_FAILURE"
    retryable = True


__all__ = [
    "EmailError",
    "EmailConfigurationError",
    "EmailValidationError",
    "EmailTemplateError",
    "EmailRenderError",
    "EmailUnsupportedEventError",
    "EmailTransportError",
    "EmailTransportUnavailableError",
    "EmailTransportTimeoutError",
    "SMTPError",
    "SMTPConnectionError",
    "SMTPAuthenticationError",
    "SMTPRecipientRejectedError",
    "SMTPSenderRejectedError",
    "SMTPDataRejectedError",
    "SMTPTemporaryFailureError",
]
