"""
Public package surface for BIMAP's SLAI integration layer.

Exports are resolved lazily so importing a specific SLAI submodule does not
eagerly initialize orchestration, AgentFactory, SharedMemory, or unrelated
integration components.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Governance
    "GovernanceGate": (".governance", "GovernanceGate"),
    "GateDisposition": (".governance", "GateDisposition"),
    "SLAIGateResult": (".governance", "SLAIGateResult"),
    "SLAIGovernanceResult": (".governance", "SLAIGovernanceResult"),
    "SLAIGovernance": (".governance", "SLAIGovernance"),

    # Agent policy
    "AgentTier": (".agent_policy", "AgentTier"),
    "AgentPolicyEntry": (".agent_policy", "AgentPolicyEntry"),
    "SLAIAgentPolicy": (".agent_policy", "SLAIAgentPolicy"),

    # Health
    "HealthMode": (".health", "HealthMode"),
    "HealthState": (".health", "HealthState"),
    "ComponentHealth": (".health", "ComponentHealth"),
    "SLAIHealthReport": (".health", "SLAIHealthReport"),
    "SLAIHealthCheck": (".health", "SLAIHealthCheck"),

    # Job envelope
    "SLAIJobEnvelope": (".job_envelope", "SLAIJobEnvelope"),

    # Orchestration
    "OrchestrationPhase": (".orchestration", "OrchestrationPhase"),
    "AgentInvocationRecord": (
        ".orchestration",
        "AgentInvocationRecord",
    ),
    "SLAIOrchestrationResult": (
        ".orchestration",
        "SLAIOrchestrationResult",
    ),
    "AgentTaskBuilder": (".orchestration", "AgentTaskBuilder"),
    "SLAIOrchestrator": (".orchestration", "SLAIOrchestrator"),

    # Result mapping
    "MappedAgentOutput": (".result_mapper", "MappedAgentOutput"),
    "SLAIMappedResult": (".result_mapper", "SLAIMappedResult"),
    "SLAIResultMapper": (".result_mapper", "SLAIResultMapper"),

    # Public façade
    "SLAIAdapter": (".adapter", "SLAIAdapter"),
}


__all__ = list(_LAZY_EXPORTS) # type: ignore


def __getattr__(name: str) -> Any:
    """Resolve one public SLAI integration symbol lazily."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        )

    module_name, attribute_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)

    # Cache the resolved value so subsequent access has no import lookup cost.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy public symbols to introspection tools."""
    return sorted(set(globals()) | set(__all__))
