"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.order_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Cancel Order")
printer = PrettyPrinter()


class CancelOrder:
    def __init__(self) -> None:
        logger(f"BIMAP Cancel Order successfully initialized")
        pass

__all__ = ["CancelOrder"]

