"""Read-only query for the latest completed BIMAP audit workspace result."""

from __future__ import annotations

from ..ports.audit_results import AuditResultRecord, AuditResultStore
from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Get Audit Workspace Query")
printer = PrettyPrinter()
_COMPONENT = "get_audit_workspace_query"


class GetAuditWorkspace:
    __slots__ = ("_store",)

    def __init__(self, store: AuditResultStore) -> None:
        if not isinstance(store, AuditResultStore):
            raise AppConfigurationError(
                "store must implement AuditResultStore.",
                component=_COMPONENT,
                operation="initialize",
                field="store",
                context={"received_type": type(store).__name__},
            )
        self._store = store

    def find(self, order_id: str) -> AuditResultRecord | None:
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="find",
        )
        return self._store.get_by_order(target)

    def execute(self, order_id: str) -> AuditResultRecord:
        result = self.find(order_id)
        if result is None:
            raise AppValidationError(
                "Completed audit result does not exist for this order.",
                component=_COMPONENT,
                operation="execute",
                field="order_id",
                context={"order_id": order_id},
            )
        return result


__all__ = ["GetAuditWorkspace"]
