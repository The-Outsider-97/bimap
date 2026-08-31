"""
Defines the BIMAP product types: Family Audit, BIM QA and Combined Audit, plus product metadata.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Product models")
printer = PrettyPrinter()

class ProductModels:
    def __init__(self) -> None:
        logger(f"Product models successfully initialized")
        pass

__all__ = ["ProductModels"]
