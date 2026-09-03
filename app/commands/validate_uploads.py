"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.upload_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Uploads Validation")
printer = PrettyPrinter()


class ValidateUpload:
    def __init__(self) -> None:
        logger(f"BIMAP Uploads Validation Slot successfully initialized")
        pass

__all__ = ["ValidateUpload"]