"""

"""

from __future__ import annotations

from typing import Any, Mapping

from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from ...app.commands.create_upload_slot import *
from ...app.commands.validate_uploads import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Route Uploads")
printer = PrettyPrinter()


class RouteUploads:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Route Uploads successfully initialized")
        pass

__all__ = ["RouteUploads"]
