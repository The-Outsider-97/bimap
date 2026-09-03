"""
Worker-process orchestration facade for BIMAP.

``Runner`` is the highest orchestration object inside ``bimap.workers``.  The
composition root injects whichever typed worker jobs a process is intended to
serve; the runner routes explicit in-process invocations through
``WorkerEngine`` and exposes safe execution summaries through ``WorkerReports``.

Dependency direction is intentionally one-way::

    bootstrap / process composition
               ↓
             Runner
               ↓
       WorkerEngine + jobs
               ↓
    app commands / app services

``runner.py`` must never import ``bootstrap.py``.  It also does not open queue
connections, poll brokers, acknowledge deliveries, implement retry/backoff,
construct application services, or create concrete infrastructure adapters.
A provider-specific process host may receive and validate a transport message,
call the appropriate ``run_*`` method, inspect ``WorkerExecutionResult`` and its
``retryable`` metadata, and then apply transport-owned acknowledgement policy.

The runner uses explicit typed methods instead of an untyped ``dict``/``**kwargs``
dispatch envelope.  BIMAP currently has one external ``AuditJob`` contract but
no generic external worker contract for report, retention, or deletion jobs;
this module therefore does not fabricate one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar

from .engine import WorkerEngine, WorkerExecutionResult, WorkerJobType
from .jobs.audit import WorkerAudit
from .jobs.deletion import JobDeletion
from .jobs.report import JobReport
from .jobs.retention import JobRetention
from .reports import WorkerPerformanceReport, WorkerReports, WorkerRunSummary
from .utils.workers_errors import WorkerConfigurationError
from .utils.workers_helpers import announce_worker_action
from ..app.services.audit_service import AuditExecutionResult
from ..app.services.fulfilment_service import FulfilmentResult
from ..audit_engine.engine import (
    EngineEvidenceInput,
    RequirementPayload,
    RuleSelection,
    RuleVersionSelection,
)
from ..contracts.audit_job import AuditJob
from ..contracts.evidence import EvidenceContract
from ..contracts.finding import FindingContract
from ..contracts.requirement import RequirementContract
from ..domain.evidence.models import EvidenceItem
from ..domain.governance.review import Review
from ..domain.orders.models import Order
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Worker Runner")
printer = PrettyPrinter()

_COMPONENT = "worker_runner"
H = TypeVar("H")


class Runner:
    """Route explicitly composed worker jobs through one execution boundary."""

    __slots__ = (
        "_engine",
        "_reports",
        "_audit",
        "_report",
        "_retention",
        "_deletion",
    )

    def __init__(
        self,
        *,
        audit: WorkerAudit | None = None,
        report: JobReport | None = None,
        retention: JobRetention | None = None,
        deletion: JobDeletion | None = None,
        engine: WorkerEngine | None = None,
        reports: WorkerReports | None = None,
    ) -> None:
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing worker runner",
            event="worker_runner_init_start",
            context={
                "audit_configured": audit is not None,
                "report_configured": report is not None,
                "retention_configured": retention is not None,
                "deletion_configured": deletion is not None,
            },
        )

        if audit is not None and not isinstance(audit, WorkerAudit):
            raise WorkerConfigurationError(
                "audit must be a WorkerAudit instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="audit",
                context={"received_type": type(audit).__name__},
            )
        if report is not None and not isinstance(report, JobReport):
            raise WorkerConfigurationError(
                "report must be a JobReport instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="report",
                context={"received_type": type(report).__name__},
            )
        if retention is not None and not isinstance(retention, JobRetention):
            raise WorkerConfigurationError(
                "retention must be a JobRetention instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="retention",
                context={"received_type": type(retention).__name__},
            )
        if deletion is not None and not isinstance(deletion, JobDeletion):
            raise WorkerConfigurationError(
                "deletion must be a JobDeletion instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="deletion",
                context={"received_type": type(deletion).__name__},
            )
        if engine is not None and not isinstance(engine, WorkerEngine):
            raise WorkerConfigurationError(
                "engine must be a WorkerEngine instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="engine",
                context={"received_type": type(engine).__name__},
            )
        if reports is not None and not isinstance(reports, WorkerReports):
            raise WorkerConfigurationError(
                "reports must be a WorkerReports instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="reports",
                context={"received_type": type(reports).__name__},
            )

        self._audit = audit
        self._report = report
        self._retention = retention
        self._deletion = deletion
        self._engine = engine or WorkerEngine()
        self._reports = reports or WorkerReports()

        logger.info(
            {
                "event": "worker_runner_initialized",
                "available_jobs": tuple(item.value for item in self.available_jobs),
            }
        )

    @property
    def available_jobs(self) -> tuple[WorkerJobType, ...]:
        """Return configured job kinds in canonical worker order."""
        configured: list[WorkerJobType] = []
        if self._audit is not None:
            configured.append(WorkerJobType.AUDIT)
        if self._report is not None:
            configured.append(WorkerJobType.REPORT)
        if self._retention is not None:
            configured.append(WorkerJobType.RETENTION)
        if self._deletion is not None:
            configured.append(WorkerJobType.DELETION)
        return tuple(configured)

    def _require_handler(
        self,
        handler: H | None,
        expected_type: type[H],
        *,
        job_type: WorkerJobType,
    ) -> H:
        """Return one configured handler or fail as process misconfiguration."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving configured worker handler",
            event="worker_runner_handler_resolve_start",
            context={"job_type": job_type.value},
        )
        if handler is None:
            raise WorkerConfigurationError(
                "Requested worker job is not configured in this runner.",
                component=_COMPONENT,
                operation="resolve_handler",
                field=job_type.value,
                job_type=job_type.value,
                context={
                    "available_jobs": tuple(item.value for item in self.available_jobs),
                },
            )
        if not isinstance(handler, expected_type):
            raise WorkerConfigurationError(
                "Configured worker handler has an unexpected type.",
                component=_COMPONENT,
                operation="resolve_handler",
                field=job_type.value,
                job_type=job_type.value,
                context={
                    "expected_type": expected_type.__name__,
                    "received_type": type(handler).__name__,
                },
            )
        return handler

    def run_audit(
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
    ) -> WorkerExecutionResult[AuditExecutionResult]:
        """Execute one complete audit worker invocation exactly once."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running audit worker",
            event="worker_runner_audit_start",
            context={
                "job_id": getattr(job, "job_id", None),
                "order_id": getattr(job, "order_id", None),
            },
        )
        handler = self._require_handler(
            self._audit,
            WorkerAudit,
            job_type=WorkerJobType.AUDIT,
        )
        return self._engine.execute(
            WorkerJobType.AUDIT,
            "execute",
            lambda: handler.execute(
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
            job_id=getattr(job, "job_id", None),
            context={"order_id": getattr(job, "order_id", None)},
        )

    def run_report(
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
    ) -> WorkerExecutionResult[FulfilmentResult]:
        """Execute one governed report-release worker invocation exactly once."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running report worker",
            event="worker_runner_report_start",
            context={"order_id": order_id, "report_id": report_id},
        )
        handler = self._require_handler(
            self._report,
            JobReport,
            job_type=WorkerJobType.REPORT,
        )
        return self._engine.execute(
            WorkerJobType.REPORT,
            "execute",
            lambda: handler.execute(
                order_id=order_id,
                findings=findings,
                evidence=evidence,
                report_id=report_id,
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
            job_id=report_id,
            context={"order_id": order_id},
        )

    def notify_report_available(
        self,
        report_id: str,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkerExecutionResult[None]:
        """Execute report-availability notification as a separate worker effect."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running report availability notification",
            event="worker_runner_report_notify_start",
            context={"report_id": report_id},
        )
        handler = self._require_handler(
            self._report,
            JobReport,
            job_type=WorkerJobType.REPORT,
        )
        return self._engine.execute(
            WorkerJobType.REPORT,
            "notify_available",
            lambda: handler.notify_available(
                report_id,
                event_type=event_type,
                target_ref=target_ref,
                idempotency_key=idempotency_key,
                metadata=metadata,
            ),
            job_id=report_id,
        )

    def run_retention(
        self,
        order_id: str,
        *,
        object_ids: Iterable[str],
        idempotency_key: str,
        actor: str | None = None,
    ) -> WorkerExecutionResult[Order]:
        """Execute one configured retention-deadline evaluation exactly once."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running retention worker",
            event="worker_runner_retention_start",
            context={"order_id": order_id},
        )
        handler = self._require_handler(
            self._retention,
            JobRetention,
            job_type=WorkerJobType.RETENTION,
        )
        return self._engine.execute(
            WorkerJobType.RETENTION,
            "execute",
            lambda: handler.execute(
                order_id,
                object_ids=object_ids,
                idempotency_key=idempotency_key,
                actor=actor,
            ),
            job_id=order_id,
        )

    def run_deletion(
        self,
        order_id: str,
        *,
        object_ids: Iterable[str],
        idempotency_key: str,
        actor: str | None = None,
    ) -> WorkerExecutionResult[Order]:
        """Execute one already-authorized deletion command exactly once."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running deletion worker",
            event="worker_runner_deletion_start",
            context={"order_id": order_id},
        )
        handler = self._require_handler(
            self._deletion,
            JobDeletion,
            job_type=WorkerJobType.DELETION,
        )
        return self._engine.execute(
            WorkerJobType.DELETION,
            "execute",
            lambda: handler.execute(
                order_id,
                object_ids=object_ids,
                idempotency_key=idempotency_key,
                actor=actor,
            ),
            job_id=order_id,
        )

    def summarize(
        self,
        execution: WorkerExecutionResult[Any],
    ) -> WorkerRunSummary:
        """Return a content-safe summary for one worker execution."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Summarizing runner execution",
            event="worker_runner_summarize_start",
        )
        return self._reports.summarize(execution)

    def performance_report(
        self,
        executions: Iterable[WorkerExecutionResult[Any]],
    ) -> WorkerPerformanceReport:
        """Aggregate observed execution metadata without defining an SLO."""
        announce_worker_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building runner performance report",
            event="worker_runner_performance_report_start",
        )
        return self._reports.performance_report(executions)


__all__ = ["Runner"]


if __name__ == "__main__":
    print("\n=== Running Worker Runner Self-Test ===\n")
    printer.status("TEST", "Worker runner module initialized", "info")

    runner = Runner()
    assert runner.available_jobs == ()
    try:
        runner.run_retention(
            "ORD-SELF-TEST",
            object_ids=(),
            idempotency_key="SELF-TEST",
        )
    except WorkerConfigurationError:
        pass
    else:
        raise AssertionError("Unconfigured worker job did not fail closed.")

    printer.status("PASS", "Worker runner composition guards", "success")
    print("\n=== Test ran successfully ===\n")