"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.fulfilment_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Enqueue Audit")
printer = PrettyPrinter()


class EnqueueAudit:
    """
    """
    def __init__(self) -> None:
        logger(f"BIMAP Enqueue Audit Slot successfully initialized")
        pass

__all__ = ["EnqueueAudit"]