"""Provider-neutral base transport for BIMAP SMS adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..sms_models import *
from ..utils.sms_errors import *
from ..utils.sms_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SMS Provider")
printer = PrettyPrinter()


class SMSProvider(ABC):
    def __init__(self, provider_name: str) -> None:
        printer.status("SMS", "Initializing SMS provider", "info")
        self.provider_name = require_text(
            provider_name,
            field="provider_name",
            max_length=64,
            allow_newlines=False,
        )
        logger.info(
            {
                "event": "sms_provider_initialized",
                "provider": self.provider_name,
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _send(self, message: OutboundSMS) -> SMSDeliveryReceipt:
        raise NotImplementedError

    def send(self, message: OutboundSMS) -> SMSDeliveryReceipt:
        printer.status("SMS", "Submitting SMS to provider", "info")
        if not isinstance(message, OutboundSMS):
            raise SMSValidationError(
                "message must be OutboundSMS.",
                component="sms_provider",
                operation="send",
                field="message",
            )

        try:
            receipt = self._send(message)
        except SMSError:
            raise
        except TimeoutError as exc:
            raise SMSTransportTimeoutError(
                "SMS provider request timed out.",
                component="sms_provider",
                operation="send",
                context={"provider": self.provider_name, "event_type": message.event_type.value},
                cause=exc,
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise SMSTransportUnavailableError(
                "SMS provider is unavailable.",
                component="sms_provider",
                operation="send",
                context={"provider": self.provider_name, "event_type": message.event_type.value},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise SMSProviderError(
                "SMS provider failed unexpectedly.",
                component="sms_provider",
                operation="send",
                context={
                    "provider": self.provider_name,
                    "event_type": message.event_type.value,
                    "lower_error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(receipt, SMSDeliveryReceipt):
            raise SMSProviderError(
                "SMS provider returned an invalid delivery receipt.",
                component="sms_provider",
                operation="send",
                field="receipt",
                context={"provider": self.provider_name, "received_type": type(receipt).__name__},
            )
        if receipt.provider.casefold() != self.provider_name.casefold():
            raise SMSProviderError(
                "SMS delivery receipt identifies a different provider.",
                component="sms_provider",
                operation="send",
                field="receipt.provider",
                context={"provider": self.provider_name, "receipt_provider": receipt.provider},
            )

        logger.info(
            {
                "event": "sms_provider_accepted",
                "provider": self.provider_name,
                "message_id": receipt.message_id,
                "event_type": message.event_type.value,
                "recipient": mask_phone_number(message.recipient.phone_e164),
                "segments": message.content.segment_count,
                "encoding": message.content.encoding,
            }
        )
        return receipt


__all__ = ["SMSProvider"]
