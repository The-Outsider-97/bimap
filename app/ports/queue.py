"""
Interface for submitting asynchronous jobs.
"""

from __future__ import annotations

from .utils.app_errors import *
from .utils.app_helpers import *
from ...contracts.audit_job import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Queue")
printer = PrettyPrinter()


class Queue:
    def __init__(self) -> None:
        logger(f"BIMAP Queue successfully initialized")
        pass

__all__ = ["Queue"]

