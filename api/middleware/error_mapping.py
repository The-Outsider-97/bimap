"""
Maps known domain/application exceptions into safe HTTP errors.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...domain.orders.transitions import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Error Mapping")
printer = PrettyPrinter()


class ErrorMapping:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Error Mapping successfully initialized")
        pass

__all__ = ["ErrorMapping"]