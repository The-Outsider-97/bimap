"""
Pure domain representation of evidence/requirement coverage statistics.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Report Coverage")
printer = PrettyPrinter()

class ReportCoverage:
    def __init__(self) -> None:
        logger(f"BIMAP Report Coverage successfully initialized")
        pass

__all__ = ["ReportCoverage"]
