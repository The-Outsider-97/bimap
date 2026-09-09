"""Model-conversion application command for BIMAP."""

from __future__ import annotations

from typing import BinaryIO

from ..ports.model_conversion import ModelConversionCapability, ModelTargetFormat
from ..services.model_conversion_service import ModelConversionResult, ModelConversionService
from ..utils.app_errors import AppConfigurationError, AppError, AppIntegrityError
from ..utils.app_helpers import announce_app_action, lower_error_context
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Convert Model Command")
printer = PrettyPrinter()

_COMPONENT = "convert_model_command"


class ConvertModel:
    __slots__ = ("_service",)

    def __init__(self, service: ModelConversionService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing convert-model command",
            event="convert_model_command_init_start",
        )
        if not isinstance(service, ModelConversionService):
            raise AppConfigurationError(
                "service must be a ModelConversionService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )
        self._service = service

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        return self._service.capabilities

    def execute(
        self,
        *,
        account_id: str,
        conversion_id: str,
        idempotency_key: str,
        source: BinaryIO,
        filename: str,
        content_type: str | None,
        target_format: ModelTargetFormat | str,
    ) -> ModelConversionResult:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing convert-model command",
            event="convert_model_command_execute_start",
            context={"conversion_id": conversion_id, "target_format": str(target_format)},
        )
        try:
            result = self._service.convert(
                account_id=account_id,
                conversion_id=conversion_id,
                idempotency_key=idempotency_key,
                source=source,
                filename=filename,
                content_type=content_type,
                target_format=target_format,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "ModelConversionService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={"conversion_id": conversion_id, **lower_error_context(exc)},
                cause=exc,
            ) from exc
        if not isinstance(result, ModelConversionResult):
            raise AppIntegrityError(
                "Model-conversion service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result


__all__ = ["ConvertModel"]
