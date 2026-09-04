"""
Creates/configures the FastAPI instance.
"""

from __future__ import annotations

from typing import Any

from .utils.api_errors import *
from .utils.api_helpers import *
from .middleware.correlation import *
from .middleware.error_mapping import *
from .middleware.request_limits import *
from .middleware.security import *
from .routes.admin import *
from .routes.checkout import *
from .routes.deletion import *
from .routes.downloads import *
from .routes.health import *
from .routes.orders import *
from .routes.products import *
from .routes.reports import *
from .routes.uploads import *
from .routes.webhooks import *
from .dependencies import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API App")
printer = PrettyPrinter()


class App:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API App successfully initialized")
        pass

__all__ = ["App"]
