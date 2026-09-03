"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ...contracts.order import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP List Order Query")
printer = PrettyPrinter()


class ListOrder:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP List Order Query successfully initialized")
        pass

__all__ = ["ListOrder"]