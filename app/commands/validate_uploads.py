"""
Validate-uploads application command for BIMAP.

This command owns the explicit application use case that commits an order's
upload phase as validated.  It deliberately does not create a second upload
validator.

``UploadService`` already owns the current supported upload-security workflow:

* staging through the provider-neutral ``Storage`` port;
* malware scanning through the ``Malware`` port;
* fail-closed acceptance of only an explicit ``clean`` scanner verdict;
* upload lifecycle transitions and optimistic order persistence.

The service also deliberately does not infer that one clean object means the
entire product upload set is complete.  Product-specific file-count,
required-file, extension/MIME, archive-expansion, and size policies are not
defined by the current upload ports and must be checked by the caller/configured
ingress policy before this command is executed.

Accordingly, ``ValidateUploads.execute`` is the lifecycle commit point after the
caller has established that the complete upload set satisfies the applicable
policy.  It delegates the authoritative transition to
``UploadService.mark_uploads_validated`` and does not duplicate malware or order
state-machine logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.upload_service import UploadService
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Validate Uploads Command")
printer = PrettyPrinter()

_COMPONENT = "validate_uploads_command"


class ValidateUploads:
    """
    Mark one order's complete upload set as validated.

    This class assumes that per-object security scanning and any configured
    whole-order upload policy have already succeeded.  It does not infer upload
    completeness from storage contents because the current storage port exposes
    no authoritative order-to-object listing contract.
    """

    __slots__ = ("_service",)

    def __init__(self, service: UploadService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing validate-uploads command",
            event="validate_uploads_command_init_start",
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
                "event": "validate_uploads_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        actor: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Order:
        """
        Commit the canonical ``UPLOADING -> UPLOAD_VALIDATED`` transition.

        The supplied idempotency key, actor, and event metadata are passed
        unchanged to the existing service/domain lifecycle machinery.  Metadata
        content is intentionally omitted from diagnostics.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing validate-uploads command",
            event="validate_uploads_command_execute_start",
            context={
                "order_id": order_id,
                "has_metadata": metadata is not None,
            },
        )

        try:
            result = self._service.mark_uploads_validated(
                order_id,
                idempotency_key=idempotency_key,
                actor=actor,
                metadata=metadata,
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
                "Validate-uploads service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        if result.state is not OrderState.UPLOAD_VALIDATED:
            raise AppIntegrityError(
                "Validate-uploads command completed outside the canonical upload-validated state.",
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
                "event": "validate_uploads_command_completed",
                "order_id": result.order_id,
                "state": result.state.value,
                "version": result.version,
            }
        )
        return result


# Retain the initial scaffold name for source compatibility while exposing the
# grammatically correct command name for new code.
ValidateUpload = ValidateUploads


__all__ = [
    "ValidateUploads",
    "ValidateUpload",
]