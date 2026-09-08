"""SMTP transport adapter for BIMAP transactional email."""

from __future__ import annotations

import os
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage as MIMEEmailMessage
from email.policy import SMTP
from email.utils import formataddr, format_datetime, make_msgid

from ..email_models import (
    EmailAddress,
    EmailDeliveryReceipt,
    EmailDeliveryStatus,
    OutboundEmail,
)
from ..utils.email_errors import (
    EmailConfigurationError,
    EmailTransportTimeoutError,
    EmailValidationError,
    SMTPAuthenticationError,
    SMTPConnectionError,
    SMTPDataRejectedError,
    SMTPRecipientRejectedError,
    SMTPSenderRejectedError,
    SMTPTemporaryFailureError,
)
from ..utils.email_helpers import (
    mask_email_address,
    normalize_display_name,
    normalize_email_address,
    optional_text,
    parse_env_bool,
    parse_env_float,
    parse_env_int,
    require_header_value,
    require_text,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SMTP Provider")
printer = PrettyPrinter()


@dataclass(frozen=True, slots=True)
class SMTPSettings:
    host: str
    from_email: str = field(repr=False)
    port: int = 587
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    from_name: str = "BIMAP"
    reply_to: str | None = field(default=None, repr=False)
    use_starttls: bool = True
    use_ssl: bool = False
    timeout_seconds: float = 15.0
    local_hostname: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", require_text(self.host, field="host", max_length=255, allow_newlines=False))
        object.__setattr__(self, "from_email", normalize_email_address(self.from_email, field="from_email"))
        object.__setattr__(self, "from_name", normalize_display_name(self.from_name, field="from_name") or "BIMAP")
        if self.reply_to is not None:
            object.__setattr__(self, "reply_to", normalize_email_address(self.reply_to, field="reply_to"))

        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise EmailConfigurationError(
                "SMTP port must be an integer between 1 and 65535.",
                component="smtp_provider",
                operation="validate_settings",
                field="port",
            )
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool):
            raise EmailConfigurationError(
                "SMTP timeout must be numeric.",
                component="smtp_provider",
                operation="validate_settings",
                field="timeout_seconds",
            )
        if not 1.0 <= float(self.timeout_seconds) <= 120.0:
            raise EmailConfigurationError(
                "SMTP timeout must be between 1 and 120 seconds.",
                component="smtp_provider",
                operation="validate_settings",
                field="timeout_seconds",
            )
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        if not isinstance(self.use_starttls, bool) or not isinstance(self.use_ssl, bool):
            raise EmailConfigurationError(
                "SMTP TLS settings must be boolean.",
                component="smtp_provider",
                operation="validate_settings",
                field="tls",
            )
        if self.use_starttls and self.use_ssl:
            raise EmailConfigurationError(
                "SMTP STARTTLS and implicit SSL cannot both be enabled.",
                component="smtp_provider",
                operation="validate_settings",
                field="tls",
            )

        username = optional_text(
            self.username,
            field="username",
            max_length=512,
            allow_newlines=False,
        )
        password = self.password
        if password is not None:
            if not isinstance(password, str) or not password or len(password) > 4096:
                raise EmailConfigurationError(
                    "SMTP password must be non-empty text within the allowed length.",
                    component="smtp_provider",
                    operation="validate_settings",
                    field="password",
                )
        if (username is None) != (password is None):
            raise EmailConfigurationError(
                "SMTP username and password must be configured together.",
                component="smtp_provider",
                operation="validate_settings",
                field="credentials",
            )
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "password", password)
        object.__setattr__(
            self,
            "local_hostname",
            optional_text(self.local_hostname, field="local_hostname", max_length=255, allow_newlines=False),
        )

    @classmethod
    def from_env(cls, prefix: str = "BIMAP_") -> "SMTPSettings":
        printer.status("EMAIL", "Loading SMTP settings", "info")
        env = os.environ
        host_key = f"{prefix}SMTP_HOST"
        from_key = f"{prefix}EMAIL_FROM"
        host = env.get(host_key)
        from_email = env.get(from_key)
        if not host:
            raise EmailConfigurationError(
                "SMTP host is not configured.",
                component="smtp_provider",
                operation="from_env",
                field=host_key,
            )
        if not from_email:
            raise EmailConfigurationError(
                "Sender email address is not configured.",
                component="smtp_provider",
                operation="from_env",
                field=from_key,
            )

        use_ssl = parse_env_bool(env.get(f"{prefix}SMTP_SSL"), field=f"{prefix}SMTP_SSL", default=False)
        use_starttls = parse_env_bool(
            env.get(f"{prefix}SMTP_STARTTLS"),
            field=f"{prefix}SMTP_STARTTLS",
            default=not use_ssl,
        )
        default_port = 465 if use_ssl else 587
        return cls(
            host=host,
            port=parse_env_int(
                env.get(f"{prefix}SMTP_PORT"),
                field=f"{prefix}SMTP_PORT",
                default=default_port,
                minimum=1,
                maximum=65535,
            ),
            username=env.get(f"{prefix}SMTP_USERNAME"),
            password=env.get(f"{prefix}SMTP_PASSWORD"),
            from_email=from_email,
            from_name=env.get(f"{prefix}EMAIL_FROM_NAME", "BIMAP"),
            reply_to=env.get(f"{prefix}EMAIL_REPLY_TO"),
            use_starttls=use_starttls,
            use_ssl=use_ssl,
            timeout_seconds=parse_env_float(
                env.get(f"{prefix}SMTP_TIMEOUT_SECONDS"),
                field=f"{prefix}SMTP_TIMEOUT_SECONDS",
                default=15.0,
                minimum=1.0,
                maximum=120.0,
            ),
            local_hostname=env.get(f"{prefix}SMTP_LOCAL_HOSTNAME"),
        )


