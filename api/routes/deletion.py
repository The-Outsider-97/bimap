"""

"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.commands.request_deletion import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Deletion")
printer = PrettyPrinter()


class RouteDeletion:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Deletion successfully initialized")
        pass

__all__ = ["RouteDeletion"]
