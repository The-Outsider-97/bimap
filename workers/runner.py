"""
Worker-process entry/orchestrator.
It must not import bootstrap. Instead:

bootstrap
    ↓
runner

runner
    ✕
bootstrap
"""

from __future__ import annotations

from .utils.workers_errors import *
from .utils.workers_helpers import *
from .jobs.audit import *
from .jobs.deletion import *
from .jobs.report import *
from .jobs.retention import *
from .engine import *
from .reports import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Runner")
printer = PrettyPrinter()


class Runner:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Runner successfully initialized")
        pass

__all__ = ["Runner"]