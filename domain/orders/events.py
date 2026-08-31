"""
Defines domain events emitted by lifecycle changes.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .states import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Orders Events")
printer = PrettyPrinter()

class Events:
    def __init__(self) -> None:
        logger(f"Orders Events  successfully initialized")
        pass

__all__ = ["Events"]
