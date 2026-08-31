"""
Single authoritative definition of order lifecycle states.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Orders States")
printer = PrettyPrinter()

class States:
    def __init__(self) -> None:
        logger(f"Orders States  successfully initialized")
        pass

__all__ = ["States"]