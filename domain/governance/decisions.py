"""
Canonical governance outcomes, such as approved, suppressed, review-required or blocked.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("Decisions")
printer = PrettyPrinter()


class Decisions:
    def __init__(self) -> None:
        logger.info(f"Initializing Decisions...")
        pass

__all__ = ["Decisions"]