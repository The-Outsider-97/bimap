"""
Version identity for BIMAP's Combined Audit correlation layer.

The Combined Audit version is intentionally distinct from external contract
schema versions, deterministic rule versions, and the BIMAP application version.
A behavioral change that can alter cross-scope relationships or findings should
therefore receive a new Combined Audit version.

No current-version constant is hard-coded here because the repository does not
yet establish one authoritatively. Composition/bootstrap must select it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..utils.engine_errors import EngineValidationError
from ..utils.engine_helpers import announce_engine_action, require_engine_text
from logs.logger import PrettyPrinter, get_logger  # type: ignore

logger = get_logger("BIMAP Combined Audit Versions")
printer = PrettyPrinter()

_COMPONENT = "combined_versions"
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _validate_component(value: Any, *, field: str) -> int:
    """Validate one non-negative integer version component."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EngineValidationError(
            "Combined Audit version components must be integers.",
            component=_COMPONENT,
            operation="validate_version",
            field=field,
            context={"received_type": type(value).__name__},
        )
    if value < 0:
        raise EngineValidationError(
            "Combined Audit version components must be non-negative.",
            component=_COMPONENT,
            operation="validate_version",
            field=field,
            context={"received": value},
        )
    return value


@dataclass(frozen=True, order=True, slots=True)
class AuditVersion:
    """Immutable ``major.minor.patch`` identity for Combined Audit behavior."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating Combined Audit version",
            event="combined_version_validate_start",
        )
        object.__setattr__(self, "major", _validate_component(self.major, field="major"))
        object.__setattr__(self, "minor", _validate_component(self.minor, field="minor"))
        object.__setattr__(self, "patch", _validate_component(self.patch, field="patch"))

    @classmethod
    def parse(
        cls,
        value: str | "AuditVersion",
        *,
        field: str = "combined_audit_version",
    ) -> "AuditVersion":
        """Parse a canonical three-component Combined Audit version."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Parsing Combined Audit version",
            event="combined_version_parse_start",
        )
        if isinstance(value, cls):
            return value
        text = require_engine_text(value, field=field, error_type=EngineValidationError)
        match = _VERSION_RE.fullmatch(text)
        if match is None:
            raise EngineValidationError(
                "Combined Audit version must use canonical major.minor.patch numeric form.",
                component=_COMPONENT,
                operation="parse",
                field=field,
                context={"received": text},
            )
        return cls(*(int(part) for part in match.groups()))

    def same_major(self, other: str | "AuditVersion") -> bool:
        """Return whether another Combined Audit version shares this major version."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Comparing Combined Audit major versions",
            event="combined_version_same_major_start",
        )
        return self.major == type(self).parse(other).major

    def to_tuple(self) -> tuple[int, int, int]:
        """Return ``(major, minor, patch)`` for deterministic comparisons."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing Combined Audit version tuple",
            event="combined_version_to_tuple_start",
        )
        return self.major, self.minor, self.patch

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


CombinedAuditVersion = AuditVersion

__all__ = ["AuditVersion", "CombinedAuditVersion"]


if __name__ == "__main__":
    print("\n=== Running Combined Audit Versions Self-Test ===\n")
    printer.status("TEST", "Combined Audit versions module initialized", "info")
    version = AuditVersion.parse("1.2.3")
    assert version.to_tuple() == (1, 2, 3)
    assert version.same_major("1.9.0")
    assert str(version) == "1.2.3"
    printer.status("PASS", "Combined Audit version identity", "success")
    print("\n=== Test ran successfully ===\n")