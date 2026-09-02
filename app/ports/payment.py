"""
Interface between BIMAP and a payment provider.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Payment")
printer = PrettyPrinter()


class Payment: # contract-level payment metadata later.
    def __init__(self) -> None:
        logger(f"BIMAP Payment successfully initialized")
        pass

__all__ = ["Payment"]

