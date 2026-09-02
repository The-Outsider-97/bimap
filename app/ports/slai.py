"""
Stable application-facing SLAI port for BIMAP.

This module formalizes the application port that the existing
``bimap.slai.adapter.SLAIAdapter`` already satisfies structurally at its narrow,
high-level façade:

- ``process_audit_job(...)``
- ``check_liveness()``
- ``check_readiness(...)``
- ``close()`` / ``shutdown()``

The port deliberately does not import ``bimap.slai`` implementation modules.
The dependency direction therefore remains inverted:

    app/ports/slai.py
            ↑ structurally implemented by
    slai/adapter.py

Application code depends on the port, while the concrete SLAI integration
package remains replaceable.  The port also does not expose lower SLAI runtime
objects such as ``SLAIJobEnvelope``, ``SLAIOrchestrationResult``, AgentFactory,
SharedMemory, individual agents, or SLAI-native governance classes.

Boundary policy
---------------
* ``AuditJob`` remains the authoritative versioned work contract.
* ``grounded_context`` must already be JSON-safe BIMAP-owned analytical data.
  This module does not decide which audit fields an application service should
  expose to reasoning.
* ``authoritative_findings`` are passed through unchanged.  Supplemental SLAI
  output must never rewrite deterministic finding identity or evidence linkage.
* requested agents, task overrides, context-size limits, and correlation IDs are
  caller-/composition-owned.  This port validates shape but does not invent
  agent policy, task contracts, thresholds, retries, or governance decisions.
* SLAI implementation exceptions are translated at the application wrapper
  boundary into BIMAP application-port errors without importing the SLAI error
  hierarchy here.
* Protocol declarations are intentionally side-effect free.  Executable wrapper
  functions and value-object methods emit method-start diagnostics; the current
  ``SLAIAdapter`` independently performs its own implementation-level logging.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Protocol, cast, runtime_checkable

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.audit_job import AuditJob
from ...contracts.finding import FindingContract
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SLAI Application Port")
printer = PrettyPrinter()

_COMPONENT = "slai_port"


def _normalize_text_sequence(
    values: Sequence[str] | None,
    *,
    field: str,
) -> tuple[str, ...] | None:
    """Normalize a stable unique text sequence while preserving input order."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Normalizing SLAI text sequence: {field}",
        event="slai_text_sequence_normalize_start",
    )

    if values is None:
        return None
    if isinstance(values, (str, bytes, bytearray)):
        raise AppValidationError(
            "SLAI sequence input must contain individual text items.",
            component=_COMPONENT,
            operation="normalize_text_sequence",
            field=field,
            context={"received_type": type(values).__name__},
        )

    try:
        raw_values = tuple(values)
    except TypeError as exc:
        raise AppValidationError(
            "SLAI sequence input must be iterable.",
            component=_COMPONENT,
            operation="normalize_text_sequence",
            field=field,
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw_values):
        text = require_app_text(
            value,
            field=f"{field}[{index}]",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="normalize_text_sequence",
        )
        if text in seen:
            raise AppValidationError(
                "SLAI sequence contains a duplicate normalized value.",
                component=_COMPONENT,
                operation="normalize_text_sequence",
                field=field,
                context={"duplicate": text, "index": index},
            )
        seen.add(text)
        normalized.append(text)

    return tuple(normalized)


def _normalize_grounded_context(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate grounded context as deterministic JSON-safe mapping data."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing grounded SLAI context",
        event="slai_grounded_context_normalize_start",
    )

    if not isinstance(value, Mapping):
        raise UnsupportedAppInputError(
            "grounded_context must be a mapping.",
            component=_COMPONENT,
            operation="normalize_grounded_context",
            field="grounded_context",
            context={"received_type": type(value).__name__},
        )

    primitive = to_app_primitive(dict(value), field="grounded_context")
    if not isinstance(primitive, dict):
        raise AppIntegrityError(
            "Grounded SLAI context did not normalize to a JSON object.",
            component=_COMPONENT,
            operation="normalize_grounded_context",
            field="grounded_context",
        )
    return MappingProxyType(primitive)


