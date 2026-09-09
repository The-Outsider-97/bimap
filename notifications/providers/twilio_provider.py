"""Twilio transport adapter for BIMAP transactional SMS notifications."""

from __future__ import annotations

import os
import re

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout
from twilio.base.exceptions import (
    TwilioException,
    TwilioRestException,
    TwilioServiceException,
)
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from .sms_provider import SMSProvider
from ..sms_models import (
    OutboundSMS,
    SMSDeliveryReceipt,
    SMSDeliveryStatus,
)
from ..utils.sms_errors import (
    SMSError,
    SMSConfigurationError,
    SMSProviderAuthenticationError,
    SMSProviderError,
    SMSProviderPermanentFailureError,
    SMSProviderRateLimitError,
    SMSProviderRecipientRejectedError,
    SMSProviderTemporaryFailureError,
    SMSTransportTimeoutError,
    SMSTransportUnavailableError,
)
from ..utils.sms_helpers import (
    mask_phone_number,
    normalize_e164_number,
    optional_text,
    require_text,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Twilio Provider")
printer = PrettyPrinter()

_ACCOUNT_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
_API_KEY_SID_RE = re.compile(r"^SK[0-9a-fA-F]{32}$")
_MESSAGING_SERVICE_SID_RE = re.compile(r"^MG[0-9a-fA-F]{32}$")
_ALPHANUMERIC_SENDER_RE = re.compile(r"^[A-Za-z0-9]{1,11}$")

_AUTH_ERROR_CODES = frozenset({20003, 20403})
_RATE_LIMIT_ERROR_CODES = frozenset({20429})
_RECIPIENT_ERROR_CODES = frozenset(
    {
        21211,
        21610,
        21614,
        30003,
        30005,
        30006,
        30007,
        30008,
        30034,
        30036,
        30037,
        60006,
        60703,
        63107,
    }
)
_SENDER_OR_CONFIGURATION_ERROR_CODES = frozenset(
    {
        21212,
        21603,
        21606,
        21607,
        21608,
        21612,
        21613,
        21615,
        21616,
        21617,
        21618,
        21619,
        21620,
        21621,
        21622,
        21623,
        21624,
        21625,
        21626,
        21628,
        21629,
        21630,
        21631,
        21632,
        21633,
        21634,
        21635,
        21704,
        30124,
        30127,
    }
)
_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _require_sid(
    value: Any,
    *,
    field_name: str,
    pattern: re.Pattern[str],
) -> str:
    normalized = require_text(
        value,
        field=field_name,
        max_length=34,
        allow_newlines=False,
    )
    if not pattern.fullmatch(normalized):
        raise SMSConfigurationError(
            f"{field_name} has an invalid Twilio SID format.",
            component="twilio_provider",
            operation="validate_settings",
            field=field_name,
        )
    return normalized


def _normalize_secret(
    value: Any,
    *,
    field_name: str,
    required: bool,
) -> str | None:
    if value is None:
        if required:
            raise SMSConfigurationError(
                f"{field_name} is required.",
                component="twilio_provider",
                operation="validate_settings",
                field=field_name,
            )
        return None
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise SMSConfigurationError(
            f"{field_name} must be non-empty text within the allowed length.",
            component="twilio_provider",
            operation="validate_settings",
            field=field_name,
        )
    return value


def _normalize_sender(value: Any) -> str | None:
    sender = optional_text(
        value,
        field="from_sender",
        max_length=16,
        allow_newlines=False,
    )
    if sender is None:
        return None

    if sender.startswith("+"):
        return normalize_e164_number(sender, field="from_sender")

    if not _ALPHANUMERIC_SENDER_RE.fullmatch(sender):
        raise SMSConfigurationError(
            "from_sender must be an E.164 Twilio number or an alphanumeric sender ID of 1-11 letters/digits.",
            component="twilio_provider",
            operation="validate_settings",
            field="from_sender",
        )
    if sender.isdigit():
        raise SMSConfigurationError(
            "Alphanumeric sender ID must contain at least one letter.",
            component="twilio_provider",
            operation="validate_settings",
            field="from_sender",
        )
    return sender


def _normalize_callback_url(value: Any) -> str | None:
    callback = optional_text(
        value,
        field="status_callback",
        max_length=2048,
        allow_newlines=False,
    )
    if callback is None:
        return None

    parsed = urlsplit(callback)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SMSConfigurationError(
            "status_callback must be an absolute HTTP or HTTPS URL.",
            component="twilio_provider",
            operation="validate_settings",
            field="status_callback",
        )
    if parsed.username is not None or parsed.password is not None:
        raise SMSConfigurationError(
            "status_callback must not contain embedded credentials.",
            component="twilio_provider",
            operation="validate_settings",
            field="status_callback",
        )
    return callback


def _exception_status(exc: BaseException) -> int | None:
    value = getattr(exc, "status", None)
    return value if isinstance(value, int) else None


def _exception_code(exc: BaseException) -> int | None:
    value = getattr(exc, "code", None)
    return value if isinstance(value, int) else None


@dataclass(frozen=True, slots=True)
class TwilioSettings:
    account_sid: str
    auth_token: str | None = field(default=None, repr=False)
    api_key_sid: str | None = field(default=None, repr=False)
    api_key_secret: str | None = field(default=None, repr=False)
    messaging_service_sid: str | None = None
    from_sender: str | None = None
    status_callback: str | None = None
    timeout_seconds: float = 15.0
    region: str | None = None
    edge: str | None = None

    def __post_init__(self) -> None:
        printer.status("SMS", "Validating Twilio settings", "info")

        object.__setattr__(
            self,
            "account_sid",
            _require_sid(
                self.account_sid,
                field_name="account_sid",
                pattern=_ACCOUNT_SID_RE,
            ),
        )

        auth_token = _normalize_secret(
            self.auth_token,
            field_name="auth_token",
            required=False,
        )
        api_key_sid = self.api_key_sid
        api_key_secret = _normalize_secret(
            self.api_key_secret,
            field_name="api_key_secret",
            required=False,
        )

        if api_key_sid is not None:
            api_key_sid = _require_sid(
                api_key_sid,
                field_name="api_key_sid",
                pattern=_API_KEY_SID_RE,
            )

        token_auth = auth_token is not None
        key_auth = api_key_sid is not None or api_key_secret is not None

        if token_auth and key_auth:
            raise SMSConfigurationError(
                "Configure either Twilio auth_token or API key credentials, not both.",
                component="twilio_provider",
                operation="validate_settings",
                field="credentials",
            )

        if not token_auth and not key_auth:
            raise SMSConfigurationError(
                "Twilio credentials are not configured.",
                component="twilio_provider",
                operation="validate_settings",
                field="credentials",
            )

        if key_auth and (api_key_sid is None or api_key_secret is None):
            raise SMSConfigurationError(
                "api_key_sid and api_key_secret must be configured together.",
                component="twilio_provider",
                operation="validate_settings",
                field="credentials",
            )

        messaging_service_sid = self.messaging_service_sid
        if messaging_service_sid is not None:
            messaging_service_sid = _require_sid(
                messaging_service_sid,
                field_name="messaging_service_sid",
                pattern=_MESSAGING_SERVICE_SID_RE,
            )

        from_sender = _normalize_sender(self.from_sender)

        if messaging_service_sid is None and from_sender is None:
            raise SMSConfigurationError(
                "Configure messaging_service_sid or from_sender for Twilio SMS.",
                component="twilio_provider",
                operation="validate_settings",
                field="sender",
            )

        timeout = self.timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise SMSConfigurationError(
                "timeout_seconds must be numeric.",
                component="twilio_provider",
                operation="validate_settings",
                field="timeout_seconds",
            )
        timeout = float(timeout)
        if not 1.0 <= timeout <= 120.0:
            raise SMSConfigurationError(
                "timeout_seconds must be between 1 and 120 seconds.",
                component="twilio_provider",
                operation="validate_settings",
                field="timeout_seconds",
            )

        region = optional_text(
            self.region,
            field="region",
            max_length=64,
            allow_newlines=False,
        )
        edge = optional_text(
            self.edge,
            field="edge",
            max_length=64,
            allow_newlines=False,
        )

        object.__setattr__(self, "auth_token", auth_token)
        object.__setattr__(self, "api_key_sid", api_key_sid)
        object.__setattr__(self, "api_key_secret", api_key_secret)
        object.__setattr__(self, "messaging_service_sid", messaging_service_sid)
        object.__setattr__(self, "from_sender", from_sender)
        object.__setattr__(
            self,
            "status_callback",
            _normalize_callback_url(self.status_callback),
        )
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "edge", edge)

    @property
    def uses_api_key(self) -> bool:
        return self.api_key_sid is not None

    @classmethod
    def from_env(cls, prefix: str = "BIMAP_") -> "TwilioSettings":
        printer.status("SMS", "Loading Twilio settings", "info")

        env = os.environ
        account_sid = env.get(f"{prefix}TWILIO_ACCOUNT_SID")
        if account_sid is None:
            raise SMSConfigurationError(
                "Twilio Account SID is not configured.",
                component="twilio_provider",
                operation="from_env",
                field=f"{prefix}TWILIO_ACCOUNT_SID",
            )

        raw_timeout = env.get(f"{prefix}TWILIO_TIMEOUT_SECONDS", "15")
        try:
            timeout_seconds = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise SMSConfigurationError(
                "Twilio timeout environment value must be numeric.",
                component="twilio_provider",
                operation="from_env",
                field=f"{prefix}TWILIO_TIMEOUT_SECONDS",
                cause=exc,
            ) from exc

        return cls(
            account_sid=account_sid,
            auth_token=env.get(f"{prefix}TWILIO_AUTH_TOKEN"),
            api_key_sid=env.get(f"{prefix}TWILIO_API_KEY_SID"),
            api_key_secret=env.get(f"{prefix}TWILIO_API_KEY_SECRET"),
            messaging_service_sid=env.get(f"{prefix}TWILIO_MESSAGING_SERVICE_SID"),
            from_sender=env.get(f"{prefix}TWILIO_FROM"),
            status_callback=env.get(f"{prefix}TWILIO_STATUS_CALLBACK"),
            timeout_seconds=timeout_seconds,
            region=env.get(f"{prefix}TWILIO_REGION"),
            edge=env.get(f"{prefix}TWILIO_EDGE"),
        )


