"""
BIMAP application-facing adapter for the SLAI runtime.

``SLAIAdapter`` is the narrow façade higher BIMAP layers should use.  It builds
policy-approved ``SLAIJobEnvelope`` instances, delegates runtime work to
``SLAIOrchestrator``, delegates SLAI-to-BIMAP conversion to
``SLAIResultMapper``, and exposes liveness/readiness checks without leaking
AgentFactory or SharedMemory details into the application layer.

The current ``bimap/app/ports/slai.py`` scaffold does not yet define a concrete
Protocol/ABC.  This adapter therefore implements the intended port
*structurally* rather than importing or inventing an application-port class.
When that port is formally defined, it should describe the existing adapter
surface rather than duplicating adapter behavior.

Dependency direction
--------------------

``agent_policy/job_envelope/health/governance -> orchestration -> result_mapper``
``orchestration + result_mapper -> adapter``

No lower SLAI integration module imports ``adapter.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from threading import RLock
from typing import Any

from .utils.slai_errors import *
from .utils.slai_helpers import *
from ..contracts.audit_job import AuditJob
from ..contracts.finding import FindingContract
from .health import SLAIHealthReport
from .job_envelope import SLAIJobEnvelope
from .orchestration import *
from .result_mapper import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("SLAI Adapter")
printer = PrettyPrinter()


class SLAIAdapter:
    """Application-facing BIMAP façade over the SLAI integration package."""

    def __init__(
        self,
        *,
        orchestrator: SLAIOrchestrator | None = None,
        result_mapper: SLAIResultMapper | None = None,
        close_orchestrator: bool | None = None,
    ) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Initializing BIMAP SLAI adapter",
        )

        self._owns_orchestrator = orchestrator is None
        self.orchestrator = orchestrator if orchestrator is not None else SLAIOrchestrator()
        if not isinstance(self.orchestrator, SLAIOrchestrator):
            raise SLAIRuntimeContractError(
                "orchestrator must be an SLAIOrchestrator instance.",
                component="adapter",
                operation="initialize",
                field="orchestrator",
                context={"received_type": type(self.orchestrator).__name__},
            )

        self.result_mapper = result_mapper if result_mapper is not None else SLAIResultMapper(
            governance=self.orchestrator.governance,
        )
        if not isinstance(self.result_mapper, SLAIResultMapper):
            raise SLAIRuntimeContractError(
                "result_mapper must be an SLAIResultMapper instance.",
                component="adapter",
                operation="initialize",
                field="result_mapper",
                context={"received_type": type(self.result_mapper).__name__},
            )

        if close_orchestrator is None:
            self._close_orchestrator = self._owns_orchestrator
        else:
            self._close_orchestrator = require_bool(
                close_orchestrator,
                field="close_orchestrator",
                error_type=SLAIRuntimeContractError,
            )

        self._closed = False
        self._lock = RLock()
        logger.info(
            "BIMAP SLAI adapter initialized: owns_orchestrator=%s close_orchestrator=%s",
            self._owns_orchestrator,
            self._close_orchestrator,
        )

    @property
    def policy(self):
        """Expose the orchestrator's effective SLAI agent policy read-only by convention."""

        return self.orchestrator.policy

    def build_job_envelope(
        self,
        audit_job: AuditJob,
        *,
        grounded_context: Mapping[str, Any],
        requested_agents: Sequence[str] | None = None,
        correlation_id: str | None = None,
        max_context_bytes: int | None = None,
    ) -> SLAIJobEnvelope:
        """Build a validated policy-approved runtime envelope from an AuditJob."""

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Building BIMAP SLAI job envelope",
            context={"job_id": getattr(audit_job, "job_id", None)},
        )
        self._ensure_open()
        if not isinstance(audit_job, AuditJob):
            raise SLAIRuntimeContractError(
                "audit_job must be an AuditJob contract instance.",
                component="adapter",
                operation="build_job_envelope",
                field="audit_job",
                context={"received_type": type(audit_job).__name__},
            )
        return SLAIJobEnvelope.build(
            audit_job,
            policy=self.orchestrator.policy,
            grounded_context=grounded_context,
            requested_agents=requested_agents,
            correlation_id=correlation_id,
            max_context_bytes=max_context_bytes,
        )

    def orchestrate_job(
        self,
        job_envelope: SLAIJobEnvelope,
        *,
        task_overrides: Mapping[str, Any] | None = None,
    ) -> SLAIOrchestrationResult:
        """Execute a prepared SLAI job and return the unmapped runtime result."""

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Delegating BIMAP job to SLAI orchestrator",
            context={"job_id": getattr(getattr(job_envelope, "audit_job", None), "job_id", None)},
        )
        self._ensure_open()
        try:
            return self.orchestrator.orchestrate(
                job_envelope,
                task_overrides=task_overrides,
            )
        except SLAIIntegrationError:
            raise
        except Exception as exc:
            raise SLAIOrchestrationError(
                "Unexpected failure while delegating BIMAP job to SLAI orchestrator.",
                component="adapter",
                operation="orchestrate_job",
                context={
                    "job_id": getattr(getattr(job_envelope, "audit_job", None), "job_id", None),
                },
                cause=exc,
            ) from exc

    def process_job(
        self,
        job_envelope: SLAIJobEnvelope,
        *,
        authoritative_findings: Sequence[FindingContract] = (),
        task_overrides: Mapping[str, Any] | None = None,
    ) -> SLAIMappedResult:
        """
        Execute and map one prepared BIMAP SLAI job.

        ``authoritative_findings`` are passed unchanged to ``SLAIResultMapper``;
        SLAI outputs cannot rewrite deterministic finding fields at this boundary.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Processing BIMAP SLAI job end-to-end",
            context={"job_id": getattr(getattr(job_envelope, "audit_job", None), "job_id", None)},
        )
        self._ensure_open()
        with self._lock:
            orchestration_result = self.orchestrate_job(
                job_envelope,
                task_overrides=task_overrides,
            )
            try:
                return self.result_mapper.map_result(
                    orchestration_result,
                    authoritative_findings=authoritative_findings,
                )
            except SLAIIntegrationError:
                raise
            except Exception as exc:
                raise SLAIResultMappingError(
                    "Unexpected failure while mapping SLAI orchestration result.",
                    component="adapter",
                    operation="process_job",
                    context={"job_id": orchestration_result.job_id},
                    cause=exc,
                ) from exc

    def process_audit_job(
        self,
        audit_job: AuditJob,
        *,
        grounded_context: Mapping[str, Any],
        authoritative_findings: Sequence[FindingContract] = (),
        requested_agents: Sequence[str] | None = None,
        correlation_id: str | None = None,
        max_context_bytes: int | None = None,
        task_overrides: Mapping[str, Any] | None = None,
    ) -> SLAIMappedResult:
        """Build an envelope, execute SLAI, and map the result in one call."""

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Processing AuditJob through BIMAP SLAI adapter",
            context={"job_id": getattr(audit_job, "job_id", None)},
        )
        self._ensure_open()
        envelope = self.build_job_envelope(
            audit_job,
            grounded_context=grounded_context,
            requested_agents=requested_agents,
            correlation_id=correlation_id,
            max_context_bytes=max_context_bytes,
        )
        return self.process_job(
            envelope,
            authoritative_findings=authoritative_findings,
            task_overrides=task_overrides,
        )

    def check_liveness(self) -> SLAIHealthReport:
        """Return import-level SLAI integration liveness."""

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Checking BIMAP SLAI adapter liveness",
        )
        self._ensure_open()
        return self.orchestrator.check_liveness()

    def check_readiness(
        self,
        *,
        required_agents: Sequence[str] | None = None,
        prepare: bool = False,
    ) -> SLAIHealthReport:
        """Return runtime readiness for an explicit or policy-default agent set."""

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Checking BIMAP SLAI adapter readiness",
        )
        self._ensure_open()
        names = (
            self.orchestrator.policy.required_agents()
            if required_agents is None
            else tuple(required_agents)
        )
        return self.orchestrator.check_readiness(names, prepare=prepare)

    def close(self) -> None:
        """Close adapter-owned runtime resources exactly once."""

        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Closing BIMAP SLAI adapter",
        )
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._close_orchestrator:
                self.orchestrator.close()
            logger.info("BIMAP SLAI adapter closed")

    shutdown = close

    def __enter__(self) -> "SLAIAdapter":
        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Entering BIMAP SLAI adapter context",
        )
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Exiting BIMAP SLAI adapter context",
        )
        exc_type_name = getattr(exc_type, "__name__", str(exc_type))
        logger.info(
            "BIMAP SLAI adapter context exit: exc_type=%s exc=%s tb=%s",
            exc_type_name,
            exc,
            tb,
        )
        self.close()

    def _ensure_open(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ADAPTER",
            "Checking BIMAP SLAI adapter lifecycle",
        )
        if self._closed:
            raise SLAIRuntimeContractError(
                "SLAI adapter is closed.",
                component="adapter",
                operation="ensure_open",
            )


__all__ = [
    "SLAIAdapter",
]


if __name__ == "__main__":
    print("\n=== Running SLAI Adapter Self-Test ===\n")
    printer.status("TEST", "SLAI Adapter initialized", "info")

    orchestrator =SLAIOrchestrator()
    result_mapper =SLAIResultMapper(governance=orchestrator.governance)
    adapter = SLAIAdapter(orchestrator=orchestrator, result_mapper=result_mapper)

    printer.status("START", adapter, "success" if adapter is not None else "error")
    printer.status("PASS", "SLAI Adapter initialized", "success")

    print("\n=== * * * CHECKS * * *===\n")
    liveness = adapter.check_liveness()
    readiness = adapter.check_readiness()

    printer.status("CHECK", liveness, "success")
    printer.status("CHECK", readiness, "success")
    printer.status("PASS", "SLAI Adapter liveness/readiness checks", "success")
    
    print("\n=== * * * Process Audit Job * * *===\n")
    import datetime
    from ..domain.products.models import ProductCode
    job_id = "test_job_001"
    order_id = "test_order_001"
    order_version = 1
    product_code = ProductCode.FAMILY_AUDIT.value
    submitted_at = datetime.datetime.now(datetime.timezone.utc)
    audit_job = AuditJob(job_id, order_id, order_version=order_version,
                         product_code=product_code, submitted_at=submitted_at,
                         evidence_manifest_ref="manifest://test")
    context={"test_key": "test_value"}
    processed_result = adapter.process_audit_job(
        audit_job=audit_job,
        grounded_context=context,
    )
    printer.status("CHECK", processed_result, "success")

    print("\n=== Test ran successfully ===\n")
