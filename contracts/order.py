"""
API-safe/order-persistence contract representation.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from ..domain.orders.states import *
from ..domain.products.models import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts Orders")
printer = PrettyPrinter()


class ContractsOrders:
    def __init__(self) -> None:
        logger(f"Contracts Orders successfully initialized")
        pass

__all__ = ["ContractsOrders"]


