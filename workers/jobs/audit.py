"""
Asynchronous BIMAP audit-worker entry.

This module is an outer execution adapter. It delegates the complete supported
audit sequence to ``AuditService.run_audit`` and does not reconstruct ingestion,
normalization, deterministic rule execution, SLAI invocation, result mapping, or
governance policy inside the worker.

The current ``AuditService`` requires the authoritative order to already be in a
valid active audit state (``INGESTING`` or ``ANALYZING``). This worker therefore
does not manufacture order transitions around the service call.

The scaffold's previous ``ReviewService`` import is intentionally removed.
``ReviewService.request_review`` requires explicit review identifiers, reason
codes, and/or a caller-owned confidence threshold. A worker must not invent
those governance inputs merely because an audit completed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.services.audit_service import *
from ...audit_engine.engine import *
from ...contracts.audit_job import AuditJob
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Worker Audit")
printer = PrettyPrinter()

_COMPONENT = "worker_audit"


class WorkerAudit:
    """Execute one validated active ``AuditJob`` through ``AuditService``."""

    __slots__ = ("_service",)

    def __init__(self, service: AuditService) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing audit worker job",
            event="worker_audit_init_start",
        )
        if not isinstance(service, AuditService):
            raise WorkerConfigurationError(
                "service must be an AuditService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )
        self._service = service
        logger.debug(
            {
                "event": "worker_audit_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        job: AuditJob,
        *,
        family_payload: EngineEvidenceInput | None = None,
        project_payload: EngineEvidenceInput | None = None,
        requirements: RequirementPayload = (),
        family_rule_ids: RuleSelection = None,
        family_versions: RuleVersionSelection = None,
        project_rule_ids: RuleSelection = None,
        project_versions: RuleVersionSelection = None,
        metadata: Mapping[str, Any] | None = None,
        requested_agents: Sequence[str] | None = None,
        correlation_id: str | None = None,
        max_context_bytes: int | None = None,
        task_overrides: Mapping[str, Any] | None = None,
    ) -> AuditExecutionResult:
        """Run one complete deterministic + governed SLAI audit execution."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing audit worker job",
            event="worker_audit_execute_start",
            context={
                "job_id": getattr(job, "job_id", None),
                "order_id": getattr(job, "order_id", None),
            },
        )

        if not isinstance(job, AuditJob):
            raise WorkerValidationError(
                "Audit worker requires an AuditJob contract.",
                component=_COMPONENT,
                operation="execute",
                field="job",
                job_type="audit",
                context={"received_type": type(job).__name__},
            )

        result = run_worker_dependency(
            lambda: self._service.run_audit(
                job,
                family_payload=family_payload,
                project_payload=project_payload,
                requirements=requirements,
                family_rule_ids=family_rule_ids,
                family_versions=family_versions,
                project_rule_ids=project_rule_ids,
                project_versions=project_versions,
                metadata=metadata,
                requested_agents=requested_agents,
                correlation_id=correlation_id,
                max_context_bytes=max_context_bytes,
                task_overrides=task_overrides,
            ),
            component=_COMPONENT,
            operation="execute",
            message="AuditService failed while executing an audit job.",
            context={"job_id": job.job_id, "order_id": job.order_id},
            error_type=WorkerAuditError,
        )
        validated = require_worker_result(
            result,
            AuditExecutionResult,
            component=_COMPONENT,
            operation="execute",
            message="AuditService returned an unsupported audit execution result.",
        )

        if validated.job.job_id != job.job_id or validated.job.order_id != job.order_id:
            raise WorkerIntegrityError(
                "Audit worker result is bound to a different job/order.",
                component=_COMPONENT,
                operation="execute",
                field="result.job",
                job_type="audit",
                job_id=job.job_id,
                context={
                    "requested_order_id": job.order_id,
                    "returned_job_id": validated.job.job_id,
                    "returned_order_id": validated.job.order_id,
                },
            )

        logger.info(
            {
                "event": "worker_audit_completed",
                "job_id": job.job_id,
                "order_id": job.order_id,
                "product_code": getattr(
                    validated.deterministic.product_code,
                    "value",
                    validated.deterministic.product_code,
                ),
                "finding_count": validated.deterministic.finding_count,
                "evidence_count": validated.deterministic.evidence_count,
                "slai_terminated_early": bool(validated.slai.terminated_early),
            }
        )
        return validated


__all__ = ["WorkerAudit"]