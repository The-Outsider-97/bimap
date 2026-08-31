"""
External requirement representation.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from ..domain.requirements.models import * # For stable enums
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts Requirement")
printer = PrettyPrinter()


class ContractsRequirement:
    def __init__(self) -> None:
        logger(f"Contracts Requirement successfully initialized")
        pass

__all__ = ["ContractsRequirement"]


