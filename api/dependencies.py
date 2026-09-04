"""
FastAPI dependency providers that retrieve already-created services from the application runtime.
Critical:

api/dependencies.py
    MUST NOT import bootstrap.py

Instead:

bootstrap.py
    creates services
        ↓
api/app.py
    stores them in app.state
        ↓
api/dependencies.py
    retrieves them

That prevents:

bootstrap → api.app → dependencies → bootstrap
"""

from __future__ import annotations

from typing import Any, Mapping, TYPE_CHECKING

from .utils.api_errors import *
from .utils.api_helpers import *
from ..app.services.audit_service import *
from ..app.services.fulfilment_service import *
from ..app.services.order_service import *
from ..app.services.review_service import *
from ..app.services.upload_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP API Dependancies")
printer = PrettyPrinter()


class Dependancies:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP API Dependancies successfully initialized")
        pass

__all__ = ["Dependancies"]
