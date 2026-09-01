"""
BIMAP policy boundary for invoking SLAI agents.

This module answers one question only: *which SLAI agent capabilities may BIMAP
invoke?*  Runtime construction belongs to ``slai/orchestration.py`` and YAML
loading belongs to ``bootstrap.py``.  Keeping those concerns separate prevents
policy from importing the factory/orchestrator and avoids a circular dependency.

The built-in baseline is derived from the BIMAP implementation report's launch
assignment: core agents are enabled by default, Perception is conditional,
Execution is supporting/on-demand, Learning and Adaptive are deferred, and QNN
is disabled.  Unlisted SLAI agents are denied unless they are explicitly added
to the supplied policy profile.

Expected profile shape
----------------------
``bootstrap.py`` may pass a mapping such as::

    {
        "agents": {
            "reader": {"tier": "core", "enabled": True, "required": True},
            "perception": {"tier": "conditional", "enabled": True},
            "execution": {"tier": "supporting", "enabled": True},
            "learning": {"tier": "deferred", "enabled": False},
        },
    }

The mapping overlays the report-derived baseline.  Unknown/unlisted agents remain
fail-closed; expanding the product agent set requires an explicit policy entry.
This module deliberately does not read ``configs/slai_profile.yaml`` itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterator

from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("SLAI Agent Policy")
printer = PrettyPrinter()


class AgentTier(str, Enum):
    """BIMAP launch/control classification for an SLAI agent."""

    CORE = "core"
    CONDITIONAL = "conditional"
    SUPPORTING = "supporting"
    DEFERRED = "deferred"
    DISABLED = "disabled"


_HARD_DISABLED_AGENTS = frozenset({"qnn"})


@dataclass(frozen=True, slots=True)
class AgentPolicyEntry:
    """Immutable effective policy for one normalized SLAI agent key."""

    name: str
    tier: AgentTier
    enabled: bool
    required: bool = False

    def __post_init__(self) -> None:
        normalized_name = normalize_agent_name(self.name, field="name")
        if not isinstance(self.tier, AgentTier):
            tier = parse_enum(AgentTier, self.tier, field="tier")
        else:
            tier = self.tier
        enabled = require_bool(self.enabled, field="enabled")
        required = require_bool(self.required, field="required")

        if tier is AgentTier.DISABLED and enabled:
            raise SLAIPolicyValidationError(
                "An agent classified as disabled cannot also be enabled.",
                component="agent_policy",
                operation="validate_entry",
                field="enabled",
                context={"agent": normalized_name, "tier": tier.value},
            )
        if required and not enabled:
            raise SLAIPolicyValidationError(
                "A required SLAI agent must be enabled.",
                component="agent_policy",
                operation="validate_entry",
                field="required",
                context={"agent": normalized_name, "tier": tier.value},
            )
        if required and tier in {AgentTier.DEFERRED, AgentTier.DISABLED}:
            raise SLAIPolicyValidationError(
                "Deferred or disabled agents cannot be mandatory launch dependencies.",
                component="agent_policy",
                operation="validate_entry",
                field="required",
                context={"agent": normalized_name, "tier": tier.value},
            )
        if normalized_name in _HARD_DISABLED_AGENTS and (
            enabled or tier is not AgentTier.DISABLED
        ):
            raise SLAIPolicyValidationError(
                "This SLAI capability is hard-disabled for the BIMAP product profile.",
                component="agent_policy",
                operation="validate_entry",
                field="tier",
                context={"agent": normalized_name},
            )

        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "tier", tier)
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "required", required)

    @property
    def allowed(self) -> bool:
        """Return whether BIMAP may explicitly request this agent."""

        return self.enabled and self.tier is not AgentTier.DISABLED

    @property
    def default_invocation(self) -> bool:
        """Return whether the agent belongs to the normal default invocation set."""

        return self.allowed and self.tier is AgentTier.CORE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier.value,
            "enabled": self.enabled,
            "required": self.required,
            "allowed": self.allowed,
            "default_invocation": self.default_invocation,
        }


# Report-derived launch baseline.  This is authorization policy only; runtime
# availability is checked separately by ``health.py`` and the SLAI factory.
_BASELINE_ENTRIES: tuple[AgentPolicyEntry, ...] = (
    AgentPolicyEntry("collaborative", AgentTier.CORE, True, True),
    AgentPolicyEntry("evaluation", AgentTier.CORE, True, True),
    AgentPolicyEntry("reader", AgentTier.CORE, True, True),
    AgentPolicyEntry("knowledge", AgentTier.CORE, True, True),
    AgentPolicyEntry("language", AgentTier.CORE, True, True),
    AgentPolicyEntry("observability", AgentTier.CORE, True, True),
    AgentPolicyEntry("planning", AgentTier.CORE, True, True),
    AgentPolicyEntry("privacy", AgentTier.CORE, True, True),
    AgentPolicyEntry("quality", AgentTier.CORE, True, True),
    AgentPolicyEntry("reasoning", AgentTier.CORE, True, True),
    AgentPolicyEntry("safety", AgentTier.CORE, True, True),
    AgentPolicyEntry("perception", AgentTier.CONDITIONAL, True, False),
    AgentPolicyEntry("execution", AgentTier.SUPPORTING, True, False),
    AgentPolicyEntry("learning", AgentTier.DEFERRED, False, False),
    AgentPolicyEntry("adaptive", AgentTier.DEFERRED, False, False),
    AgentPolicyEntry("qnn", AgentTier.DISABLED, False, False),
)


class SLAIAgentPolicy:
    """Validated, fail-closed policy for BIMAP -> SLAI agent invocation."""

    __slots__ = ("_entries",)

    def __init__(self, profile: Mapping[str, Any] | None = None) -> None:
        announce_method_start(printer, logger, "SLAI POLICY", "Initializing BIMAP SLAI agent policy")

        entries = {entry.name: entry for entry in _BASELINE_ENTRIES}
        if profile is not None:
            parsed_profile = require_mapping(
                profile,
                field="slai_profile",
                error_type=SLAIPolicyValidationError,
            )
            allowed_top_level = {"agents"}
            unexpected = sorted(set(parsed_profile) - allowed_top_level)
            if unexpected:
                raise SLAIPolicyValidationError(
                    "SLAI policy profile contains unsupported top-level fields.",
                    component="agent_policy",
                    operation="load_profile",
                    context={"unexpected_fields": unexpected},
                )

            raw_agents = parsed_profile.get("agents", {})
            agent_overrides = require_mapping(
                raw_agents,
                field="agents",
                error_type=SLAIPolicyValidationError,
            )
            for raw_name, raw_entry in agent_overrides.items():
                name = normalize_agent_name(raw_name, field="agents.key")
                base = entries.get(name)
                entries[name] = self._parse_entry(name, raw_entry, base=base)

        self._entries = MappingProxyType(entries)

        logger.info(
            "BIMAP SLAI agent policy initialized: entries=%d required=%d defaults=%d",
            len(self._entries),
            len(self.required_agents()),
            len(self.default_agents()),
        )

    @staticmethod
    def _parse_entry(name: str, raw_entry: Any, *, base: AgentPolicyEntry | None) -> AgentPolicyEntry:
        announce_method_start(printer, logger, "SLAI POLICY", f"Normalizing agent policy entry: {name}")

        if isinstance(raw_entry, bool):
            if base is None:
                tier = AgentTier.SUPPORTING
                required = False
            else:
                tier = base.tier
                required = base.required if raw_entry else False
            return AgentPolicyEntry(
                name=name,
                tier=tier,
                enabled=raw_entry,
                required=required,
            )

        data = require_mapping(
            raw_entry,
            field=f"agents.{name}",
            error_type=SLAIPolicyValidationError,
        )
        allowed_fields = {"tier", "enabled", "required"}
        unexpected = sorted(set(data) - allowed_fields)
        if unexpected:
            raise SLAIPolicyValidationError(
                "Agent policy entry contains unsupported fields.",
                component="agent_policy",
                operation="parse_entry",
                field=f"agents.{name}",
                context={"agent": name, "unexpected_fields": unexpected},
            )

        if base is None:
            tier = parse_enum(AgentTier, data.get("tier", AgentTier.SUPPORTING.value), field=f"agents.{name}.tier")
            enabled = require_bool(data.get("enabled", False), field=f"agents.{name}.enabled")
            required = require_bool(data.get("required", False), field=f"agents.{name}.required")
        else:
            tier = parse_enum(AgentTier, data.get("tier", base.tier.value), field=f"agents.{name}.tier")
            enabled = require_bool( data.get("enabled", base.enabled), field=f"agents.{name}.enabled")
            required = require_bool(data.get("required", base.required if enabled else False), field=f"agents.{name}.required")

        return AgentPolicyEntry(
            name=name,
            tier=tier,
            enabled=enabled,
            required=required,
        )

    @classmethod
    def from_mapping(cls, profile: Mapping[str, Any]) -> "SLAIAgentPolicy":
        """Construct a policy from a bootstrap-supplied profile mapping."""

        announce_method_start(printer, logger, "SLAI POLICY", "Constructing SLAI agent policy from mapping")
        return cls(profile)

    def entry(self, agent: str) -> AgentPolicyEntry:
        """Return the effective policy entry for ``agent`` or fail closed."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Resolving SLAI agent policy entry",
            context={"agent": agent},
        )
        name = normalize_agent_name(agent)
        entry = self._entries.get(name)
        if entry is not None:
            return entry
        raise SLAIUnknownAgentError(
            "SLAI agent is not represented by the BIMAP policy.",
            component="agent_policy",
            operation="entry",
            field="agent",
            context={"agent": name},
        )

    def allows(self, agent: str) -> bool:
        """Return whether BIMAP policy currently permits an agent invocation."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Checking whether SLAI agent is allowed",
            context={"agent": agent},
        )
        try:
            return self.entry(agent).allowed
        except SLAIUnknownAgentError:
            return False

    def require_allowed(self, agent: str) -> AgentPolicyEntry:
        """Return the policy entry or raise a structured authorization error."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Enforcing SLAI agent authorization",
            context={"agent": agent},
        )
        entry = self.entry(agent)
        if not entry.enabled:
            raise SLAIDisabledAgentError(
                "SLAI agent is disabled by BIMAP policy.",
                component="agent_policy",
                operation="require_allowed",
                field="agent",
                context={"agent": entry.name, "tier": entry.tier.value},
            )
        if not entry.allowed:
            raise SLAIAgentNotAllowedError(
                "SLAI agent invocation is not permitted by BIMAP policy.",
                component="agent_policy",
                operation="require_allowed",
                field="agent",
                context={"agent": entry.name, "tier": entry.tier.value},
            )
        return entry

    def default_agents(self) -> tuple[str, ...]:
        """Return enabled core agents invoked when no explicit list is supplied."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Resolving default SLAI agent set",
        )
        return tuple(
            entry.name
            for entry in self._entries.values()
            if entry.default_invocation
        )

    def required_agents(self) -> tuple[str, ...]:
        """Return enabled agents whose absence makes the BIMAP SLAI profile incomplete."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Resolving required SLAI agent set",
        )
        return tuple(
            entry.name
            for entry in self._entries.values()
            if entry.required and entry.allowed
        )

    def agents_by_tier(self, tier: AgentTier | str) -> tuple[str, ...]:
        """Return policy entries in one launch/control tier."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Resolving SLAI agents by policy tier",
        )
        parsed = parse_enum(AgentTier, tier, field="tier")
        return tuple(
            entry.name
            for entry in self._entries.values()
            if entry.tier is parsed
        )

    def resolve_requested_agents(self, requested: Any = None) -> tuple[str, ...]:
        """Resolve and authorize an explicit request or the default core set.

        ``None`` selects the enabled core agents.  Conditional/supporting agents
        are therefore opt-in.  Deferred/disabled agents remain unavailable
        unless the effective profile explicitly enables/reclassifies them.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Resolving requested SLAI agents",
        )

        if requested is None:
            resolved = self.default_agents()
        else:
            resolved = normalize_agent_sequence(requested, field="requested_agents")
            if not resolved:
                raise SLAIPolicyValidationError(
                    "An explicit requested-agent list must not be empty.",
                    component="agent_policy",
                    operation="resolve_requested_agents",
                    field="requested_agents",
                )

        for name in resolved:
            self.require_allowed(name)

        logger.debug(
            {
                "event": "bimap_slai_agents_resolved",
                "agent_count": len(resolved),
                "agents": list(resolved),
            }
        )
        return resolved

    def snapshot(self) -> dict[str, Any]:
        """Return the complete effective policy without runtime state."""

        announce_method_start(
            printer,
            logger,
            "SLAI POLICY",
            "Creating SLAI agent policy snapshot",
        )
        return {
            "agents": {
                name: entry.to_dict()
                for name, entry in self._entries.items()
            },
            "default_agents": list(self.default_agents()),
            "required_agents": list(self.required_agents()),
        }

    def __contains__(self, agent: object) -> bool:
        if not isinstance(agent, str):
            return False
        try:
            name = normalize_agent_name(agent)
        except SLAIPolicyValidationError:
            return False
        return name in self._entries

    def __iter__(self) -> Iterator[AgentPolicyEntry]:
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "AgentTier",
    "AgentPolicyEntry",
    "SLAIAgentPolicy",
]