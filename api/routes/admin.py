"""
Internal review/operations API.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.services.review_service import *
from ...app.queries.get_order import *
from ...app.queries.list_orders import *
from ...app.queries.list_reports import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Admin")
printer = PrettyPrinter()


class RouteAdmin:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Admin successfully initialized")
        pass

__all__ = ["RouteAdmin"]
