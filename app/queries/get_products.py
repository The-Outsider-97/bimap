"""
Product config should be injected by bootstrap rather than this module opening YAML files itself.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.products.models import *
from ...domain.products.limits import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Get Products Query")
printer = PrettyPrinter()


class GetProduct:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Get Products Query successfully initialized")
        pass

__all__ = ["GetProduct"]