def _normalize_task_overrides(
    value: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """
    Normalize task-override keys without interpreting agent-native payloads.

    Payload values intentionally remain opaque.  The SLAI integration package is
    the authority for agent-native task contracts; serializing or reshaping them
    here would duplicate that responsibility and could reject valid runtime
    objects.
    """
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing SLAI task override mapping",
        event="slai_task_overrides_normalize_start",
    )

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise UnsupportedAppInputError(
            "task_overrides must be a mapping or None.",
            component=_COMPONENT,
            operation="normalize_task_overrides",
            field="task_overrides",
            context={"received_type": type(value).__name__},
        )

    normalized: dict[str, Any] = {}
    for raw_key, payload in value.items():
        key = require_app_text(
            raw_key,
            field="task_overrides.key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="normalize_task_overrides",
        )
        if key in normalized:
            raise AppValidationError(
                "task_overrides contains duplicate normalized keys.",
                component=_COMPONENT,
                operation="normalize_task_overrides",
                field="task_overrides",
                context={"duplicate_key": key},
            )
        normalized[key] = payload

    return MappingProxyType(normalized)


@runtime_checkable
class SlaiResult(Protocol):
    """
    Structural application view of a completed SLAI mapping result.

    ``bimap.slai.result_mapper.SLAIMappedResult`` already exposes this surface.
    Keeping it structural prevents ``app/ports`` from importing the concrete
    integration result type while preserving the invariants application code
    actually needs to verify.
    """

    job_id: str
    order_id: str
    correlation_id: str
    authoritative_findings: tuple[FindingContract, ...]
    started_at: datetime
    completed_at: datetime
    terminated_early: bool
    termination_reason: str | None
    mapping_warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the mapped SLAI result as BIMAP-owned serializable data."""
        ...


@runtime_checkable
class SlaiHealth(Protocol):
    """Structural application view of a SLAI liveness/readiness report."""

    ready: bool
    live: bool
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """Return content-safe operational health metadata."""
        ...


@dataclass(frozen=True, slots=True)
class SLAIRequest:
    """
    Validated application-owned request for the narrow SLAI façade.

    This object packages only arguments already accepted by the current
    ``SLAIAdapter.process_audit_job`` method.  It does not create a second
    orchestration envelope and is never passed into SLAI internals directly.
    """

    audit_job: AuditJob
    grounded_context: Mapping[str, Any]
    authoritative_findings: tuple[FindingContract, ...] = ()
    requested_agents: tuple[str, ...] | None = None
    correlation_id: str | None = None
    max_context_bytes: int | None = None
    task_overrides: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating SLAI application request",
            event="slai_request_validate_start",
            context={"job_id": getattr(self.audit_job, "job_id", None)},
        )

        if not isinstance(self.audit_job, AuditJob):
            raise UnsupportedAppInputError(
                "SLAIRequest requires an AuditJob contract.",
                component=_COMPONENT,
                operation="validate_request",
                field="audit_job",
                context={"received_type": type(self.audit_job).__name__},
            )

        grounded_context = _normalize_grounded_context(self.grounded_context)

        if isinstance(
            self.authoritative_findings,
            (str, bytes, bytearray, Mapping),
        ):
            raise UnsupportedAppInputError(
                "authoritative_findings must be a sequence of FindingContract values.",
                component=_COMPONENT,
                operation="validate_request",
                field="authoritative_findings",
                context={
                    "received_type": type(self.authoritative_findings).__name__,
                },
            )
        try:
            findings = tuple(self.authoritative_findings)
        except TypeError as exc:
            raise UnsupportedAppInputError(
                "authoritative_findings must be iterable.",
                component=_COMPONENT,
                operation="validate_request",
                field="authoritative_findings",
                context={
                    "received_type": type(self.authoritative_findings).__name__,
                },
                cause=exc,
            ) from exc

        finding_ids: list[str] = []
        for index, finding in enumerate(findings):
            if not isinstance(finding, FindingContract):
                raise UnsupportedAppInputError(
                    "authoritative_findings accepts FindingContract values only.",
                    component=_COMPONENT,
                    operation="validate_request",
                    field=f"authoritative_findings[{index}]",
                    context={"received_type": type(finding).__name__},
                )
            if finding.finding_id in finding_ids:
                raise AppIntegrityError(
                    "authoritative_findings contains a duplicate finding identifier.",
                    component=_COMPONENT,
                    operation="validate_request",
                    field="authoritative_findings",
                    context={"finding_id": finding.finding_id},
                )
            finding_ids.append(finding.finding_id)

        requested_agents = _normalize_text_sequence(
            self.requested_agents,
            field="requested_agents",
        )
        correlation_id = optional_app_text(
            self.correlation_id,
            field="correlation_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_request",
        )
        max_context_bytes = (
            None
            if self.max_context_bytes is None
            else require_non_negative_int(
                self.max_context_bytes,
                field="max_context_bytes",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_request",
            )
        )
        task_overrides = _normalize_task_overrides(self.task_overrides)

        object.__setattr__(self, "grounded_context", grounded_context)
        object.__setattr__(self, "authoritative_findings", findings)
        object.__setattr__(self, "requested_agents", requested_agents)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "max_context_bytes", max_context_bytes)
        object.__setattr__(self, "task_overrides", task_overrides)

        logger.debug(
            {
                "event": "slai_request_validated",
                "job_id": self.audit_job.job_id,
                "order_id": self.audit_job.order_id,
                "finding_count": len(findings),
                "requested_agent_count": (
                    None if requested_agents is None else len(requested_agents)
                ),
                "has_correlation_id": correlation_id is not None,
                "has_context_limit": max_context_bytes is not None,
                "task_override_count": (
                    0 if task_overrides is None else len(task_overrides)
                ),
            }
        )


@runtime_checkable
class SLAIPort(Protocol):
    """Narrow structural application port implemented by ``SLAIAdapter``."""

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
    ) -> SlaiResult:
        """Execute and map one validated BIMAP audit job through SLAI."""
        ...

    def check_liveness(self) -> SlaiHealth:
        """Return SLAI integration liveness."""
        ...

    def check_readiness(
        self,
        *,
        required_agents: Sequence[str] | None = None,
        prepare: bool = False,
    ) -> SlaiHealth:
        """Return SLAI readiness for an explicit/default required-agent set."""
        ...

    def close(self) -> None:
        """Release owned SLAI integration resources."""
        ...

    def shutdown(self) -> None:
        """Alias for ``close`` when supplied by the implementation."""
        ...


def _require_port_method(port: Any, method_name: str) -> Any:
    """Return one required port method or fail with a structured input error."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating SLAI port method: {method_name}",
        event="slai_port_method_validate_start",
    )
    method = getattr(port, method_name, None)
    if not callable(method):
        raise UnsupportedAppInputError(
            "Object does not satisfy the required SLAI application-port surface.",
            component=_COMPONENT,
            operation="require_port_method",
            field=method_name,
            context={
                "received_type": type(port).__name__,
                "required_method": method_name,
            },
        )
    return method


