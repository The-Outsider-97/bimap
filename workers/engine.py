"""

"""

from __future__ import annotations

from .utils.workers_errors import *
from .utils.workers_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Worker Engine")
printer = PrettyPrinter()


class WorkerEngine:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Worker Engine successfully initialized")
        pass

__all__ = ["WorkerEngine"]