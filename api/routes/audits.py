"""Authorized audit execution, status, and workspace-result routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request, Response, status # type: ignore

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.enqueue_audit import EnqueueAudit
from ...app.queries.get_audit_status import GetAuditStatus
from ...app.queries.get_audit_workspace import GetAuditWorkspace
from ...app.queries.get_order import GetOrder
from ...contracts.audit_job import AuditJob
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Audits")
printer = PrettyPrinter()
_COMPONENT = "api_route_audits"


class RouteAudits:
    __slots__ = (
        "router",
        "_enqueue_audit",
        "_get_audit_status",
        "_get_audit_workspace",
        "_get_order",
        "_authorize",
    )

    def __init__(
        self,
        enqueue_audit: EnqueueAudit,
        get_audit_status: GetAuditStatus,
        get_order: GetOrder,
        *,
        authorizer: RouteAuthorizer,
        get_audit_workspace: GetAuditWorkspace | None = None,
    ) -> None:
        dependencies = (
            ("enqueue_audit", enqueue_audit, EnqueueAudit),
            ("get_audit_status", get_audit_status, GetAuditStatus),
            ("get_order", get_order, GetOrder),
        )
        for field, value, expected in dependencies:
            if not isinstance(value, expected):
                raise APIConfigurationError(
                    f"{field} must be a {expected.__name__} handler.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=field,
                    context={"received_type": type(value).__name__},
                )
        if (
            get_audit_workspace is not None
            and not isinstance(get_audit_workspace, GetAuditWorkspace)
        ):
            raise APIConfigurationError(
                "get_audit_workspace must be a GetAuditWorkspace handler or None.",
                component=_COMPONENT,
                operation="initialize",
                field="get_audit_workspace",
                context={"received_type": type(get_audit_workspace).__name__},
            )

        self._enqueue_audit = enqueue_audit
        self._get_audit_status = get_audit_status
        self._get_audit_workspace = get_audit_workspace
        self._get_order = get_order
        self._authorize = require_route_authorizer(authorizer)

        router = APIRouter(prefix="/orders", tags=["audits"])
        router.add_api_route(
            "/{order_id}/audit",
            self.start,
            methods=["POST"],
            status_code=status.HTTP_202_ACCEPTED,
            response_class=Response,
            name="start_audit",
        )
        router.add_api_route(
            "/{order_id}/audit/status",
            self.status,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="get_audit_status",
        )
        router.add_api_route(
            "/{order_id}/audit/workspace",
            self.workspace,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="get_audit_workspace",
        )
        self.router = router

    @staticmethod
    def _evidence_refs(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes, bytearray, Mapping)):
            raise APIValidationError(
                "evidence_refs must be an array of evidence identifiers.",
                component=_COMPONENT,
                operation="start_audit",
                field="evidence_refs",
                context={"received_type": type(value).__name__},
            )
        return normalize_route_texts(
            value,
            field="evidence_refs",
            allow_empty=True,
            error_type=APIValidationError,
        )

    @staticmethod
    def _metadata(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise APIValidationError(
                "metadata must be a JSON object.",
                component=_COMPONENT,
                operation="start_audit",
                field="metadata",
                context={"received_type": type(value).__name__},
            )
        return dict(value)

    async def start(self, request: Request, order_id: str) -> Response:
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="start_audit",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="start_audit",
            resource_id=target,
        )
        idempotency_key = require_idempotency_key(request)

        order = self._get_order.find(target)
        if order is None:
            raise APINotFoundError(
                "Requested order does not exist.",
                component=_COMPONENT,
                operation="start_audit",
                field="order_id",
                context={"order_id": target},
            )

        payload = validate_object_fields(
            await read_json_object(request),
            required=("job_id",),
            optional=(
                "evidence_refs",
                "evidence_manifest_ref",
                "metadata",
            ),
        )
        job_id = require_api_text(
            payload["job_id"],
            field="job_id",
            component=_COMPONENT,
            operation="start_audit",
            max_length=256,
        )
        evidence_refs = self._evidence_refs(payload.get("evidence_refs"))
        manifest_ref = optional_route_text(
            payload.get("evidence_manifest_ref"),
            field="evidence_manifest_ref",
            max_length=2048,
        )
        if not evidence_refs and manifest_ref is None:
            raise APIValidationError(
                "Audit submission requires evidence_refs or evidence_manifest_ref.",
                public_message="Select validated audit evidence before starting the audit.",
                component=_COMPONENT,
                operation="start_audit",
                field="evidence_refs",
            )

        job = AuditJob.from_order(
            order,
            job_id=job_id,
            evidence_refs=evidence_refs,
            evidence_manifest_ref=manifest_ref,
            metadata=self._metadata(payload.get("metadata")),
        )
        receipt = self._enqueue_audit.execute(
            job,
            idempotency_key=idempotency_key,
            actor=actor,
        )
        audit_status = self._get_audit_status.execute(target, job=job)

        logger.info(
            {
                "event": "api_route_audit_accepted",
                "order_id": target,
                "job_id": job.job_id,
                "queue_reference": receipt.queue_reference,
            }
        )
        return json_response(
            {
                "job": job.to_dict(),
                "queue": receipt.to_dict(),
                "status": audit_status.to_dict(),
            },
            status_code=status.HTTP_202_ACCEPTED,
            headers={"Cache-Control": "no-store"},
        )

    async def status(self, request: Request, order_id: str) -> Response:
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="get_audit_status",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="get_audit_status",
            resource_id=target,
        )
        result = self._get_audit_status.find(target)
        if result is None:
            raise APINotFoundError(
                "Requested order does not exist.",
                component=_COMPONENT,
                operation="get_audit_status",
                field="order_id",
                context={"order_id": target},
            )
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def workspace(self, request: Request, order_id: str) -> Response:
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="get_audit_workspace",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="get_audit_workspace",
            resource_id=target,
        )
        if self._get_order.find(target) is None:
            raise APINotFoundError(
                "Requested order does not exist.",
                component=_COMPONENT,
                operation="get_audit_workspace",
                field="order_id",
                context={"order_id": target},
            )
        if self._get_audit_workspace is None:
            raise APIServiceUnavailableError(
                "Audit result read model is not configured.",
                public_message="Completed audit results are temporarily unavailable.",
                component=_COMPONENT,
                operation="get_audit_workspace",
            )
        result = self._get_audit_workspace.find(target)
        if result is None:
            raise APINotFoundError(
                "No completed audit result exists for this order.",
                public_message="The audit has not produced a completed result yet.",
                component=_COMPONENT,
                operation="get_audit_workspace",
                field="order_id",
                context={"order_id": target},
            )
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteAudits"]
