"""
Structured error hierarchy for the BIMAP -> SLAI integration boundary.

The SLAI integration layer sits above BIMAP's domain/contracts layers and below
application services.  Errors raised here therefore describe integration,
policy, runtime-health, governance-mapping, and envelope failures without
leaking raw customer evidence or depending on concrete SLAI agent classes.

Dependency direction
--------------------
slai/utils/slai_errors.py
    -> Python standard library
    -> SLAI logging utilities

Keeping the error hierarchy at the bottom of the BIMAP/SLAI dependency graph
allows every integration module to share one stable exception vocabulary while
avoiding circular imports.

Logging policy
--------------
Exception construction does not emit an error log.  Architectural boundaries
(API, worker, bootstrap, adapter/orchestrator) should log a handled failure once.
``SLAIIntegrationError.announce`` exists for explicit operator-facing status
emission when desired.  Diagnostic context is bounded and redacted so raw BIM
or customer payloads are not accidentally written to logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP SLAI Errors")
printer = PrettyPrinter()


_REDACTED = "<redacted>"
_MAX_CONTEXT_ITEMS = 32
_MAX_CONTEXT_STRING = 256
_MAX_CONTEXT_DEPTH = 3
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization",
    "cookie",
    "credential",
    "document",
    "evidence_content",
    "evidence_value",
    "file_bytes",
    "password",
    "payload",
    "raw",
    "secret",
    "session",
    "token",
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _safe_context_value(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded, logging-safe representation of a context value."""

    if depth >= _MAX_CONTEXT_DEPTH:
        return f"<{type(value).__name__}>"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        if len(value) <= _MAX_CONTEXT_STRING:
            return value
        return f"{value[:_MAX_CONTEXT_STRING]}…"

    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTEXT_ITEMS:
                safe["__truncated__"] = True
                break
            rendered_key = str(key)
            safe[rendered_key] = (
                _REDACTED
                if _is_sensitive_key(rendered_key)
                else _safe_context_value(item, depth=depth + 1)
            )
        return safe

    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = list(value)
        rendered = [
            _safe_context_value(item, depth=depth + 1)
            for item in sequence[:_MAX_CONTEXT_ITEMS]
        ]
        if len(sequence) > _MAX_CONTEXT_ITEMS:
            rendered.append("<truncated>")
        return rendered

    return f"<{type(value).__name__}>"


def _normalize_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise TypeError(
            "SLAI error context must be a mapping or None, "
            f"got {type(context).__name__}."
        )

    safe: dict[str, Any] = {}
    for key, value in context.items():
        rendered_key = str(key)
        safe[rendered_key] = (
            _REDACTED
            if _is_sensitive_key(rendered_key)
            else _safe_context_value(value)
        )
    return safe


class SLAIIntegrationError(Exception):
    """Base exception for BIMAP's SLAI integration boundary.

    Parameters
    ----------
    message:
        Technical operator-facing description.  Do not include raw customer
        evidence in this text.
    component:
        Optional integration component (for example ``agent_policy`` or
        ``governance``).
    operation:
        Optional operation that failed.
    field:
        Optional logical field involved in validation.
    context:
        Optional non-sensitive structured diagnostics.  Values are bounded and
        sensitive-looking keys are redacted before storage on the exception.
    cause:
        Optional underlying exception.  ``to_dict`` exposes only its type, not
        its message, because nested exceptions can contain customer data.
    """

    code = "BIMAP.SLAI.ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        operation: str | None = None,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        self.message = normalized_message
        self.component = str(component).strip() if component is not None else None
        self.operation = str(operation).strip() if operation is not None else None
        self.field = str(field).strip() if field is not None else None
        self.context = _normalize_context(context)
        self.cause = cause

        qualifiers: list[str] = []
        if self.component:
            qualifiers.append(f"component={self.component}")
        if self.operation:
            qualifiers.append(f"operation={self.operation}")
        if self.field:
            qualifiers.append(f"field={self.field}")

        rendered = normalized_message
        if qualifiers:
            rendered = f"{rendered} [{', '.join(qualifiers)}]"
        super().__init__(rendered)

    def announce(
        self,
        *,
        label: str = "BIMAP/SLAI",
        level: str = "error",
    ) -> None:
        """Explicitly emit an operator-facing status for a handled error."""

        printer.status(label, self.message, level)
        logger.debug(
            {
                "event": "bimap_slai_error_announced",
                "code": self.code,
                "type": self.__class__.__name__,
                "component": self.component,
                "operation": self.operation,
                "field": self.field,
                "retryable": bool(self.retryable),
            }
        )

    def to_dict(
        self,
        *,
        include_context: bool = True,
        include_cause_type: bool = True,
    ) -> dict[str, Any]:
        """Return a deterministic, logging-safe representation."""

        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.component:
            payload["component"] = self.component
        if self.operation:
            payload["operation"] = self.operation
        if self.field:
            payload["field"] = self.field
        if include_context and self.context:
            payload["context"] = dict(self.context)
        if include_cause_type and self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__
        return payload


# ---------------------------------------------------------------------------
# Configuration and policy
# ---------------------------------------------------------------------------


class SLAIConfigurationError(SLAIIntegrationError):
    """Raised when BIMAP's SLAI integration configuration is invalid."""

    code = "BIMAP.SLAI.CONFIGURATION"


class SLAIPolicyError(SLAIIntegrationError):
    """Base class for SLAI agent-authorization policy failures."""

    code = "BIMAP.SLAI.POLICY"