class TwilioSMSProvider(SMSProvider):
    def __init__(
        self,
        settings: TwilioSettings,
        *,
        client: Client | None = None,
    ) -> None:
        printer.status("SMS", "Initializing Twilio SMS provider", "info")

        if not isinstance(settings, TwilioSettings):
            raise SMSConfigurationError(
                "settings must be TwilioSettings.",
                component="twilio_provider",
                operation="initialize",
                field="settings",
            )

        self.settings = settings
        self._client = client or self._build_client(settings)

        super().__init__("twilio")

        logger.info(
            {
                "event": "twilio_sms_provider_initialized",
                "uses_api_key": settings.uses_api_key,
                "uses_messaging_service": settings.messaging_service_sid is not None,
                "uses_explicit_sender": settings.from_sender is not None,
                "status_callback_configured": settings.status_callback is not None,
                "region": settings.region,
                "edge": settings.edge,
            }
        )

    @classmethod
    def from_env(cls, prefix: str = "BIMAP_") -> "TwilioSMSProvider":
        return cls(TwilioSettings.from_env(prefix=prefix))

    @staticmethod
    def _build_client(settings: TwilioSettings) -> Client:
        http_client = TwilioHttpClient(
            timeout=settings.timeout_seconds,
            max_retries=0,
        )

        if settings.uses_api_key:
            assert settings.api_key_sid is not None
            assert settings.api_key_secret is not None
            return Client(
                settings.api_key_sid,
                settings.api_key_secret,
                account_sid=settings.account_sid,
                region=settings.region,
                edge=settings.edge,
                http_client=http_client,
            )

        assert settings.auth_token is not None
        return Client(
            settings.account_sid,
            settings.auth_token,
            region=settings.region,
            edge=settings.edge,
            http_client=http_client,
        )

    def _create_arguments(self, message: OutboundSMS) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "to": message.recipient.phone_e164,
            "body": message.content.body,
        }

        if self.settings.messaging_service_sid is not None:
            arguments["messaging_service_sid"] = self.settings.messaging_service_sid
        if self.settings.from_sender is not None:
            arguments["from_"] = self.settings.from_sender
        if self.settings.status_callback is not None:
            arguments["status_callback"] = self.settings.status_callback

        return arguments

    def _translate_twilio_error(
        self,
        exc: BaseException,
        *,
        message: OutboundSMS,
    ) -> SMSError:
        status = _exception_status(exc)
        code = _exception_code(exc)

        context: dict[str, Any] = {
            "provider": self.provider_name,
            "event_type": message.event_type.value,
            "recipient": mask_phone_number(message.recipient.phone_e164),
            "http_status": status,
            "twilio_code": code,
        }

        if status in {401, 403} or code in _AUTH_ERROR_CODES:
            return SMSProviderAuthenticationError(
                "Twilio authentication or authorization failed.",
                component="twilio_provider",
                operation="send",
                context=context,
                cause=exc,
            )

        if status == 429 or code in _RATE_LIMIT_ERROR_CODES:
            return SMSProviderRateLimitError(
                "Twilio rate limit was exceeded.",
                component="twilio_provider",
                operation="send",
                context=context,
                cause=exc,
            )

        if code in _RECIPIENT_ERROR_CODES:
            return SMSProviderRecipientRejectedError(
                "Twilio rejected the SMS recipient.",
                component="twilio_provider",
                operation="send",
                context=context,
                cause=exc,
            )

        if status in _TRANSIENT_HTTP_STATUSES or (
            status is not None and 500 <= status < 600
        ):
            return SMSProviderTemporaryFailureError(
                "Twilio is temporarily unable to accept the SMS.",
                component="twilio_provider",
                operation="send",
                context=context,
                cause=exc,
            )

        if code in _SENDER_OR_CONFIGURATION_ERROR_CODES:
            return SMSProviderPermanentFailureError(
                "Twilio rejected the configured sender or message request.",
                component="twilio_provider",
                operation="send",
                context=context,
                cause=exc,
            )

        if status is not None and 400 <= status < 500:
            return SMSProviderPermanentFailureError(
                "Twilio permanently rejected the SMS request.",
                component="twilio_provider",
                operation="send",
                context=context,
                cause=exc,
            )

        return SMSProviderError(
            "Twilio SMS request failed.",
            component="twilio_provider",
            operation="send",
            context=context,
            cause=exc,
        )

    def _send(self, message: OutboundSMS) -> SMSDeliveryReceipt:
        printer.status("SMS", "Sending SMS through Twilio", "info")

        arguments = self._create_arguments(message)

        try:
            result = self._client.messages.create(**arguments)
        except (TwilioRestException, TwilioServiceException) as exc:
            translated = self._translate_twilio_error(exc, message=message)
            raise translated from exc
        except RequestsTimeout as exc:
            raise SMSTransportTimeoutError(
                "Twilio request timed out.",
                component="twilio_provider",
                operation="send",
                context={
                    "provider": self.provider_name,
                    "event_type": message.event_type.value,
                },
                cause=exc,
            ) from exc
        except RequestsConnectionError as exc:
            raise SMSTransportUnavailableError(
                "Twilio transport is unavailable.",
                component="twilio_provider",
                operation="send",
                context={
                    "provider": self.provider_name,
                    "event_type": message.event_type.value,
                },
                cause=exc,
            ) from exc
        except TwilioException as exc:
            raise SMSProviderError(
                "Twilio SDK failed to submit the SMS.",
                component="twilio_provider",
                operation="send",
                context={
                    "provider": self.provider_name,
                    "event_type": message.event_type.value,
                    "lower_error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        sid = getattr(result, "sid", None)
        if not isinstance(sid, str) or not sid.strip():
            raise SMSProviderError(
                "Twilio returned an SMS response without a message SID.",
                component="twilio_provider",
                operation="send",
                field="message_sid",
                context={
                    "provider": self.provider_name,
                    "event_type": message.event_type.value,
                    "received_type": type(result).__name__,
                },
            )

        accepted_at = datetime.now(timezone.utc)

        logger.info(
            {
                "event": "twilio_sms_accepted",
                "provider": self.provider_name,
                "message_id": sid,
                "event_type": message.event_type.value,
                "recipient": mask_phone_number(message.recipient.phone_e164),
                "segments": message.content.segment_count,
                "encoding": message.content.encoding,
            }
        )

        return SMSDeliveryReceipt(
            provider=self.provider_name,
            message_id=sid,
            status=SMSDeliveryStatus.ACCEPTED,
            accepted_at=accepted_at,
        )


__all__ = [
    "TwilioSettings",
    "TwilioSMSProvider",
]
