"""
Abstracts customer/system notifications.
"""

from __future__ import annotations

from .utils.app_errors import *
from .utils.app_helpers import *
from ...domain.orders.models import *
from ...contracts.report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Notifications")
printer = PrettyPrinter()


class Notifications:
    def __init__(self) -> None:
        logger(f"BIMAP Notifications successfully initialized")
        pass

__all__ = ["Notifications"]