class SLAIPolicyValidationError(SLAIPolicyError):
    """Raised when an agent-policy profile cannot be normalized safely."""

    code = "BIMAP.SLAI.POLICY.VALIDATION"


class SLAIUnknownAgentError(SLAIPolicyError):
    """Raised when an agent is not represented by the effective BIMAP policy."""

    code = "BIMAP.SLAI.POLICY.UNKNOWN_AGENT"


class SLAIAgentNotAllowedError(SLAIPolicyError):
    """Raised when BIMAP policy forbids invocation of a known agent."""

    code = "BIMAP.SLAI.POLICY.NOT_ALLOWED"


class SLAIDisabledAgentError(SLAIAgentNotAllowedError):
    """Raised when a policy entry is explicitly disabled."""

    code = "BIMAP.SLAI.POLICY.DISABLED"


# ---------------------------------------------------------------------------
# Job-envelope boundary
# ---------------------------------------------------------------------------


class SLAIEnvelopeError(SLAIIntegrationError):
    """Base class for the internal BIMAP -> SLAI job envelope."""

    code = "BIMAP.SLAI.ENVELOPE"


class SLAIEnvelopeValidationError(SLAIEnvelopeError):
    """Raised when envelope data does not satisfy the integration contract."""

    code = "BIMAP.SLAI.ENVELOPE.VALIDATION"


class SLAIEnvelopeIntegrityError(SLAIEnvelopeError):
    """Raised when an envelope/context digest no longer matches its payload."""

    code = "BIMAP.SLAI.ENVELOPE.INTEGRITY"


class SLAIEnvelopeSerializationError(SLAIEnvelopeError):
    """Raised when the internal SLAI envelope cannot be serialized safely."""

    code = "BIMAP.SLAI.ENVELOPE.SERIALIZATION"


# ---------------------------------------------------------------------------
# Runtime and health
# ---------------------------------------------------------------------------


class SLAIHealthError(SLAIIntegrationError):
    """Base class for SLAI runtime-health failures."""

    code = "BIMAP.SLAI.HEALTH"


class SLAIRuntimeUnavailableError(SLAIHealthError):
    """Raised when the SLAI runtime is unavailable for required BIMAP work."""

    code = "BIMAP.SLAI.HEALTH.RUNTIME_UNAVAILABLE"
    retryable = True


class SLAIAgentHealthError(SLAIHealthError):
    """Raised when a required SLAI agent fails a readiness requirement."""

    code = "BIMAP.SLAI.HEALTH.AGENT"
    retryable = True


class SLAIRuntimeContractError(SLAIIntegrationError):
    """Raised when an injected SLAI runtime object lacks its required surface."""

    code = "BIMAP.SLAI.RUNTIME_CONTRACT"


class SLAITransientRuntimeError(SLAIIntegrationError):
    """Retryable runtime/infrastructure failure at the SLAI boundary."""

    code = "BIMAP.SLAI.RUNTIME_TRANSIENT"
    retryable = True


# ---------------------------------------------------------------------------
# Governance and mapping
# ---------------------------------------------------------------------------


class SLAIGovernanceError(SLAIIntegrationError):
    """Base class for SLAI -> BIMAP governance conversion failures."""

    code = "BIMAP.SLAI.GOVERNANCE"


class SLAIGovernanceValidationError(SLAIGovernanceError):
    """Raised when governance input is incomplete or structurally invalid."""

    code = "BIMAP.SLAI.GOVERNANCE.VALIDATION"


class SLAIGovernanceMappingError(SLAIGovernanceError):
    """Raised when a native SLAI gate decision cannot be mapped safely."""

    code = "BIMAP.SLAI.GOVERNANCE.MAPPING"


class SLAIReleaseBlockedError(SLAIGovernanceError):
    """Raised by callers that require a releasable governance outcome."""

    code = "BIMAP.SLAI.GOVERNANCE.RELEASE_BLOCKED"


class SLAIResultMappingError(SLAIIntegrationError):
    """Reserved shared error for ``slai/result_mapper.py``."""

    code = "BIMAP.SLAI.RESULT_MAPPING"


# ---------------------------------------------------------------------------
# Orchestration boundary (shared by upcoming adapter/orchestrator work)
# ---------------------------------------------------------------------------


class SLAIOrchestrationError(SLAIIntegrationError):
    """Base class for orchestrator failures after policy/envelope validation."""

    code = "BIMAP.SLAI.ORCHESTRATION"


class SLAIAgentInvocationError(SLAIOrchestrationError):
    """Raised when an approved SLAI agent invocation fails."""

    code = "BIMAP.SLAI.ORCHESTRATION.AGENT_INVOCATION"
    retryable = True


__all__ = [
    "SLAIIntegrationError",
    "SLAIConfigurationError",
    "SLAIPolicyError",
    "SLAIPolicyValidationError",
    "SLAIUnknownAgentError",
    "SLAIAgentNotAllowedError",
    "SLAIDisabledAgentError",
    "SLAIEnvelopeError",
    "SLAIEnvelopeValidationError",
    "SLAIEnvelopeIntegrityError",
    "SLAIEnvelopeSerializationError",
    "SLAIHealthError",
    "SLAIRuntimeUnavailableError",
    "SLAIAgentHealthError",
    "SLAIRuntimeContractError",
    "SLAITransientRuntimeError",
    "SLAIGovernanceError",
    "SLAIGovernanceValidationError",
    "SLAIGovernanceMappingError",
    "SLAIReleaseBlockedError",
    "SLAIResultMappingError",
    "SLAIOrchestrationError",
    "SLAIAgentInvocationError",
]