"""
HTTP-facing security headers/request security context.
"""

from __future__ import annotations

from ..utils.api_errors import *
from ..utils.api_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Security")
printer = PrettyPrinter()


class Security:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Security successfully initialized")
        pass

__all__ = ["Security"]