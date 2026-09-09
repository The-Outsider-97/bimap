"""Application command for one governed BIMAP model-data extraction."""

from __future__ import annotations

from typing import BinaryIO

from ..ports.data_extraction import DataExtractionCapability, ExtractionDataset
from ..services.data_extraction_service import DataExtractionResult, DataExtractionService
from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Extract Model Data Command")
printer = PrettyPrinter()

_COMPONENT = "extract_model_data"


class ExtractModelData:
    __slots__ = ("_service",)

    def __init__(self, service: DataExtractionService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing ExtractModelData command",
            event="extract_model_data_init_start",
        )

        if not isinstance(service, DataExtractionService):
            raise AppConfigurationError(
                "service must be a DataExtractionService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )

        self._service = service

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._service.capabilities

    @property
    def email_available(self) -> bool:
        return self._service.email_available

    def execute(
        self,
        *,
        account_id: str,
        extraction_id: str,
        idempotency_key: str,
        source: BinaryIO,
        filename: str,
        content_type: str | None,
        datasets: tuple[ExtractionDataset | str, ...],
        email_result: bool,
    ) -> DataExtractionResult:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing ExtractModelData command",
            event="extract_model_data_execute_start",
            context={"extraction_id": extraction_id},
        )

        return self._service.extract(
            account_id=account_id,
            extraction_id=extraction_id,
            idempotency_key=idempotency_key,
            source=source,
            filename=filename,
            content_type=content_type,
            datasets=datasets,
            email_result=email_result,
        )


__all__ = ["ExtractModelData"]
