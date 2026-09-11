"""
BIMAP audit-worker entry.

This module is an outer execution adapter. It delegates the complete supported
audit sequence to ``AuditService.run_audit`` and does not reconstruct ingestion,
normalization, deterministic rule execution, SLAI invocation, result mapping, or
governance policy inside the worker.

The worker owns only worker-bound lifecycle orchestration:

    QUEUED -> INGESTING -> ANALYZING
        -> resolve prepared audit input
        -> AuditService.run_audit(...)
        -> GOVERNANCE_REVIEW

The deterministic/SLAI audit is executed exactly once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.services.audit_input_service import AuditInputService
from ...app.services.audit_service import *
from ...app.services.order_service import OrderService
from ...audit_engine.engine import *
from ...contracts.audit_job import AuditJob
from ...domain.orders.states import OrderState
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Worker Audit")
printer = PrettyPrinter()

_COMPONENT = "worker_audit"


class WorkerAudit:
    """Execute one validated active ``AuditJob`` through ``AuditService``."""

    __slots__ = (
        "_service",
        "_order_service",
        "_audit_inputs",
    )

    def __init__(
        self,
        service: AuditService,
        order_service: OrderService,
        audit_inputs: AuditInputService,
    ) -> None:
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
            )

        if not isinstance(order_service, OrderService):
            raise WorkerConfigurationError(
                "order_service must be an OrderService.",
                component=_COMPONENT,
                operation="initialize",
                field="order_service",
            )

        if not isinstance(audit_inputs, AuditInputService):
            raise WorkerConfigurationError(
                "audit_inputs must be an AuditInputService.",
                component=_COMPONENT,
                operation="initialize",
                field="audit_inputs",
            )

        self._service = service
        self._order_service = order_service
        self._audit_inputs = audit_inputs

        logger.debug(
            {
                "event": "worker_audit_initialized",
                "service_type": type(service).__name__,
                "order_service_type": type(order_service).__name__,
                "audit_input_service_type": type(audit_inputs).__name__,
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

        order = self._order_service.get_order(job.order_id)

        if order.state is OrderState.QUEUED:
            order = self._order_service.transition(
                order.order_id,
                OrderState.INGESTING,
                idempotency_key=f"{job.job_id}:ingesting",
                actor="bimap-worker",
            )

        if order.state is OrderState.INGESTING:
            order = self._order_service.transition(
                order.order_id,
                OrderState.ANALYZING,
                idempotency_key=f"{job.job_id}:analyzing",
                actor="bimap-worker",
            )

        if order.state is not OrderState.ANALYZING:
            raise WorkerValidationError(
                "Audit worker requires an analyzing order.",
                component=_COMPONENT,
                operation="execute",
                field="order.state",
                job_type="audit",
                job_id=job.job_id,
                context={
                    "order_id": order.order_id,
                    "state": order.state.value,
                },
            )

        # Resolve the canonical prepared input only when the caller has not
        # supplied an explicit evidence payload.
        if family_payload is None and project_payload is None:
            if job.evidence_manifest_ref is None:
                raise WorkerValidationError(
                    "AuditJob does not reference a prepared audit-input manifest.",
                    component=_COMPONENT,
                    operation="execute",
                    field="job.evidence_manifest_ref",
                    job_type="audit",
                    job_id=job.job_id,
                )

            resolved = self._audit_inputs.resolve(
                job.evidence_manifest_ref,
                expected_order_id=job.order_id,
                expected_product_code=job.product_code,
            )

            family_payload = resolved.family_payload
            project_payload = resolved.project_payload

        # Execute exactly once. The previous implementation invoked
        # AuditService.run_audit() once before resolving the prepared input and
        # then a second time afterwards.
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
            context={
                "job_id": job.job_id,
                "order_id": job.order_id,
            },
            error_type=WorkerAuditError,
        )

        validated = require_worker_result(
            result,
            AuditExecutionResult,
            component=_COMPONENT,
            operation="execute",
            message=(
                "AuditService returned an unsupported audit execution result."
            ),
        )

        if (
            validated.job.job_id != job.job_id
            or validated.job.order_id != job.order_id
        ):
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

        # AuditService validates and persists the workspace result before the
        # externally visible lifecycle is advanced.
        self._order_service.transition(
            job.order_id,
            OrderState.GOVERNANCE_REVIEW,
            idempotency_key=f"{job.job_id}:governance-review",
            actor="bimap-worker",
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
                "slai_terminated_early": bool(
                    validated.slai.terminated_early
                ),
            }
        )

        return validated


__all__ = ["WorkerAudit"]
