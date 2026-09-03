"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.fulfilment_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Request Deletion")
printer = PrettyPrinter()


class RequestDeletion:
    """
    """
    def __init__(self) -> None:
        logger(f"BIMAP Request Deletion Slot successfully initialized")
        pass

__all__ = ["RequestDeletion"]