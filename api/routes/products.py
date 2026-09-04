"""

"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.queries.get_products import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Products")
printer = PrettyPrinter()


class RouteProducts:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Products successfully initialized")
        pass

__all__ = ["RouteProducts"]