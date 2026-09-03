"""
Owns order lifecycle, product eligibility, checkout preparation and payment-state changes.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.audit_service import *
from ...contracts.audit_job import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Enqueue Audit")
printer = PrettyPrinter()


class EnqueueAudit:
    def __init__(self) -> None:
        logger(f"BIMAP Enqueue Audit successfully initialized")
        pass

__all__ = ["EnqueueAudit"]

