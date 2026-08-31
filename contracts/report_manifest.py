"""
Defines which artifacts form an immutable BIMAP report package.
"""

from __future__ import annotations

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from .finding import *
from .requirement import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Report Manifest")
printer = PrettyPrinter()


class ReportManifest:
    def __init__(self) -> None:
        logger(f"Report Manifest successfully initialized")
        pass

__all__ = ["ReportManifest"]




