"""

"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.services.fulfilment_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Downloads")
printer = PrettyPrinter()


class RouteDownloads:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Downloads successfully initialized")
        pass

__all__ = ["RouteDownloads"]