"""
Abstracts current time for deterministic tests and expiry/retention logic.
"""

from __future__ import annotations

from .utils.app_errors import *
from .utils.app_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Clock")
printer = PrettyPrinter()


class Clock:
    def __init__(self) -> None:
        logger(f"Clock successfully initialized")
        pass

__all__ = ["Clock"]

