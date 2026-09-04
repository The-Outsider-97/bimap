"""

"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.commands.handle_payment import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Webhooks")
printer = PrettyPrinter()


class RouteWebhooks:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Webhooks successfully initialized")
        pass

__all__ = ["RouteWebhooks"]