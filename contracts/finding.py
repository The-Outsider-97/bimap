"""
Externally serializable finding representation.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from ..domain.findings.severity import *
from ..domain.findings.confidence import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts Findings")
printer = PrettyPrinter()


class ContractsFindings:
    def __init__(self) -> None:
        logger(f"Contracts Findings successfully initialized")
        pass

__all__ = ["ContractsFindings"]

