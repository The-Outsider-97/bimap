"""
Versioned external Family Evidence contract used by future Revit extraction and BIMAP ingestion.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from .evidence import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts Family Evidence")
printer = PrettyPrinter()


class FamilyEvidence:
    def __init__(self) -> None:
        logger(f"Family Evidence successfully initialized")
        pass

__all__ = ["FamilyEvidence"]
