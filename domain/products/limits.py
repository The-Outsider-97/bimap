"""
Defines limits and constraints associated with each product.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .models import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Limits")
printer = PrettyPrinter()

class Limits:
    def __init__(self) -> None:
        logger(f"Product Limits successfully initialized")
        pass

__all__ = ["Limits"]
