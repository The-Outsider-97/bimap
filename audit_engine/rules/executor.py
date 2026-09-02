"""
Base deterministic audit-rule contract/protocol.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.findings.models import *
from ..context import *
from .registry import *
from .base import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Rules Executor")
printer = PrettyPrinter()


class RulesExecutor:
    def __init__(self) -> None:
        logger(f"Rules Executor Rule successfully initialized")
        pass

__all__ = ["RulesExecutor"]