

from typing import Any, Mapping

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Helpers")
printer = PrettyPrinter()


_COMPONENT = "bootstrap"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _announce(action: str, *, event: str, context: Mapping[str, Any] | None = None) -> None:
    """
    Emit one bounded method-start diagnostic.

    Customer evidence, report content, access tokens, credentials, and raw
    payloads must never be supplied in ``context``.
    """

    printer.status("BOOTSTRAP", action, "info")

    payload: dict[str, Any] = {
        "event": event,
        "component": _COMPONENT,
        "action": action,
    }

    if context:
        payload["context"] = dict(context)

    logger.debug(payload)


__all__ = ["_announce"]