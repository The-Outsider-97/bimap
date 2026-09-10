"""Provider-neutral persistence port for completed BIMAP audit execution results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Result Store")
printer = PrettyPrinter()

_COMPONENT = "audit_result_store"


@dataclass(frozen=True, slots=True)
class AuditResultRecord:
    order_id: str
    job_id: str
    product_code: str
    completed_at: datetime | str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        order_id = require_app_text(
            self.order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_record",
        )
        job_id = require_app_text(
            self.job_id,
            field="job_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_record",
        )
        product_code = require_app_text(
            self.product_code,
            field="product_code",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_record",
            max_length=128,
        )
        completed = ensure_app_utc_datetime(
            self.completed_at,
            field="completed_at",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_record",
        )
        if not isinstance(self.payload, Mapping):
            raise AppValidationError(
                "Audit result payload must be a mapping.",
                component=_COMPONENT,
                operation="validate_record",
                field="payload",
                context={"received_type": type(self.payload).__name__},
            )
        primitive = to_app_primitive(dict(self.payload), field="audit_result.payload")
        if not isinstance(primitive, dict):
            raise AppSerializationError(
                "Audit result payload must serialize to a JSON object.",
                component=_COMPONENT,
                operation="validate_record",
                field="payload",
            )

        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "product_code", product_code)
        object.__setattr__(
            self,
            "completed_at",
            format_app_utc_datetime(
                completed,
                field="completed_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_record",
            ),
        )
        object.__setattr__(self, "payload", MappingProxyType(primitive))

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "job_id": self.job_id,
            "product_code": self.product_code,
            "completed_at": self.completed_at,
            "payload": to_app_primitive(dict(self.payload), field="audit_result.payload"),
        }


class AuditResultStore(ABC):
    """Persist and resolve the latest completed audit result per order."""

    def __init__(self) -> None:
        logger.debug(
            {
                "event": "audit_result_store_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _save(self, record: AuditResultRecord) -> AuditResultRecord:
        raise NotImplementedError

    @abstractmethod
    def _get_by_order(self, order_id: str) -> AuditResultRecord | None:
        raise NotImplementedError

    def save(self, record: AuditResultRecord) -> AuditResultRecord:
        if not isinstance(record, AuditResultRecord):
            raise AppValidationError(
                "Audit result store accepts AuditResultRecord values only.",
                component=_COMPONENT,
                operation="save",
                field="record",
                context={"received_type": type(record).__name__},
            )
        result = self._save(record)
        if not isinstance(result, AuditResultRecord):
            raise AppIntegrityError(
                "Audit result store returned an unsupported record type.",
                component=_COMPONENT,
                operation="save",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.order_id != record.order_id or result.job_id != record.job_id:
            raise AppIntegrityError(
                "Audit result store changed record identity during persistence.",
                component=_COMPONENT,
                operation="save",
                field="result",
                context={
                    "expected_order_id": record.order_id,
                    "returned_order_id": result.order_id,
                    "expected_job_id": record.job_id,
                    "returned_job_id": result.job_id,
                },
            )
        return result

    def get_by_order(self, order_id: str) -> AuditResultRecord | None:
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="get_by_order",
        )
        result = self._get_by_order(target)
        if result is None:
            return None
        if not isinstance(result, AuditResultRecord):
            raise AppIntegrityError(
                "Audit result store returned an unsupported record type.",
                component=_COMPONENT,
                operation="get_by_order",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.order_id != target:
            raise AppIntegrityError(
                "Audit result store returned a record for another order.",
                component=_COMPONENT,
                operation="get_by_order",
                field="result.order_id",
                context={
                    "requested_order_id": target,
                    "returned_order_id": result.order_id,
                },
            )
        return result


__all__ = ["AuditResultRecord", "AuditResultStore"]
