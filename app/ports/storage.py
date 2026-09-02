"""
Storage abstraction for staged uploads, evidence and generated artifacts.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.evidence import *
from ...contracts.report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Storage")
printer = PrettyPrinter()


class Storage:
    def __init__(self) -> None:
        logger(f"BIMAP Storage successfully initialized")
        pass

__all__ = ["Storage"]