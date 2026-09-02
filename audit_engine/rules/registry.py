"""
Registry through which deterministic rules are registered/discovered by product auditors.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from .base import *
from .versions import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Rules Registry")
printer = PrettyPrinter()


class RulesRegistry:
    def __init__(self) -> None:
        logger(f"Rules Registry successfully initialized")
        pass

__all__ = ["RulesRegistry"]