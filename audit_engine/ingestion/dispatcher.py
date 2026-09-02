"""
Determines which ingestion path applies to the incoming audit payload.
"""

from __future__ import annotations

from ..utils.engine_error import *
from ..utils.engine_helpers import *
from ...contracts.family_evidence import *
from ...contracts.project_evidence import *
from .project_evidence import *
from .manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Dispatcher")
printer = PrettyPrinter()


class Dispatcher:
    def __init__(self) -> None:
        logger(f"Dispatcher successfully initialized")
        pass

__all__ = ["Dispatcher"]