"""
Stable envelope representing work submitted to an audit worker/SLAI boundary.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from .order import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Audit Job")
printer = PrettyPrinter()


class AuditJob:
    """It reference evidence by IDs/URIs/manifests rather than embedding the entire application graph."""
    def __init__(self) -> None:
        logger(f"Audit Job successfully initialized")
        pass

__all__ = ["AuditJob"]



