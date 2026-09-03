"""
Enforces request/body/rate limits.
"""

from __future__ import annotations

from ..utils.api_errors import *
from ..utils.api_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Limit Request")
printer = PrettyPrinter()


class RequestLimits:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Limit Request successfully initialized")
        pass

__all__ = ["RequestLimits"]