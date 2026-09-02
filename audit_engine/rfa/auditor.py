"""
Product-specific Family Audit coordinator.
The hierarchy of the Family Audit coordinator is as follows:
rules/base
    ↓
rules/registry
    ↓
rules/executor
    ↓
rfa/auditor

"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.findings.models import *
from ...domain.evidence.models import *
from ..rules.registry import *
from ..rules.executor import *
from ..context import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIM QA Auditor")
printer = PrettyPrinter()


class BIMAuditor:
    def __init__(self) -> None:
        logger(f"BIM QA Auditor successfully initialized")
        pass

__all__ = ["BIMAuditor"]
