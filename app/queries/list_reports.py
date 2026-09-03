"""
Product config should be injected by bootstrap rather than this module opening YAML files itself.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ...contracts.report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP List Report Query")
printer = PrettyPrinter()


class ListReport:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP List Report Query successfully initialized")
        pass

__all__ = ["ListReport"]