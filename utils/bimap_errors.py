

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


def _safe_context(context: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return a shallow immutable operator-safe diagnostic context."""

    if context is None:
        return MappingProxyType({})

    safe: dict[str, Any] = {}

    for key, value in context.items():
        safe[str(key)] = (
            value
            if value is None or isinstance(value, (bool, int, float, str))
            else f"<{type(value).__name__}>"
        )

    return MappingProxyType(safe)

# ---------------------------------------------------------------------------
# Bootstrap errors
# ---------------------------------------------------------------------------

class BootstrapError(RuntimeError):
    """Base exception for BIMAP composition and lifecycle failures."""

    code = "BIMAP.BOOTSTRAP.ERROR"

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        normalized_message = str(message).strip() or self.__class__.__name__
        normalized_operation = str(operation).strip() or "unknown"

        self.message = normalized_message
        self.operation = normalized_operation
        self.field = (
            None
            if field is None
            else str(field).strip() or None
        )
        self.context = _safe_context(context)
        self.cause = cause

        rendered = (
            f"{normalized_message} "
            f"[operation={normalized_operation}"
        )

        if self.field:
            rendered += f", field={self.field}"

        rendered += "]"

        super().__init__(rendered)

    def to_dict(self) -> dict[str, Any]:
        """Return a bounded machine-readable diagnostic representation."""

        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
            "operation": self.operation,
        }

        if self.field:
            payload["field"] = self.field

        if self.context:
            payload["context"] = dict(self.context)

        if self.cause is not None:
            payload["cause_type"] = type(self.cause).__name__

        return payload


class BootstrapConfigurationError(BootstrapError):
    """Raised when bootstrap inputs are structurally invalid."""

    code = "BIMAP.BOOTSTRAP.CONFIGURATION"


class BootstrapCompositionError(BootstrapError):
    """Raised when the runtime graph cannot be composed."""

    code = "BIMAP.BOOTSTRAP.COMPOSITION"


class BootstrapStateError(BootstrapError):
    """Raised when a lifecycle operation is invalid for the current state."""

    code = "BIMAP.BOOTSTRAP.STATE"


class BootstrapShutdownError(BootstrapError):
    """Raised when BIMAP-owned runtime resources cannot close cleanly."""

    code = "BIMAP.BOOTSTRAP.SHUTDOWN"


__all__ = [
    "BootstrapError",
    "BootstrapConfigurationError",
    "BootstrapCompositionError",
    "BootstrapStateError",
    "BootstrapShutdownError",
]