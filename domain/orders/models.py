"""
Defines the canonical BIMAP Order aggregate.
"""

from __future__ import annotations

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from ..products.models import *
from .states import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Orders Models")
printer = PrettyPrinter()

class OrdersModels:
    def __init__(self) -> None:
        logger(f"Orders Models  successfully initialized")
        pass

__all__ = ["OrdersModels"]