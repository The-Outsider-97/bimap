"""
HTTP endpoints for order creation/retrieval/cancellation.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.commands.create_order import *
from ...app.commands.cancel_order import *
from ...app.queries.get_order import *
from ...app.queries.list_orders import *
from ...contracts.order import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Orders")
printer = PrettyPrinter()


class RouteOrders:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Orders successfully initialized")
        pass

__all__ = ["RouteOrders"]