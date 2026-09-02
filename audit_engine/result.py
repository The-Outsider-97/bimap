"""
Canonical aggregate returned by the deterministic audit engine.
"""

from __future__ import annotations

from .utils.engine_errors import *
from .utils.engine_helpers import *
from ..domain.findings.models import *
from ..domain.evidence.models import *
from ..domain.reports.coverage import *
# It should also import governance metadata, but not SLAI orchestration itself
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Audit Result")
printer = PrettyPrinter()


class AuditResult:
    def __init__(self) -> None:
        logger(f"Audit Result successfully initialized")
        pass

__all__ = ["AuditResult"]

