"""

"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ...contracts.audit_job import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Get Audit Status")
printer = PrettyPrinter()


class GAS:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Get Audit Status successfully initialized")
        pass

__all__ = ["GAS"]