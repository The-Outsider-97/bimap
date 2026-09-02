"""
Calculates measurable requirement/evidence coverage.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.evidence.models import *
from ...domain.requirements.models import *
from ...domain.findings.models import *
from ...domain.reports.coverage import ReportCoverage # It recieves and uses the report
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Validation Coverage")
printer = PrettyPrinter()


class ValidationCoverage:
    def __init__(self) -> None:
        logger(f"Validation Coverage successfully initialized")
        pass

__all__ = ["ValidationCoverage"]

