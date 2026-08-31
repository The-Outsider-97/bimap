"""
Versioned external project-evidence interchange contract.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from .evidence import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Project Evidence")
printer = PrettyPrinter()


class ProjectEvidence:
    def __init__(self) -> None:
        logger(f"Project Evidence successfully initialized")
        pass

__all__ = ["ProjectEvidence"]

