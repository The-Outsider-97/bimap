"""
Owns report finalization, artifact publication, notification, retention and deletion.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ..ports.storage import *
from ..ports.notifications import *
from ..ports.clock import *
from ...reporting.report_builder import *
from ...reporting.package_builder import *
from ...reporting.artifact_manifest import *
from ...domain.orders.transitions import *
from ...contracts.report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Fulfilment Service")
printer = PrettyPrinter()


class FulfilmentService:
    def __init__(self) -> None:
        logger(f"BIMAP Fulfilment Service successfully initialized")
        pass

__all__ = ["FulfilmentService"]