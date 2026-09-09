"""Structured BIMAP SMS failure vocabulary."""

from __future__ import annotations

from ...app.utils.app_errors import AppError


class SMSError(AppError):
    code = "BIMAP.SMS.ERROR"


class SMSConfigurationError(SMSError):
    code = "BIMAP.SMS.CONFIGURATION"


class SMSValidationError(SMSError):
    code = "BIMAP.SMS.VALIDATION"


class SMSTemplateError(SMSError):
    code = "BIMAP.SMS.TEMPLATE"


class SMSRenderError(SMSError):
    code = "BIMAP.SMS.RENDER"


class SMSUnsupportedEventError(SMSError):
    code = "BIMAP.SMS.EVENT.UNSUPPORTED"


class SMSTransportError(SMSError):
    code = "BIMAP.SMS.TRANSPORT"


class SMSTransportUnavailableError(SMSTransportError):
    code = "BIMAP.SMS.TRANSPORT.UNAVAILABLE"
    retryable = True


class SMSTransportTimeoutError(SMSTransportError):
    code = "BIMAP.SMS.TRANSPORT.TIMEOUT"
    retryable = True


class SMSProviderError(SMSTransportError):
    code = "BIMAP.SMS.PROVIDER"


class SMSProviderAuthenticationError(SMSProviderError):
    code = "BIMAP.SMS.PROVIDER.AUTHENTICATION"


class SMSProviderRecipientRejectedError(SMSProviderError):
    code = "BIMAP.SMS.PROVIDER.RECIPIENT_REJECTED"


class SMSProviderRateLimitError(SMSProviderError):
    code = "BIMAP.SMS.PROVIDER.RATE_LIMIT"
    retryable = True


class SMSProviderTemporaryFailureError(SMSProviderError):
    code = "BIMAP.SMS.PROVIDER.TEMPORARY_FAILURE"
    retryable = True


class SMSProviderPermanentFailureError(SMSProviderError):
    code = "BIMAP.SMS.PROVIDER.PERMANENT_FAILURE"


__all__ = [
    "SMSError",
    "SMSConfigurationError",
    "SMSValidationError",
    "SMSTemplateError",
    "SMSRenderError",
    "SMSUnsupportedEventError",
    "SMSTransportError",
    "SMSTransportUnavailableError",
    "SMSTransportTimeoutError",
    "SMSProviderError",
    "SMSProviderAuthenticationError",
    "SMSProviderRecipientRejectedError",
    "SMSProviderRateLimitError",
    "SMSProviderTemporaryFailureError",
    "SMSProviderPermanentFailureError",
]
