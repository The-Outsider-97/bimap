"""
Version identifier for the Combined Audit correlation/evidence-graph algorithm.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Audit Engine Version")
printer = PrettyPrinter()


class AuditVersion:
    def __init__(self) -> None:
        logger(f"Audit Engine Version successfully initialized")
        pass

__all__ = ["AuditVersion"]

