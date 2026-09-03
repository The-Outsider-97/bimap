"""
Asynchronous entry for executing a queued audit.
"""

from __future__ import annotations

from ..utils.workers_errors import *
from ..utils.workers_helpers import *
from ...app.services.audit_service import *
from ...app.services.review_service import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Worker Audit")
printer = PrettyPrinter()


class WorkerAudit:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Worker Audit successfully initialized")
        pass

__all__ = ["WorkerAudit"]