def _validate_slai_result(
    value: Any,
    *,
    request: SLAIRequest,
) -> SlaiResult:
    """Validate the stable cross-layer invariants of one mapped SLAI result."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating mapped SLAI application result",
        event="slai_result_validate_start",
        context={"job_id": request.audit_job.job_id},
    )

    job_id = require_app_text(
        getattr(value, "job_id", None),
        field="result.job_id",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="validate_result",
    )
    order_id = require_app_text(
        getattr(value, "order_id", None),
        field="result.order_id",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="validate_result",
    )
    require_app_text(
        getattr(value, "correlation_id", None),
        field="result.correlation_id",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="validate_result",
    )

    if job_id != request.audit_job.job_id:
        raise AppIntegrityError(
            "SLAI result is bound to a different audit job.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.job_id",
            context={
                "expected_job_id": request.audit_job.job_id,
                "returned_job_id": job_id,
            },
        )
    if order_id != request.audit_job.order_id:
        raise AppIntegrityError(
            "SLAI result is bound to a different order.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.order_id",
            context={
                "expected_order_id": request.audit_job.order_id,
                "returned_order_id": order_id,
            },
        )

    started_at = ensure_app_utc_datetime(
        cast(datetime | str, getattr(value, "started_at", None)),
        field="result.started_at",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="validate_result",
    )
    completed_at = ensure_app_utc_datetime(
        cast(datetime | str, getattr(value, "completed_at", None)),
        field="result.completed_at",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="validate_result",
    )
    if completed_at < started_at:
        raise AppIntegrityError(
            "SLAI result completed_at cannot precede started_at.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.completed_at",
        )

    terminated_early = getattr(value, "terminated_early", None)
    if not isinstance(terminated_early, bool):
        raise AppIntegrityError(
            "SLAI result terminated_early must be boolean.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.terminated_early",
            context={"received_type": type(terminated_early).__name__},
        )
    optional_app_text(
        getattr(value, "termination_reason", None),
        field="result.termination_reason",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation="validate_result",
    )

    raw_findings = getattr(value, "authoritative_findings", None)
    if isinstance(raw_findings, (str, bytes, bytearray, Mapping)):
        raise AppIntegrityError(
            "SLAI result authoritative_findings has an invalid collection type.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.authoritative_findings",
            context={"received_type": type(raw_findings).__name__},
        )
    try:
        returned_findings = tuple(cast(Iterable[FindingContract], raw_findings))
    except TypeError as exc:
        raise AppIntegrityError(
            "SLAI result authoritative_findings must be iterable.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.authoritative_findings",
            cause=exc,
        ) from exc

    if any(not isinstance(item, FindingContract) for item in returned_findings):
        raise AppIntegrityError(
            "SLAI result contains a non-FindingContract authoritative finding.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.authoritative_findings",
        )
    if returned_findings != request.authoritative_findings:
        raise AppIntegrityError(
            "SLAI result changed the authoritative deterministic finding set.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.authoritative_findings",
            context={
                "expected_count": len(request.authoritative_findings),
                "returned_count": len(returned_findings),
            },
        )

    warnings = getattr(value, "mapping_warnings", None)
    if isinstance(warnings, (str, bytes, bytearray, Mapping)):
        raise AppIntegrityError(
            "SLAI result mapping_warnings must be a sequence of strings.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.mapping_warnings",
            context={"received_type": type(warnings).__name__},
        )
    try:
        warning_items = tuple(warnings)
    except TypeError as exc:
        raise AppIntegrityError(
            "SLAI result mapping_warnings must be iterable.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.mapping_warnings",
            cause=exc,
        ) from exc
    for index, warning in enumerate(warning_items):
        require_app_text(
            warning,
            field=f"result.mapping_warnings[{index}]",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_result",
        )

    if not callable(getattr(value, "to_dict", None)):
        raise AppIntegrityError(
            "SLAI result must provide a callable to_dict() representation.",
            component=_COMPONENT,
            operation="validate_result",
            field="result.to_dict",
        )

    return cast(SlaiResult, value)


def _validate_health(value: Any, *, operation: str) -> SlaiHealth:
    """Validate the minimal application-facing SLAI health surface."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating SLAI health result",
        event="slai_health_validate_start",
        context={"operation": operation},
    )

    ready = getattr(value, "ready", None)
    live = getattr(value, "live", None)
    if not isinstance(ready, bool):
        raise AppIntegrityError(
            "SLAI health result ready property must be boolean.",
            component=_COMPONENT,
            operation=operation,
            field="health.ready",
            context={"received_type": type(ready).__name__},
        )
    if not isinstance(live, bool):
        raise AppIntegrityError(
            "SLAI health result live property must be boolean.",
            component=_COMPONENT,
            operation=operation,
            field="health.live",
            context={"received_type": type(live).__name__},
        )
    ensure_app_utc_datetime(
        getattr(value, "checked_at", None),
        field="health.checked_at",
        error_type=AppIntegrityError,
        component=_COMPONENT,
        operation=operation,
    )
    if not callable(getattr(value, "to_dict", None)):
        raise AppIntegrityError(
            "SLAI health result must provide a callable to_dict() representation.",
            component=_COMPONENT,
            operation=operation,
            field="health.to_dict",
        )
    return cast(SlaiHealth, value)


