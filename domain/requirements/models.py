"""
Canonical audit requirement, requirement source and requirement-status representations.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Requirement Models")
printer = PrettyPrinter()

class RequirementModels:
    def __init__(self) -> None:
        logger(f"Requirement Models successfully initialized")
        pass

__all__ = ["RequirementModels"]
