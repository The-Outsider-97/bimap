"""
Interface for submitting asynchronous jobs.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import *
from ...domain.evidence.models import *
from ...domain.findings.models import *
from ...domain.governance.review import *
from ...contracts.report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Repository")
printer = PrettyPrinter()


class Repository:
    def __init__(self) -> None:
        logger(f"BIMAP Repository successfully initialized")
        pass

__all__ = ["Repository"]