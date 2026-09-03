"""
Executes the create-order use case.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.order_service import *
from ...contracts.order import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Create Order")
printer = PrettyPrinter()


class CreateOrder:
    def __init__(self) -> None:
        logger(f"BIMAP Create Order successfully initialized")
        pass

__all__ = ["CreateOrder"]

