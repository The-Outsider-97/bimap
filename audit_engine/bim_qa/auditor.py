"""
Coordinates deterministic BIM QA evaluation.
This module is responsible for orchestrating the evaluation of BIM evidence against a set of deterministic rules, producing findings and audit reports.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.findings.models import *
from ..rules.registry import *
from ..rules.executor import *
from ..context import *
from .requirement_matrix import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIM QA Auditor")
printer = PrettyPrinter()


class BIMAuditor:
    def __init__(self) -> None:
        logger(f"BIM QA Auditor successfully initialized")
        pass

__all__ = ["BIMAuditor"]