def invoke_slai(port: SLAIPort, request: SLAIRequest) -> SlaiResult:
    """
    Invoke the narrow SLAI façade with application-level error translation.

    This function is the safest call path for application services because it
    validates the request, hides lower SLAI exception classes, and verifies that
    the returned result is still bound to the same job/order and unchanged
    authoritative finding set.
    """
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Invoking SLAI application port",
        event="slai_invoke_start",
        context={"job_id": getattr(getattr(request, "audit_job", None), "job_id", None)},
    )

    if not isinstance(request, SLAIRequest):
        raise UnsupportedAppInputError(
            "invoke_slai requires an SLAIRequest.",
            component=_COMPONENT,
            operation="invoke",
            field="request",
            context={"received_type": type(request).__name__},
        )

    method = _require_port_method(port, "process_audit_job")
    try:
        result = method(
            request.audit_job,
            grounded_context=request.grounded_context,
            authoritative_findings=request.authoritative_findings,
            requested_agents=request.requested_agents,
            correlation_id=request.correlation_id,
            max_context_bytes=request.max_context_bytes,
            task_overrides=request.task_overrides,
        )
    except AppError:
        raise
    except TimeoutError as exc:
        raise AppPortTimeoutError(
            "SLAI application-port invocation timed out.",
            component=_COMPONENT,
            operation="invoke",
            context={"job_id": request.audit_job.job_id},
            cause=exc,
        ) from exc
    except ConnectionError as exc:
        raise AppPortUnavailableError(
            "SLAI application-port dependency is unavailable.",
            component=_COMPONENT,
            operation="invoke",
            context={"job_id": request.audit_job.job_id},
            cause=exc,
        ) from exc
    except Exception as exc:
        raise AppPortOperationError(
            "SLAI application-port invocation failed.",
            component=_COMPONENT,
            operation="invoke",
            context={
                "job_id": request.audit_job.job_id,
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc

    validated = _validate_slai_result(result, request=request)
    logger.info(
        {
            "event": "slai_invoke_completed",
            "job_id": request.audit_job.job_id,
            "order_id": request.audit_job.order_id,
            "terminated_early": validated.terminated_early,
            "finding_count": len(validated.authoritative_findings),
        }
    )
    return validated


def probe_slai_liveness(port: SLAIPort) -> SlaiHealth:
    """Probe SLAI liveness through the application port with error translation."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Probing SLAI liveness",
        event="slai_liveness_probe_start",
    )
    method = _require_port_method(port, "check_liveness")
    try:
        health = method()
    except AppError:
        raise
    except Exception as exc:
        raise AppPortOperationError(
            "SLAI liveness probe failed.",
            component=_COMPONENT,
            operation="check_liveness",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc
    return _validate_health(health, operation="check_liveness")


def probe_slai_readiness(
    port: SLAIPort,
    *,
    required_agents: Sequence[str] | None = None,
    prepare: bool = False,
) -> SlaiHealth:
    """Probe SLAI readiness for an explicit/default required-agent set."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Probing SLAI readiness",
        event="slai_readiness_probe_start",
    )

    normalized_agents = _normalize_text_sequence(
        required_agents,
        field="required_agents",
    )
    if not isinstance(prepare, bool):
        raise AppValidationError(
            "prepare must be boolean.",
            component=_COMPONENT,
            operation="check_readiness",
            field="prepare",
            context={"received_type": type(prepare).__name__},
        )

    method = _require_port_method(port, "check_readiness")
    try:
        health = method(required_agents=normalized_agents, prepare=prepare)
    except AppError:
        raise
    except TimeoutError as exc:
        raise AppPortTimeoutError(
            "SLAI readiness probe timed out.",
            component=_COMPONENT,
            operation="check_readiness",
            cause=exc,
        ) from exc
    except ConnectionError as exc:
        raise AppPortUnavailableError(
            "SLAI readiness dependency is unavailable.",
            component=_COMPONENT,
            operation="check_readiness",
            cause=exc,
        ) from exc
    except Exception as exc:
        raise AppPortOperationError(
            "SLAI readiness probe failed.",
            component=_COMPONENT,
            operation="check_readiness",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc

    return _validate_health(health, operation="check_readiness")


def close_slai(port: SLAIPort) -> None:
    """Close one SLAI port implementation with application-level translation."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Closing SLAI application port",
        event="slai_close_start",
    )
    method = _require_port_method(port, "close")
    try:
        method()
    except AppError:
        raise
    except Exception as exc:
        raise AppPortOperationError(
            "SLAI application port failed to close cleanly.",
            component=_COMPONENT,
            operation="close",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc


__all__ = [
    "SlaiResult",
    "SlaiHealth",
    "SLAIRequest",
    "SLAIPort",
    "invoke_slai",
    "probe_slai_liveness",
    "probe_slai_readiness",
    "close_slai",
]


if __name__ == "__main__":
    from datetime import timezone

    print("\n=== Running SLAI Application Port Self-Test ===\n")
    printer.status("TEST", "SLAI application port module initialized", "info")

    @dataclass(frozen=True)
    class _Result:
        job_id: str
        order_id: str
        correlation_id: str
        authoritative_findings: tuple[FindingContract, ...]
        started_at: datetime
        completed_at: datetime
        terminated_early: bool = False
        termination_reason: str | None = None
        mapping_warnings: tuple[str, ...] = ()

        def to_dict(self) -> dict[str, Any]:
            return {
                "job_id": self.job_id,
                "order_id": self.order_id,
                "correlation_id": self.correlation_id,
            }

    @dataclass(frozen=True)
    class _Health:
        ready: bool
        live: bool
        checked_at: datetime

        def to_dict(self) -> dict[str, Any]:
            return {
                "ready": self.ready,
                "live": self.live,
                "checked_at": self.checked_at.isoformat(),
            }

    class _Port:
        def __init__(self) -> None:
            self.closed = False

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
        ) -> _Result:
            now = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
            return _Result(
                job_id=audit_job.job_id,
                order_id=audit_job.order_id,
                correlation_id=correlation_id or "test-correlation",
                authoritative_findings=tuple(authoritative_findings),
                started_at=now,
                completed_at=now,
            )

        def check_liveness(self) -> _Health:
            return _Health(
                ready=False,
                live=True,
                checked_at=datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc),
            )

        def check_readiness(
            self,
            *,
            required_agents: Sequence[str] | None = None,
            prepare: bool = False,
        ) -> _Health:
            return _Health(
                ready=True,
                live=True,
                checked_at=datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc),
            )

        def close(self) -> None:
            self.closed = True

        shutdown = close

    job = AuditJob(
        job_id="job-1",
        order_id="order-1",
        order_version=1,
        product_code="family_audit",
        submitted_at="2026-09-03T00:00:00Z",
        evidence_manifest_ref="manifest://test",
    )
    request = SLAIRequest(
        audit_job=job,
        grounded_context={"evidence_ids": []},
        requested_agents=("quality", "reasoning"),
    )
    port = _Port()
    result = invoke_slai(port, request)
    assert result.job_id == "job-1"
    assert probe_slai_liveness(port).live is True
    assert probe_slai_readiness(port).ready is True
    close_slai(port)
    assert port.closed is True
    printer.status("PASS", "SLAI port structure and boundary invariants", "success")

    print("\n=== Test ran successfully ===\n")