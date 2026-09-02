"""
Version identities for BIMAP deterministic audit rules.

Rule versions are intentionally independent from external contract schema
versions (``contracts/versions.py``) and from the BIMAP application version.
A rule revision identifies customer-facing audit logic: changing a threshold,
comparison, applicability condition, expected value, or other behavior that can
change a deterministic rule result requires a traceable rule-version change.

This module is a leaf within ``audit_engine.rules``.  It depends only on the
shared audit-engine errors/helpers and the common logging surface; it imports no
concrete rule, registry, executor, product auditor, reporting code, or SLAI
runtime.  Keeping version identity below the registry prevents circular imports
and allows historical rule revisions to coexist safely.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from typing import Any

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Rules Versions")
printer = PrettyPrinter()

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _validate_component(value: Any, *, field: str) -> int:
    """Validate one non-negative integer version component."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EngineValidationError(
            "Rule-version components must be integers.",
            component="rules_versions",
            operation="validate_version",
            field=field,
            context={"received_type": type(value).__name__},
        )
    if value < 0:
        raise EngineValidationError(
            "Rule-version components must be non-negative.",
            component="rules_versions",
            operation="validate_version",
            field=field,
            context={"received": value},
        )
    return value


@dataclass(frozen=True, order=True, slots=True)
class VersionRule:
    """Immutable numeric version of one deterministic BIMAP rule.

    BIMAP currently uses the same canonical three-component numeric shape as
    its external contract versions (``major.minor.patch``), while keeping the
    two version domains semantically independent.  Compatibility is never
    inferred from the major component alone: the rule registry explicitly
    controls which revisions remain registered/executable.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Validating deterministic rule version",
            event="rule_version_validate_start",
        )
        object.__setattr__(self, "major", _validate_component(self.major, field="major"))
        object.__setattr__(self, "minor", _validate_component(self.minor, field="minor"))
        object.__setattr__(self, "patch", _validate_component(self.patch, field="patch"))

    @classmethod
    def parse(
        cls,
        value: str | "VersionRule",
        *,
        field: str = "rule_version",
    ) -> "VersionRule":
        """Parse a canonical ``major.minor.patch`` rule version."""
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Parsing deterministic rule version",
            event="rule_version_parse_start",
        )
        if isinstance(value, cls):
            return value

        text = require_engine_text(value, field=field, error_type=EngineValidationError)
        match = _VERSION_RE.fullmatch(text)
        if match is None:
            raise EngineValidationError(
                "Rule version must use canonical major.minor.patch numeric form.",
                component="rules_versions",
                operation="parse",
                field=field,
                context={"received": text},
            )

        return cls(*(int(part) for part in match.groups()))

    def same_major(self, other: str | "VersionRule") -> bool:
        """Return whether another rule version shares this major component."""
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Comparing deterministic rule major versions",
            event="rule_version_same_major_start",
        )
        candidate = type(self).parse(other)
        return self.major == candidate.major

    def to_tuple(self) -> tuple[int, int, int]:
        """Return ``(major, minor, patch)`` for deterministic comparisons."""
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Serializing deterministic rule version tuple",
            event="rule_version_to_tuple_start",
        )
        return self.major, self.minor, self.patch

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


# Natural public spelling for new code while preserving the starter module's
# original ``VersionRule`` name.  This is a true alias, not a second type or a
# duplicate implementation.
RuleVersion = VersionRule


@dataclass(frozen=True, slots=True)
class RuleVersionRecord:
    """Immutable version metadata for one registered rule identifier."""

    rule_id: str
    current: VersionRule
    supported: tuple[VersionRule | str, ...]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Validating deterministic rule version record",
            event="rule_version_record_validate_start",
        )
        rule_id = require_engine_text(
            self.rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        current = VersionRule.parse(self.current)

        try:
            raw_supported = tuple(self.supported)
        except TypeError as exc:
            raise EngineValidationError(
                "Supported rule versions must be iterable.",
                component="rules_versions",
                operation="validate_record",
                field="supported",
                context={"received_type": type(self.supported).__name__},
                cause=exc,
            ) from exc

        if not raw_supported:
            raise EngineIntegrityError(
                "A rule-version record must contain at least one supported revision.",
                component="rules_versions",
                operation="validate_record",
                field="supported",
                context={"rule_id": rule_id},
            )

        normalized = tuple(VersionRule.parse(item) for item in raw_supported)
        if len(set(normalized)) != len(normalized):
            raise EngineIntegrityError(
                "A rule-version record contains duplicate supported revisions.",
                component="rules_versions",
                operation="validate_record",
                field="supported",
                context={"rule_id": rule_id},
            )

        supported = tuple(sorted(normalized))
        if current not in supported:
            raise EngineIntegrityError(
                "Current rule version must also be registered as supported.",
                component="rules_versions",
                operation="validate_record",
                field="current",
                context={"rule_id": rule_id, "current": str(current)},
            )

        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "current", current)
        object.__setattr__(self, "supported", supported)

    def supports(self, version: str | VersionRule) -> bool:
        """Return whether an exact rule revision is registered/supported."""
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Checking deterministic rule-version support",
            event="rule_version_record_supports_start",
            context={"rule_id": self.rule_id},
        )
        return VersionRule.parse(version) in self.supported

    def require_supported(self, version: str | VersionRule) -> VersionRule:
        """Return an exact supported revision or fail closed."""
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Requiring supported deterministic rule version",
            event="rule_version_record_require_supported_start",
            context={"rule_id": self.rule_id},
        )
        candidate = VersionRule.parse(version)
        if candidate not in self.supported:
            raise EngineValidationError(
                "Requested deterministic rule version is not registered.",
                component="rules_versions",
                operation="require_supported",
                field="rule_version",
                context={
                    "rule_id": self.rule_id,
                    "requested": str(candidate),
                    "supported": tuple(str(item) for item in self.supported),
                },
            )
        return candidate

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic primitive version metadata."""
        announce_engine_action(
            printer,
            logger,
            component="rules_versions",
            action="Serializing deterministic rule version record",
            event="rule_version_record_to_dict_start",
            context={"rule_id": self.rule_id},
        )
        return {
            "rule_id": self.rule_id,
            "current": str(self.current),
            "supported": [str(item) for item in self.supported],
        }


__all__ = [
    "VersionRule",
    "RuleVersion",
    "RuleVersionRecord",
]