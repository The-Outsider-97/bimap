"""
Serializes requirement matrix data.
"""

from __future__ import annotations

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...domain.findings.models import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Requirement Matrix")
printer = PrettyPrinter()


class RequirementMatrix:
    def __init__(self) -> None:
        logger(f"Requirement Matrix successfully initialized")
        pass

__all__ = ["RequirementMatrix"]