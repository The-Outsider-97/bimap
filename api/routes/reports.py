"""

"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.queries.list_reports import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Reports")
printer = PrettyPrinter()


class RouteReports:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Reports successfully initialized")
        pass

__all__ = ["RouteReports"]