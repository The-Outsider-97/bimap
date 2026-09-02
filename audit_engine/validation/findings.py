"""
Validates generated findings against BIMAP's finding contract and evidence requirements.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.findings.models import *
from ...domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Coverage Findings")
printer = PrettyPrinter()


class CoverageFindings:
    def __init__(self) -> None:
        logger(f"Coverage Findings successfully initialized")
        pass

__all__ = ["CoverageFindings"]