"""
Asynchronous report-generation/delivery job.
"""

from __future__ import annotations

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.services.fulfilment_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Job Report")
printer = PrettyPrinter()


class JobReport:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Job Report successfully initialized")
        pass

__all__ = ["JobReport"]