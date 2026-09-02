"""
Provider-neutral notification application port for BIMAP.

The notification port abstracts delivery of customer/system notification events
without turning ``app/ports`` into an email, SMS, webhook, push, queue, or
provider-SDK implementation.  Concrete adapters decide how a logical target is
resolved and how a configured event is rendered/delivered.

The port intentionally works with a small semantic message contract rather than
raw provider payloads.  Convenience constructors bind messages to the canonical
``Order`` aggregate and, when applicable, a versioned ``ReportManifest``.

Boundary policy
---------------
* ``event_type`` is caller-defined.  This module does not invent a product-
  specific notification taxonomy or template catalogue.
* ``target_ref`` is an opaque application-owned delivery target reference.  The
  port does not assume that it is an email address, phone number, webhook URL,
  account ID, or external provider recipient ID.
* ``metadata`` is deterministic JSON-safe template/event data.  No raw BIM/RFA
  file content should be placed in it.
* ``idempotency_key`` is optional and caller-owned.  The port does not claim
  exactly-once delivery; retry and deduplication guarantees belong to the
  application/worker and concrete provider implementation.
* a successful ``send`` means the concrete adapter accepted/completed the call
  according to its own contract.  This interface does not fabricate delivery,
  read, bounce, or acknowledgement states that the repository does not define.
* timeout, unavailability, and implementation failures are translated to the
  shared application-port error vocabulary.  No retry loop lives here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType
from typing import Any

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.report_manifest import *
from ...domain.orders.models import Order
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Notifications Port")
printer = PrettyPrinter()

_COMPONENT = "notifications"


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """
    Immutable provider-neutral notification event submitted for delivery.

    The record contains stable references and JSON-safe metadata only.  It does
    not contain provider credentials, rendered MIME payloads, SDK objects, or a
    promise that a remote human recipient actually received/read the message.
    """

    event_type: str
    target_ref: str
    order_id: str
    report_id: str | None = None
    idempotency_key: str | None = None
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating notification message",
            event="notification_message_validate_start",
            context={
                "event_type": str(self.event_type),
                "order_id": str(self.order_id),
                "has_report": self.report_id is not None,
                "has_idempotency_key": self.idempotency_key is not None,
            },
        )

        event_type = require_app_text(
            self.event_type,
            field="event_type",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_message",
        )
        target_ref = require_app_text(
            self.target_ref,
            field="target_ref",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_message",
        )
        order_id = require_app_text(
            self.order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_message",
        )
        report_id = optional_app_text(
            self.report_id,
            field="report_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_message",
        )
        idempotency_key = optional_app_text(
            self.idempotency_key,
            field="idempotency_key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_message",
        )

        if not isinstance(self.metadata, Mapping):
            raise UnsupportedAppInputError(
                "Notification metadata must be a mapping.",
                component=_COMPONENT,
                operation="validate_message",
                field="metadata",
                context={"received_type": type(self.metadata).__name__},
            )
        primitive = to_app_primitive(dict(self.metadata), field="notification.metadata")
        if not isinstance(primitive, dict):
            raise AppIntegrityError(
                "Notification metadata did not normalize to a JSON object.",
                component=_COMPONENT,
                operation="validate_message",
                field="metadata",
            )

        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "target_ref", target_ref)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "report_id", report_id)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "metadata", MappingProxyType(primitive))

        logger.debug(
            {
                "event": "notification_message_validated",
                "event_type": event_type,
                "order_id": order_id,
                "report_id": report_id,
                "has_idempotency_key": idempotency_key is not None,
                "metadata_key_count": len(primitive),
            }
        )

    @classmethod
    def for_order(
        cls,
        order: Order,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "NotificationMessage":
        """Create a notification event bound to one canonical order."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating order notification message",
            event="notification_for_order_start",
            context={"order_id": getattr(order, "order_id", None)},
        )

        if not isinstance(order, Order):
            raise UnsupportedAppInputError(
                "Order notification requires a canonical Order instance.",
                component=_COMPONENT,
                operation="for_order",
                field="order",
                context={"received_type": type(order).__name__},
            )

        return cls(
            event_type=event_type,
            target_ref=target_ref,
            order_id=order.order_id,
            report_id=None,
            idempotency_key=idempotency_key,
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def for_report(
        cls,
        order: Order,
        report_manifest: ReportManifest,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "NotificationMessage":
        """Create a notification event bound to one order/report pair."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating report notification message",
            event="notification_for_report_start",
            context={
                "order_id": getattr(order, "order_id", None),
                "report_id": getattr(report_manifest, "report_id", None),
            },
        )

        if not isinstance(order, Order):
            raise UnsupportedAppInputError(
                "Report notification requires a canonical Order instance.",
                component=_COMPONENT,
                operation="for_report",
                field="order",
                context={"received_type": type(order).__name__},
            )
        if not isinstance(report_manifest, ReportManifest):
            raise UnsupportedAppInputError(
                "Report notification requires a ReportManifest contract.",
                component=_COMPONENT,
                operation="for_report",
                field="report_manifest",
                context={"received_type": type(report_manifest).__name__},
            )
        if report_manifest.order_id != order.order_id:
            raise AppIntegrityError(
                "Report manifest belongs to a different order.",
                component=_COMPONENT,
                operation="for_report",
                field="report_manifest.order_id",
                context={
                    "order_id": order.order_id,
                    "report_order_id": report_manifest.order_id,
                    "report_id": report_manifest.report_id,
                },
            )

        return cls(
            event_type=event_type,
            target_ref=target_ref,
            order_id=order.order_id,
            report_id=report_manifest.report_id,
            idempotency_key=idempotency_key,
            metadata={} if metadata is None else metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic provider-neutral message data."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing notification message",
            event="notification_message_to_dict_start",
            context={
                "event_type": self.event_type,
                "order_id": self.order_id,
                "has_report": self.report_id is not None,
            },
        )
        return {
            "event_type": self.event_type,
            "target_ref": self.target_ref,
            "order_id": self.order_id,
            "report_id": self.report_id,
            "idempotency_key": self.idempotency_key,
            "metadata": dict(self.metadata),
        }


class Notifications(ABC):
    """
    Abstract notification-delivery dependency for BIMAP application services.

    Concrete adapters implement :meth:`_send`.  The public :meth:`send` method
    owns BIMAP-level input validation, diagnostics, and generic failure
    translation.  Adapters may raise the shared ``AppPort*`` errors directly
    when they can classify provider-specific failures more accurately.
    """

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing notification port",
            event="notifications_init_start",
        )
        logger.debug(
            {
                "event": "notifications_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _send(self, message: NotificationMessage) -> None:
        """Deliver one validated notification message through a concrete adapter."""
        raise NotImplementedError

    def send(self, message: NotificationMessage) -> None:
        """Validate and submit one logical notification event for delivery."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Sending notification event",
            event="notification_send_start",
            context={
                "event_type": getattr(message, "event_type", None),
                "order_id": getattr(message, "order_id", None),
                "has_report": getattr(message, "report_id", None) is not None,
            },
        )

        if not isinstance(message, NotificationMessage):
            raise UnsupportedAppInputError(
                "Notifications.send requires a NotificationMessage.",
                component=_COMPONENT,
                operation="send",
                field="message",
                context={"received_type": type(message).__name__},
            )

        try:
            self._send(message)
        except AppError:
            raise
        except TimeoutError as exc:
            raise AppPortTimeoutError(
                "Notification delivery timed out.",
                component=_COMPONENT,
                operation="send",
                context={
                    "event_type": message.event_type,
                    "order_id": message.order_id,
                    "report_id": message.report_id,
                },
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise AppPortUnavailableError(
                "Notification delivery dependency is unavailable.",
                component=_COMPONENT,
                operation="send",
                context={
                    "event_type": message.event_type,
                    "order_id": message.order_id,
                    "report_id": message.report_id,
                },
                cause=exc,
            ) from exc
        except Exception as exc:
            raise AppPortOperationError(
                "Notification adapter failed to deliver the event.",
                component=_COMPONENT,
                operation="send",
                context={
                    "event_type": message.event_type,
                    "order_id": message.order_id,
                    "report_id": message.report_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        logger.info(
            {
                "event": "notification_send_completed",
                "event_type": message.event_type,
                "order_id": message.order_id,
                "report_id": message.report_id,
                "has_idempotency_key": message.idempotency_key is not None,
                "implementation": type(self).__name__,
            }
        )

    def notify_order(
        self,
        order: Order,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Build and send a logical order-scoped notification."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Sending order notification",
            event="notification_order_send_start",
            context={"order_id": getattr(order, "order_id", None)},
        )
        message = NotificationMessage.for_order(
            order,
            event_type=event_type,
            target_ref=target_ref,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        self.send(message)

    def notify_report(
        self,
        order: Order,
        report_manifest: ReportManifest,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Build and send a logical report-scoped notification."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Sending report notification",
            event="notification_report_send_start",
            context={
                "order_id": getattr(order, "order_id", None),
                "report_id": getattr(report_manifest, "report_id", None),
            },
        )
        message = NotificationMessage.for_report(
            order,
            report_manifest,
            event_type=event_type,
            target_ref=target_ref,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        self.send(message)


__all__ = [
    "NotificationMessage",
    "Notifications",
]


if __name__ == "__main__":
    print("\n=== Running Notifications Port Self-Test ===\n")
    printer.status("TEST", "Notifications port module initialized", "info")
    class _Recorder(Notifications):
        def __init__(self) -> None:
            self.messages: list[NotificationMessage] = []
            super().__init__()

        def _send(self, message: NotificationMessage) -> None:
            self.messages.append(message)

    order = Order.create(
        order_id="order-1",
        product_code="family_audit",
        created_at="2026-09-03T00:00:00Z",
    )
    artifact = ReportArtifactContract(
        artifact_id="artifact-1",
        filename="report.json",
        sha256="0" * 64,
        size_bytes=2,
    )
    manifest = ReportManifest(
        report_id="report-1",
        order_id="order-1",
        report_version="1.0.0",
        generated_at="2026-09-03T00:00:00Z",
        artifacts=(artifact,),
    )

    recorder = _Recorder()
    recorder.notify_order(
        order,
        event_type="order.changed",
        target_ref="target-1",
        idempotency_key="notify-order-1",
        metadata={"state": order.state.value},
    )
    recorder.notify_report(
        order,
        manifest,
        event_type="report.available",
        target_ref="target-1",
        idempotency_key="notify-report-1",
        metadata={"report_version": manifest.report_version},
    )

    assert len(recorder.messages) == 2
    assert recorder.messages[0].order_id == order.order_id
    assert recorder.messages[1].report_id == manifest.report_id
    assert recorder.messages[1].to_dict()["metadata"]["report_version"] == "1.0.0"
    printer.status("PASS", "Notification order/report binding and delivery wrapper", "success")

    print("\n=== Test ran successfully ===\n")