"""
Central authority for valid transitions between order states.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .models import *
from .states import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Transitions")
printer = PrettyPrinter()

class Transitions:
    def __init__(self) -> None:
        logger(f"Transitions successfully initialized")
        pass

__all__ = ["Transitions"]