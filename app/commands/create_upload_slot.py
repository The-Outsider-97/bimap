"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.upload_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Create Upload Slot")
printer = PrettyPrinter()


class UploadSlot:
    def __init__(self) -> None:
        logger(f"BIMAP Create Upload Slot successfully initialized")
        pass

__all__ = ["UploadSlot"]