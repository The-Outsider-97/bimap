"""
Create-upload-slot application command for BIMAP.

The filename is retained because ``create_upload_slot`` is part of the current
application command layout.  The current canonical storage port, however, does
*not* expose provider-specific upload-slot or presigned-URL creation, and
``UploadService`` explicitly avoids fabricating such an API.

Accordingly, this command implements only the upload-slot preparation semantics
that are actually supported by the repository today:

* optionally bind an opaque ``upload_session_id`` to the order; and
* enter the canonical ``UPLOADING`` lifecycle state idempotently.

It returns the authoritative ``Order``.  It never invents a bucket/key,
presigned URL, upload credential, MIME allowlist, file-size limit, slot
expiration, or cloud-provider response.  If a later storage-port revision adds
an explicit provider-neutral upload-slot contract, this command can compose
that contract without changing the existing order-domain transition authority.
"""

from __future__ import annotations

from ..services.upload_service import UploadService
from ..utils.app_errors import (
    AppConfigurationError,
    AppError,
    AppIntegrityError,
)
from ..utils.app_helpers import (
    announce_app_action,
    lower_error_context,
)
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Create Upload Slot Command")
printer = PrettyPrinter()

_COMPONENT = "create_upload_slot_command"


class CreateUploadSlot:
    """
    Prepare one order for uploads using the supported ``UploadService`` boundary.

    ``upload_session_id`` is an opaque identifier supplied by the caller or an
    outer composition/infrastructure layer.  This command deliberately does not
    generate one because no repository-level session-ID policy is currently
    defined.
    """

    __slots__ = ("_service",)

    def __init__(self, service: UploadService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing create-upload-slot command",
            event="create_upload_slot_command_init_start",
        )

        if not isinstance(service, UploadService):
            raise AppConfigurationError(
                "service must be an UploadService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )

        self._service = service
        logger.debug(
            {
                "event": "create_upload_slot_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        upload_session_id: str | None = None,
        actor: str | None = None,
    ) -> Order:
        """
        Bind an optional opaque upload session and enter ``UPLOADING``.

        The idempotency and session-consistency rules are owned by
        :meth:`UploadService.begin_upload`; this command does not duplicate them.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing create-upload-slot command",
            event="create_upload_slot_command_execute_start",
            context={
                "order_id": order_id,
                "has_upload_session": upload_session_id is not None,
            },
        )

        try:
            result = self._service.begin_upload(
                order_id,
                idempotency_key=idempotency_key,
                upload_session_id=upload_session_id,
                actor=actor,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "UploadService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={
                    "order_id": order_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if not isinstance(result, Order):
            raise AppIntegrityError(
                "Create-upload-slot service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        if result.state is not OrderState.UPLOADING:
            raise AppIntegrityError(
                "Create-upload-slot command completed outside the canonical uploading state.",
                component=_COMPONENT,
                operation="execute",
                field="result.state",
                context={
                    "order_id": result.order_id,
                    "returned_state": result.state.value,
                },
            )

        logger.info(
            {
                "event": "create_upload_slot_command_completed",
                "order_id": result.order_id,
                "state": result.state.value,
                "version": result.version,
                "has_upload_session": result.upload_session_id is not None,
            }
        )
        return result


__all__ = ["CreateUploadSlot"]