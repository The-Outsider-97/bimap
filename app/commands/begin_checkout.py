"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.order_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Begin Checkout")
printer = PrettyPrinter()


class BeginCheckout:
    def __init__(self) -> None:
        logger(f"BIMAP Begin Checkout successfully initialized")
        pass

__all__ = ["BeginCheckout"]