class SMTPProvider:
    provider_name = "smtp"

    def __init__(self, settings: SMTPSettings, *, tls_context: ssl.SSLContext | None = None) -> None:
        printer.status("EMAIL", "Initializing SMTP provider", "info")
        if not isinstance(settings, SMTPSettings):
            raise EmailConfigurationError(
                "settings must be SMTPSettings.",
                component="smtp_provider",
                operation="initialize",
                field="settings",
            )
        if tls_context is not None and not isinstance(tls_context, ssl.SSLContext):
            raise EmailConfigurationError(
                "tls_context must be ssl.SSLContext or None.",
                component="smtp_provider",
                operation="initialize",
                field="tls_context",
            )
        self.settings = settings
        self._tls_context = tls_context or ssl.create_default_context()
        logger.info(
            {
                "event": "smtp_provider_initialized",
                "host": settings.host,
                "port": settings.port,
                "use_starttls": settings.use_starttls,
                "use_ssl": settings.use_ssl,
                "authentication_configured": settings.username is not None,
            }
        )

    @classmethod
    def from_env(cls, prefix: str = "BIMAP_") -> "SMTPProvider":
        return cls(SMTPSettings.from_env(prefix=prefix))

    def _build_message(self, message: OutboundEmail) -> tuple[MIMEEmailMessage, str]:
        if not isinstance(message, OutboundEmail):
            raise EmailValidationError(
                "SMTPProvider.send requires OutboundEmail.",
                component="smtp_provider",
                operation="build_message",
                field="message",
            )

        mime = MIMEEmailMessage(policy=SMTP)
        sender_name = self.settings.from_name
        mime["From"] = formataddr((sender_name, self.settings.from_email))
        mime["To"] = formataddr((message.recipient.name or "", message.recipient.email))
        mime["Subject"] = require_header_value(message.content.subject, field="subject", max_length=256)
        mime["Date"] = format_datetime(datetime.now(timezone.utc))

        domain = self.settings.from_email.rsplit("@", 1)[1]
        message_id = make_msgid(domain=domain)
        mime["Message-ID"] = message_id
        mime["X-BIMAP-Event"] = message.event_type.value
        if message.idempotency_key:
            mime["X-BIMAP-Idempotency-Key"] = require_header_value(
                message.idempotency_key,
                field="idempotency_key",
                max_length=256,
            )
        if message.correlation_id:
            mime["X-BIMAP-Correlation-ID"] = require_header_value(
                message.correlation_id,
                field="correlation_id",
                max_length=128,
            )
        for name, value in message.headers.items():
            mime[name] = value

        reply_to = message.reply_to
        if reply_to is None and self.settings.reply_to:
            reply_to = EmailAddress(self.settings.reply_to)
        if reply_to is not None:
            mime["Reply-To"] = formataddr((reply_to.name or "", reply_to.email))

        mime.set_content(message.content.text_body, subtype="plain", charset="utf-8")
        mime.add_alternative(message.content.html_body, subtype="html", charset="utf-8")
        return mime, message_id

    def _connect(self) -> smtplib.SMTP:
        client: smtplib.SMTP | None = None
        try:
            if self.settings.use_ssl:
                client = smtplib.SMTP_SSL(
                    self.settings.host,
                    self.settings.port,
                    local_hostname=self.settings.local_hostname,
                    timeout=self.settings.timeout_seconds,
                    context=self._tls_context,
                )
            else:
                client = smtplib.SMTP(
                    self.settings.host,
                    self.settings.port,
                    local_hostname=self.settings.local_hostname,
                    timeout=self.settings.timeout_seconds,
                )
            client.ehlo()
            if self.settings.use_starttls:
                client.starttls(context=self._tls_context)
                client.ehlo()
            if self.settings.username is not None and self.settings.password is not None:
                client.login(self.settings.username, self.settings.password)
            return client
        except Exception:
            if client is not None:
                try:
                    client.close()
                except OSError:
                    pass
            raise

    def send(self, message: OutboundEmail) -> EmailDeliveryReceipt:
        printer.status("EMAIL", "Sending transactional email", "info")
        mime, message_id = self._build_message(message)
        masked_recipient = mask_email_address(message.recipient.email)
        client: smtplib.SMTP | None = None

        try:
            client = self._connect()
            refused = client.send_message(
                mime,
                from_addr=self.settings.from_email,
                to_addrs=[message.recipient.email],
            )
            if refused:
                raise SMTPRecipientRejectedError(
                    "SMTP server rejected the recipient.",
                    component="smtp_provider",
                    operation="send",
                    context={"recipient": masked_recipient, "refused_count": len(refused)},
                )
        except SMTPRecipientRejectedError:
            raise
        except smtplib.SMTPAuthenticationError as exc:
            raise SMTPAuthenticationError(
                "SMTP authentication failed.",
                component="smtp_provider",
                operation="send",
                context={"smtp_code": int(exc.smtp_code)},
                cause=exc,
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise SMTPRecipientRejectedError(
                "SMTP server rejected the recipient.",
                component="smtp_provider",
                operation="send",
                context={"recipient": masked_recipient, "refused_count": len(exc.recipients)},
                cause=exc,
            ) from exc
        except smtplib.SMTPSenderRefused as exc:
            raise SMTPSenderRejectedError(
                "SMTP server rejected the configured sender.",
                component="smtp_provider",
                operation="send",
                context={"smtp_code": int(exc.smtp_code)},
                cause=exc,
            ) from exc
        except smtplib.SMTPDataError as exc:
            error_type = SMTPTemporaryFailureError if 400 <= int(exc.smtp_code) < 500 else SMTPDataRejectedError
            raise error_type(
                "SMTP server rejected the message data.",
                component="smtp_provider",
                operation="send",
                context={"smtp_code": int(exc.smtp_code)},
                cause=exc,
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise EmailTransportTimeoutError(
                "SMTP operation timed out.",
                component="smtp_provider",
                operation="send",
                context={"host": self.settings.host, "port": self.settings.port},
                cause=exc,
            ) from exc
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, ConnectionError, OSError) as exc:
            smtp_code = getattr(exc, "smtp_code", None)
            if isinstance(smtp_code, int) and 400 <= smtp_code < 500:
                raise SMTPTemporaryFailureError(
                    "SMTP server is temporarily unavailable.",
                    component="smtp_provider",
                    operation="send",
                    context={"host": self.settings.host, "port": self.settings.port, "smtp_code": smtp_code},
                    cause=exc,
                ) from exc
            raise SMTPConnectionError(
                "SMTP server connection failed.",
                component="smtp_provider",
                operation="send",
                context={"host": self.settings.host, "port": self.settings.port},
                cause=exc,
            ) from exc
        except smtplib.SMTPException as exc:
            raise SMTPConnectionError(
                "SMTP transport failed.",
                component="smtp_provider",
                operation="send",
                context={"host": self.settings.host, "port": self.settings.port},
                cause=exc,
            ) from exc
        finally:
            if client is not None:
                try:
                    client.quit()
                except (smtplib.SMTPException, OSError):
                    try:
                        client.close()
                    except OSError:
                        pass

        accepted_at = datetime.now(timezone.utc)
        logger.info(
            {
                "event": "smtp_message_accepted",
                "event_type": message.event_type.value,
                "recipient": masked_recipient,
                "message_id": message_id,
            }
        )
        return EmailDeliveryReceipt(
            provider=self.provider_name,
            message_id=message_id,
            status=EmailDeliveryStatus.ACCEPTED,
            accepted_at=accepted_at,
        )


__all__ = ["SMTPSettings", "SMTPProvider"]
