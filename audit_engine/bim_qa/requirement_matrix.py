"""
Builds and evaluates the requirement-to-evidence matrix.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.requirements.models import *
from ...domain.evidence.models import *
from ...domain.findings.models import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Requirement matrix")
printer = PrettyPrinter()


class RequirementMatrix:
    def __init__(self) -> None:
        logger(f"Requirement matrix successfully initialized")
        pass

__all__ = ["RequirementMatrix"]
