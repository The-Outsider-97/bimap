"""Authorized BIMAP audit submission, status and workspace routes."""

from __future__ import annotations

import hashlib

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Request, Response, status  # type: ignore

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.commands.enqueue_audit import EnqueueAudit
from ...app.commands.grant_entitlement import GrantEntitlement
from ...app.commands.validate_uploads import ValidateUploads
from ...app.queries.get_audit_status import GetAuditStatus
from ...app.queries.get_audit_workspace import GetAuditWorkspace
from ...app.queries.get_order import GetOrder
from ...app.services.audit_input_service import *
from ...contracts.audit_job import AuditJob
from ...domain.orders.states import OrderState

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Audits")
printer = PrettyPrinter()

_COMPONENT = "api_route_audits"


def _derived_idempotency_key(value: str, stage: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"audit:{digest}:{stage}"


class RouteAudits:
    __slots__ = (
        "router",
        "_prepare_input",
        "_validate_uploads",
        "_grant_entitlement",
        "_enqueue_audit",
        "_get_audit_status",
        "_get_audit_workspace",
        "_get_order",
        "_authorize",
    )

    def __init__(
        self,
        prepare_input: AuditInputService,
        validate_uploads: ValidateUploads,
        grant_entitlement: GrantEntitlement,
        enqueue_audit: EnqueueAudit,
        get_audit_status: GetAuditStatus,
        get_audit_workspace: GetAuditWorkspace,
        get_order: GetOrder,
        *,
        authorizer: RouteAuthorizer,
    ) -> None:
        dependencies = (
            (
                "prepare_input",
                prepare_input,
                AuditInputService,
            ),
            (
                "validate_uploads",
                validate_uploads,
                ValidateUploads,
            ),
            (
                "grant_entitlement",
                grant_entitlement,
                GrantEntitlement,
            ),
            (
                "enqueue_audit",
                enqueue_audit,
                EnqueueAudit,
            ),
            (
                "get_audit_status",
                get_audit_status,
                GetAuditStatus,
            ),
            (
                "get_audit_workspace",
                get_audit_workspace,
                GetAuditWorkspace,
            ),
            (
                "get_order",
                get_order,
                GetOrder,
            ),
        )

        for field, value, expected in dependencies:
            if not isinstance(
                value,
                expected,
            ):
                raise APIConfigurationError(
                    f"{field} must be a {expected.__name__}.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=field,
                    context={
                        "received_type":
                            type(value).__name__,
                    },
                )

        self._prepare_input = prepare_input
        self._validate_uploads = validate_uploads
        self._grant_entitlement = grant_entitlement
        self._enqueue_audit = enqueue_audit
        self._get_audit_status = get_audit_status
        self._get_audit_workspace = get_audit_workspace
        self._get_order = get_order
        self._authorize = require_route_authorizer(
            authorizer
        )

        router = APIRouter(
            prefix="/orders",
            tags=["audits"],
        )

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
    def _sources(
        value: Any,
    ) -> tuple[AuditSourceRef, ...]:
        if (
            not isinstance(value, list)
            or not value
        ):
            raise APIValidationError(
                "sources must be a non-empty array.",
                public_message=(
                    "Upload the required BIM model "
                    "before running the audit."
                ),
                component=_COMPONENT,
                operation="start_audit",
                field="sources",
            )

        result: list[AuditSourceRef] = []
        seen: set[str] = set()

        for index, item in enumerate(value):
            if not isinstance(
                item,
                Mapping,
            ):
                raise APIValidationError(
                    "Audit source must be an object.",
                    component=_COMPONENT,
                    operation="start_audit",
                    field=f"sources[{index}]",
                )

            data = validate_object_fields(
                item,
                required=(
                    "source_ref",
                    "filename",
                ),
            )

            source_ref = require_api_text(
                data["source_ref"],
                field=(
                    f"sources[{index}]."
                    "source_ref"
                ),
                component=_COMPONENT,
                operation="start_audit",
            )

            filename = require_api_text(
                data["filename"],
                field=(
                    f"sources[{index}]."
                    "filename"
                ),
                component=_COMPONENT,
                operation="start_audit",
                max_length=255,
            )

            if source_ref in seen:
                raise APIValidationError(
                    "Audit source reference is duplicated.",
                    component=_COMPONENT,
                    operation="start_audit",
                    field="sources",
                )

            seen.add(source_ref)

            result.append(
                AuditSourceRef(
                    source_ref=source_ref,
                    filename=filename,
                )
            )

        return tuple(result)

    @staticmethod
    def _metadata(
        value: Any,
    ) -> dict[str, Any]:
        if value is None:
            return {}

        if not isinstance(
            value,
            Mapping,
        ):
            raise APIValidationError(
                "metadata must be a JSON object.",
                component=_COMPONENT,
                operation="start_audit",
                field="metadata",
            )

        return dict(value)

    async def start(
        self,
        request: Request,
        order_id: str,
    ) -> Response:
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

        request_key = require_idempotency_key(
            request
        )

        order = self._get_order.find(
            target
        )

        if order is None:
            raise APINotFoundError(
                "Requested audit order does not exist.",
                component=_COMPONENT,
                operation="start_audit",
                field="order_id",
            )

        if order.state is not OrderState.UPLOADING:
            raise APIConflictError(
                "Audit can start only after its models have been staged.",
                public_message=(
                    "The audit models are not in an "
                    "upload-ready state."
                ),
                component=_COMPONENT,
                operation="start_audit",
                field="order.state",
                context={
                    "state":
                        order.state.value,
                },
            )

        payload = validate_object_fields(
            await read_json_object(request),
            required=(
                "job_id",
                "sources",
            ),
            optional=(
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

        sources = self._sources(
            payload["sources"]
        )

        prepared = self._prepare_input.prepare(
            target,
            order.product_code,
            sources,
        )

        validated = self._validate_uploads.execute(
            target,
            idempotency_key=(
                _derived_idempotency_key(
                    request_key,
                    "validate",
                )
            ),
            actor=actor,
            metadata={
                "audit_input_manifest_ref":
                    prepared.manifest_ref,
                "evidence_count":
                    len(
                        prepared.evidence_refs
                    ),
            },
        )

        if (
            validated.state
            is not OrderState.UPLOAD_VALIDATED
        ):
            raise APIInternalError(
                "Audit upload validation did not establish the required lifecycle state.",
                component=_COMPONENT,
                operation="start_audit",
                field="order.state",
            )

        self._grant_entitlement.execute(
            target,
            idempotency_key=(
                _derived_idempotency_key(
                    request_key,
                    "entitlement",
                )
            ),
            actor=actor,
        )

        entitled_order = self._get_order.execute(
            target
        )

        if (
            entitled_order.state
            is not OrderState.ENTITLED
        ):
            raise APIInternalError(
                "Audit entitlement did not establish the required lifecycle state.",
                component=_COMPONENT,
                operation="start_audit",
                field="order.state",
            )

        job = AuditJob.from_order(
            entitled_order,
            job_id=job_id,
            evidence_refs=(
                prepared.evidence_refs
            ),
            evidence_manifest_ref=(
                prepared.manifest_ref
            ),
            metadata=self._metadata(
                payload.get("metadata")
            ),
        )

        receipt = self._enqueue_audit.execute(
            job,
            idempotency_key=(
                _derived_idempotency_key(
                    request_key,
                    "queue",
                )
            ),
            actor=actor,
        )

        audit_status = (
            self._get_audit_status.execute(
                target,
                job=job,
            )
        )

        return json_response(
            {
                "job":
                    job.to_dict(),
                "queue":
                    receipt.to_dict(),
                "status":
                    audit_status.to_dict(),
            },
            status_code=(
                status.HTTP_202_ACCEPTED
            ),
            headers={
                "Cache-Control": "no-store",
            },
        )

    async def status(
        self,
        request: Request,
        order_id: str,
    ) -> Response:
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

        result = self._get_audit_status.find(
            target
        )

        if result is None:
            raise APINotFoundError(
                "Requested audit does not exist.",
                component=_COMPONENT,
                operation="get_audit_status",
                field="order_id",
            )

        return json_response(
            result.to_dict(),
            headers={
                "Cache-Control": "no-store",
            },
        )

    async def workspace(
        self,
        request: Request,
        order_id: str,
    ) -> Response:
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

        if (
            self._get_order.find(target)
            is None
        ):
            raise APINotFoundError(
                "Requested audit does not exist.",
                component=_COMPONENT,
                operation="get_audit_workspace",
                field="order_id",
            )

        result = (
            self._get_audit_workspace.find(
                target
            )
        )

        if result is None:
            raise APINotFoundError(
                "No completed audit result exists for this audit.",
                public_message=(
                    "The audit has not produced a "
                    "completed result yet."
                ),
                component=_COMPONENT,
                operation="get_audit_workspace",
                field="order_id",
            )

        return json_response(
            result.to_dict(),
            headers={
                "Cache-Control": "no-store",
            },
        )


__all__ = ["RouteAudits"]
