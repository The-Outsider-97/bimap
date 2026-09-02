"""
Owns order lifecycle, product eligibility, checkout preparation and payment-state changes.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ..ports.payment import *
from ..ports.clock import *
from ...domain.orders.models import *
from ...domain.orders.events import *
from ...domain.orders.states import *
from ...domain.orders.transitions import *
from ...domain.products.models import *
from ...domain.products.limits import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Order Service")
printer = PrettyPrinter()


class OrderService:
    def __init__(self) -> None:
        logger(f"BIMAP Order Service successfully initialized")
        pass

__all__ = ["OrderService"]

