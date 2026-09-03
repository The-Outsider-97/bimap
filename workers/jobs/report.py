"""
Asynchronous BIMAP report-generation/publication worker.

``FulfilmentService.release_report`` already owns governance validation,
deterministic report generation, deterministic ZIP construction, object
publication, immutable manifest persistence, and canonical
``PACKAGING -> DELIVERED`` lifecycle handling. This worker is therefore a thin
execution adapter around that application service.

No report schema, storage naming convention, retention duration, notification
destination, or governance rule is defined here. Notification remains separate
because ``FulfilmentService`` intentionally separates it from successful report
publication.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.services.fulfilment_service import FulfilmentResult, FulfilmentService
from ...contracts.evidence import EvidenceContract
from ...contracts.finding import FindingContract
from ...contracts.requirement import RequirementContract
from ...domain.evidence.models import EvidenceItem
from ...domain.governance.review import Review
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Job Report")
printer = PrettyPrinter()

_COMPONENT = "worker_report"


class JobReport:
    """Execute report release/delivery through ``FulfilmentService``."""

    __slots__ = ("_service",)

    def __init__(self, service: FulfilmentService) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing report worker job",
            event="worker_report_init_start",
        )
        if not isinstance(service, FulfilmentService):
            raise WorkerConfigurationError(
                "service must be a FulfilmentService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )
        self._service = service
        logger.debug(
            {
                "event": "worker_report_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        *,
        order_id: str,
        findings: Iterable[FindingContract],
        evidence: Iterable[EvidenceContract | EvidenceItem],
        report_id: str,
        report_version: str,
        artifact_ids: Mapping[str, str],
        artifact_object_ids: Mapping[str, str],
        package_object_id: str,
        packaging_idempotency_key: str,
        delivery_idempotency_key: str,
        requirements: Iterable[RequirementContract] = (),
        reviews: Iterable[Review] = (),
        expires_at: datetime | str | None = None,
        software_versions: Mapping[str, str] | None = None,
        ruleset_versions: Mapping[str, str] | None = None,
        include_pdf: bool = True,
        artifact_content_types: Mapping[str, str] | None = None,
        package_content_type: str | None = "application/zip",
        actor: str | None = None,
    ) -> FulfilmentResult:
        """Build, publish, persist and deliver one governed report package."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing report worker job",
            event="worker_report_execute_start",
            context={
                "order_id": order_id,
                "report_id": report_id,
                "include_pdf": include_pdf,
                "has_expiry": expires_at is not None,
            },
        )

        target_order_id = require_worker_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="execute",
        )
        target_report_id = require_worker_text(
            report_id,
            field="report_id",
            component=_COMPONENT,
            operation="execute",
        )

        result = run_worker_dependency(
            lambda: self._service.release_report(
                order_id=target_order_id,
                findings=findings,
                evidence=evidence,
                report_id=target_report_id,
                report_version=report_version,
                artifact_ids=artifact_ids,
                artifact_object_ids=artifact_object_ids,
                package_object_id=package_object_id,
                packaging_idempotency_key=packaging_idempotency_key,
                delivery_idempotency_key=delivery_idempotency_key,
                requirements=requirements,
                reviews=reviews,
                expires_at=expires_at,
                software_versions=software_versions,
                ruleset_versions=ruleset_versions,
                include_pdf=include_pdf,
                artifact_content_types=artifact_content_types,
                package_content_type=package_content_type,
                actor=actor,
            ),
            component=_COMPONENT,
            operation="execute",
            message="FulfilmentService failed while releasing a report.",
            context={"order_id": target_order_id, "report_id": target_report_id},
            error_type=WorkerReportError,
        )
        validated = require_worker_result(
            result,
            FulfilmentResult,
            component=_COMPONENT,
            operation="execute",
            message="FulfilmentService returned an unsupported report result.",
        )

        if (
            validated.order.order_id != target_order_id
            or validated.manifest.order_id != target_order_id
            or validated.manifest.report_id != target_report_id
        ):
            raise WorkerIntegrityError(
                "Report worker result identity does not match the requested release.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                job_type="report",
                job_id=target_report_id,
                context={
                    "requested_order_id": target_order_id,
                    "returned_order_id": validated.order.order_id,
                    "manifest_order_id": validated.manifest.order_id,
                    "returned_report_id": validated.manifest.report_id,
                },
            )

        logger.info(
            {
                "event": "worker_report_completed",
                "order_id": validated.order.order_id,
                "report_id": validated.manifest.report_id,
                "artifact_count": len(validated.artifact_objects),
                "package_size_bytes": validated.package_object.size_bytes,
                "state": validated.order.state.value,
            }
        )
        return validated

    def notify_available(
        self,
        report_id: str,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Notify availability separately from report publication."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing report-availability notification",
            event="worker_report_notify_start",
            context={"report_id": report_id},
        )
        run_worker_dependency(
            lambda: self._service.notify_report_available(
                report_id,
                event_type=event_type,
                target_ref=target_ref,
                idempotency_key=idempotency_key,
                metadata=metadata,
            ),
            component=_COMPONENT,
            operation="notify_available",
            message="FulfilmentService failed while notifying report availability.",
            context={"report_id": report_id},
            error_type=WorkerReportError,
        )
        logger.info(
            {
                "event": "worker_report_notification_completed",
                "report_id": report_id,
            }
        )


__all__ = ["JobReport"]