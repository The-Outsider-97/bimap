"""
Application-level audit execution service for BIMAP.

``AuditService`` is the Level-5 coordinator between a versioned ``AuditJob``,
the deterministic Level-4 ``AuditEngine``, the application-facing SLAI port,
the queue port, and authoritative order persistence.

The service deliberately does not duplicate responsibilities that already have
canonical owners:

* ``AuditEngine`` owns ingestion, normalization, deterministic rules, product
  audit coordination, and validation coverage;
* ``SLAIRequest`` / ``invoke_slai`` own the stable application-facing SLAI
  boundary and protect authoritative deterministic findings;
* ``Queue`` owns provider-neutral ``AuditJob`` submission;
* ``Repository`` owns persistence of the record types it actually supports;
* ``OrderService`` / the order transition authority own lifecycle mutation.

The current ``Repository`` port has no persistence operation for ``AuditResult``
or mapped SLAI results.  This service therefore does not pretend to persist
those objects through an unrelated repository method.  Callers receive the
validated execution result and may pass it to later governance/reporting stages
or to a future explicitly defined audit-result persistence port.

Audit jobs are reference-oriented contracts.  The service receives already
resolved Family/Project evidence payloads as method arguments rather than
inventing a storage-key convention for ``AuditJob.evidence_refs``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..ports.queue import Queue, QueueReceipt
from ..ports.repositories import Repository
from ..ports.slai import *
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...audit_engine.engine import *
from ...audit_engine.result import AuditResult
from ...audit_engine.utils.engine_errors import EngineError
from ...contracts.audit_job import AuditJob
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from ...domain.products.models import ProductCode
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Service")
printer = PrettyPrinter()

_COMPONENT = "audit_service"


@dataclass(frozen=True, slots=True)
class AuditExecutionResult:
    """Validated application result of deterministic audit + SLAI processing.

    The two results remain separate by design.  SLAI may add supplemental
    intelligence, but ``invoke_slai`` guarantees that it does not replace or
    rewrite the deterministic authoritative finding set.
    """

    job: AuditJob
    deterministic: AuditResult
    slai: SlaiResult

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit service execution result",
            event="audit_service_result_validate_start",
            context={"job_id": getattr(self.job, "job_id", None)},
        )

        if not isinstance(self.job, AuditJob):
            raise UnsupportedAppInputError(
                "AuditExecutionResult requires an AuditJob.",
                component=_COMPONENT,
                operation="validate_result",
                field="job",
                context={"received_type": type(self.job).__name__},
            )
        if not isinstance(self.deterministic, AuditResult):
            raise UnsupportedAppInputError(
                "AuditExecutionResult requires an AuditResult.",
                component=_COMPONENT,
                operation="validate_result",
                field="deterministic",
                context={"received_type": type(self.deterministic).__name__},
            )
        if not isinstance(self.slai, SlaiResult):
            raise UnsupportedAppInputError(
                "AuditExecutionResult requires an object implementing SlaiResult.",
                component=_COMPONENT,
                operation="validate_result",
                field="slai",
                context={"received_type": type(self.slai).__name__},
            )

        expected_product = ProductCode.parse(self.job.product_code)
        if self.deterministic.product_code is not expected_product:
            raise AppIntegrityError(
                "Deterministic audit result product does not match the audit job.",
                component=_COMPONENT,
                operation="validate_result",
                field="deterministic.product_code",
                context={
                    "job_product_code": getattr(expected_product, "value", expected_product),
                    "result_product_code": getattr(
                        self.deterministic.product_code,
                        "value",
                        self.deterministic.product_code,
                    ),
                },
            )

        if getattr(self.slai, "job_id", None) != self.job.job_id:
            raise AppIntegrityError(
                "SLAI result belongs to a different audit job.",
                component=_COMPONENT,
                operation="validate_result",
                field="slai.job_id",
                context={
                    "job_id": self.job.job_id,
                    "returned_job_id": getattr(self.slai, "job_id", None),
                },
            )
        if getattr(self.slai, "order_id", None) != self.job.order_id:
            raise AppIntegrityError(
                "SLAI result belongs to a different order.",
                component=_COMPONENT,
                operation="validate_result",
                field="slai.order_id",
                context={
                    "order_id": self.job.order_id,
                    "returned_order_id": getattr(self.slai, "order_id", None),
                },
            )
        if self.slai.authoritative_findings != self.deterministic.findings:
            raise AppIntegrityError(
                "SLAI result changed the deterministic authoritative finding set.",
                component=_COMPONENT,
                operation="validate_result",
                field="slai.authoritative_findings",
                context={
                    "deterministic_finding_count": self.deterministic.finding_count,
                    "slai_finding_count": len(self.slai.authoritative_findings),
                },
            )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic application-facing execution metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing audit service execution result",
            event="audit_service_result_to_dict_start",
            context={"job_id": self.job.job_id},
        )
        return {
            "job": self.job.to_dict(),
            "deterministic": self.deterministic.to_dict(),
            "slai": self.slai.to_dict(),
        }


class AuditService:
    """Coordinate validated audit execution without redefining lower layers."""

    def __init__(
        self,
        audit_engine: AuditEngine,
        slai: SLAIPort,
        repository: Repository,
        *,
        queue: Queue | None = None,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing audit service",
            event="audit_service_init_start",
            context={"queue_configured": queue is not None},
        )

        if not isinstance(audit_engine, AuditEngine):
            raise AppConfigurationError(
                "audit_engine must be an AuditEngine instance.",
                component=_COMPONENT,
                operation="initialize",
                field="audit_engine",
                context={"received_type": type(audit_engine).__name__},
            )
        if not isinstance(repository, Repository):
            raise AppConfigurationError(
                "repository must implement the BIMAP Repository port.",
                component=_COMPONENT,
                operation="initialize",
                field="repository",
                context={"received_type": type(repository).__name__},
            )
        if not isinstance(slai, SLAIPort):
            raise AppConfigurationError(
                "slai must implement the BIMAP SLAI application port.",
                component=_COMPONENT,
                operation="initialize",
                field="slai",
                context={"received_type": type(slai).__name__},
            )
        if queue is not None and not isinstance(queue, Queue):
            raise AppConfigurationError(
                "queue must be a Queue port instance or None.",
                component=_COMPONENT,
                operation="initialize",
                field="queue",
                context={"received_type": type(queue).__name__},
            )

        self.audit_engine = audit_engine
        self.slai = slai
        self.repository = repository
        self.queue = queue

        logger.info(
            {
                "event": "audit_service_initialized",
                "queue_configured": queue is not None,
            }
        )

    def _require_job(self, job: AuditJob, *, operation: str) -> AuditJob:
        """Validate one canonical audit-job argument."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit job",
            event="audit_service_job_validate_start",
            context={"operation": operation, "job_id": getattr(job, "job_id", None)},
        )
        if not isinstance(job, AuditJob):
            raise UnsupportedAppInputError(
                "Audit service operation requires an AuditJob contract.",
                component=_COMPONENT,
                operation=operation,
                field="job",
                context={"received_type": type(job).__name__},
            )
        return job

    def _require_order_for_job(self, job: AuditJob, *, operation: str) -> Order:
        """Load the authoritative order and verify stable job/order identity."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit job against authoritative order",
            event="audit_service_order_binding_start",
            context={"operation": operation, "job_id": job.job_id, "order_id": job.order_id},
        )

        order = self.repository.get_order(job.order_id)
        if order is None:
            raise AppValidationError(
                "Audit job references an order that does not exist.",
                component=_COMPONENT,
                operation=operation,
                field="job.order_id",
                context={"job_id": job.job_id, "order_id": job.order_id},
            )

        product = ProductCode.parse(job.product_code)
        if order.product_code != product.value:
            raise AppIntegrityError(
                "Audit job product does not match the authoritative order.",
                component=_COMPONENT,
                operation=operation,
                field="job.product_code",
                context={
                    "job_id": job.job_id,
                    "order_id": order.order_id,
                    "job_product_code": product.value,
                    "order_product_code": order.product_code,
                },
            )
        if order.version < job.order_version:
            raise AppIntegrityError(
                "Authoritative order revision is older than the audit-job revision.",
                component=_COMPONENT,
                operation=operation,
                field="job.order_version",
                context={
                    "job_id": job.job_id,
                    "job_order_version": job.order_version,
                    "current_order_version": order.version,
                },
            )
        return order

    def _validate_job_evidence_binding(
        self,
        job: AuditJob,
        result: AuditResult,
        *,
        operation: str,
    ) -> None:
        """Require every explicit job evidence reference to exist in the result."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit-job evidence references",
            event="audit_service_evidence_binding_start",
            context={
                "job_id": job.job_id,
                "job_evidence_ref_count": len(job.evidence_refs),
                "result_evidence_count": result.evidence_count,
            },
        )

        if not job.evidence_refs:
            return

        result_ids = set(result.context.evidence_ids)
        missing = tuple(ref for ref in job.evidence_refs if ref not in result_ids)
        if missing:
            raise AppIntegrityError(
                "Audit result does not contain all evidence explicitly referenced by the job.",
                component=_COMPONENT,
                operation=operation,
                field="job.evidence_refs",
                context={
                    "job_id": job.job_id,
                    "missing_evidence_refs": missing,
                },
            )

    def enqueue_audit(
        self,
        job: AuditJob,
        *,
        idempotency_key: str | None = None,
    ) -> QueueReceipt:
        """Submit a queued ``AuditJob`` through the configured queue port.

        Order-state mutation is intentionally not hidden inside this method.
        A command/use-case should transition the order to ``queued`` through the
        canonical order lifecycle first, then call this method.  If submission
        fails, retrying the same immutable job and idempotency key is safe at the
        queue boundary; BIMAP currently has no transactional outbox port that
        could make repository and broker writes atomic.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Submitting audit job to queue",
            event="audit_service_enqueue_start",
            context={"job_id": getattr(job, "job_id", None)},
        )

        target = self._require_job(job, operation="enqueue_audit")
        order = self._require_order_for_job(target, operation="enqueue_audit")
        if order.state is not OrderState.QUEUED:
            raise AppValidationError(
                "Audit job may be submitted only after the order is in queued state.",
                component=_COMPONENT,
                operation="enqueue_audit",
                field="order.state",
                context={
                    "order_id": order.order_id,
                    "current_state": order.state.value,
                    "required_state": OrderState.QUEUED.value,
                },
            )
        if self.queue is None:
            raise AppConfigurationError(
                "AuditService queue dependency is not configured.",
                component=_COMPONENT,
                operation="enqueue_audit",
                field="queue",
            )

        receipt = self.queue.enqueue(target, idempotency_key=idempotency_key)
        logger.info(
            {
                "event": "audit_service_job_enqueued",
                "job_id": target.job_id,
                "order_id": target.order_id,
            }
        )
        return receipt

    def run_deterministic(
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
    ) -> AuditResult:
        """Execute exactly one product-appropriate deterministic audit."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing deterministic audit",
            event="audit_service_deterministic_start",
            context={"job_id": getattr(job, "job_id", None)},
        )

        target = self._require_job(job, operation="run_deterministic")
        order = self._require_order_for_job(target, operation="run_deterministic")
        allowed_states = {OrderState.INGESTING, OrderState.ANALYZING}
        if order.state not in allowed_states:
            raise AppValidationError(
                "Deterministic audit execution requires an active ingestion/analysis order state.",
                component=_COMPONENT,
                operation="run_deterministic",
                field="order.state",
                context={
                    "order_id": order.order_id,
                    "current_state": order.state.value,
                    "allowed_states": tuple(sorted(state.value for state in allowed_states)),
                },
            )

        product = ProductCode.parse(target.product_code)

        try:
            if product is ProductCode.FAMILY_AUDIT:
                if family_payload is None:
                    raise AppValidationError(
                        "Family Audit requires a resolved Family Evidence payload.",
                        component=_COMPONENT,
                        operation="run_deterministic",
                        field="family_payload",
                    )
                if project_payload is not None:
                    raise AppValidationError(
                        "Family Audit must not receive a Project Evidence payload.",
                        component=_COMPONENT,
                        operation="run_deterministic",
                        field="project_payload",
                    )
                result = self.audit_engine.audit_family(
                    family_payload,
                    rule_ids=family_rule_ids,
                    versions=family_versions,
                    metadata=metadata,
                )

            elif product is ProductCode.BIM_QA:
                if project_payload is None:
                    raise AppValidationError(
                        "BIM QA requires a resolved Project Evidence payload.",
                        component=_COMPONENT,
                        operation="run_deterministic",
                        field="project_payload",
                    )
                if family_payload is not None:
                    raise AppValidationError(
                        "BIM QA must not receive a Family Evidence payload.",
                        component=_COMPONENT,
                        operation="run_deterministic",
                        field="family_payload",
                    )
                result = self.audit_engine.audit_bim_qa(
                    project_payload,
                    requirements,
                    rule_ids=project_rule_ids,
                    versions=project_versions,
                    metadata=metadata,
                )

            elif product is ProductCode.COMBINED_AUDIT:
                if family_payload is None or project_payload is None:
                    raise AppValidationError(
                        "Combined Audit requires both Family and Project Evidence payloads.",
                        component=_COMPONENT,
                        operation="run_deterministic",
                        field="evidence_payloads",
                        context={
                            "has_family_payload": family_payload is not None,
                            "has_project_payload": project_payload is not None,
                        },
                    )
                result = self.audit_engine.audit_combined(
                    family_payload,
                    project_payload,
                    requirements,
                    family_rule_ids=family_rule_ids,
                    family_versions=family_versions,
                    project_rule_ids=project_rule_ids,
                    project_versions=project_versions,
                    metadata=metadata,
                )
            else:  # ProductCode exhaustiveness guard.
                raise AppIntegrityError(
                    "Audit job resolved to an unsupported BIMAP product.",
                    component=_COMPONENT,
                    operation="run_deterministic",
                    field="job.product_code",
                    context={"product_code": str(product)},
                )
        except AppError:
            raise
        except EngineError as exc:
            raise AppError(
                "Deterministic audit engine execution failed.",
                component=_COMPONENT,
                operation="run_deterministic",
                context={"job_id": target.job_id, **lower_error_context(exc)},
                cause=exc,
            ) from exc

        if result.product_code is not product:
            raise AppIntegrityError(
                "Audit engine returned a result for a different product.",
                component=_COMPONENT,
                operation="run_deterministic",
                field="result.product_code",
                context={
                    "job_id": target.job_id,
                    "job_product_code": product.value,
                    "result_product_code": result.product_code.value,
                },
            )
        self._validate_job_evidence_binding(
            target,
            result,
            operation="run_deterministic",
        )

        logger.info(
            {
                "event": "audit_service_deterministic_completed",
                "job_id": target.job_id,
                "order_id": target.order_id,
                    "product_code": getattr(product, "value", product),
                "evidence_count": result.evidence_count,
                "finding_count": result.finding_count,
                "rule_result_count": result.rule_result_count,
            }
        )
        return result

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
    ) -> AuditExecutionResult:
        """Run deterministic audit first, then SLAI over grounded output.

        ``AuditResult.to_dict()`` is used directly as grounded context.  No
        duplicate application audit schema is fabricated.  Deterministic
        ``FindingContract`` values remain authoritative and are passed unchanged
        through the SLAI boundary.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Running complete audit service execution",
            event="audit_service_run_start",
            context={"job_id": getattr(job, "job_id", None)},
        )

        target = self._require_job(job, operation="run_audit")
        deterministic = self.run_deterministic(
            target,
            family_payload=family_payload,
            project_payload=project_payload,
            requirements=requirements,
            family_rule_ids=family_rule_ids,
            family_versions=family_versions,
            project_rule_ids=project_rule_ids,
            project_versions=project_versions,
            metadata=metadata,
        )

        request = SLAIRequest(
            audit_job=target,
            grounded_context=deterministic.to_dict(),
            authoritative_findings=deterministic.findings,
            requested_agents=(
                None if requested_agents is None else tuple(requested_agents)
            ),
            correlation_id=correlation_id,
            max_context_bytes=max_context_bytes,
            task_overrides=task_overrides,
        )
        slai_result = invoke_slai(self.slai, request)
        result = AuditExecutionResult(
            job=target,
            deterministic=deterministic,
            slai=slai_result,
        )

        logger.info(
            {
                "event": "audit_service_run_completed",
                "job_id": target.job_id,
                "order_id": target.order_id,
                "product_code": getattr(
                    deterministic.product_code,
                    "value",
                    deterministic.product_code,
                ),
                "authoritative_finding_count": deterministic.finding_count,
                "slai_terminated_early": bool(slai_result.terminated_early),
            }
        )
        return result


__all__ = [
    "AuditExecutionResult",
    "AuditService",
]