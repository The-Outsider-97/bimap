"""
Represents governance-review state and decisions attached to findings.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from ..findings.models import *
from .decisions import *
from logs.logger import get_logger, PrettyPrinter  # pyright: ignore[reportMissingImports]

logger = get_logger("BIMAP Review")
printer = PrettyPrinter()


class Review:
    def __init__(self) -> None:
        logger.info(f"Initializing Review...")
        pass


__all__ =["Review"]