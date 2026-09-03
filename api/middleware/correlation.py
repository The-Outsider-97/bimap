"""
Assigns/propagates request and correlation IDs.
"""

from __future__ import annotations

from ..utils.api_errors import *
from ..utils.api_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Middleware")
printer = PrettyPrinter()


class Middleware:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Middleware successfully initialized")
        pass

__all__ = ["Middleware"]