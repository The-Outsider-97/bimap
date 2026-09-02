"""
Application-level coordinator between deterministic BIMAP auditing, the queue, SLAI and persistence.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ..ports.queue import *
from ..ports.slai import *
from ...audit_engine.engine import *
from ...audit_engine.result import *
from ...contracts.audit_job import *
from ...domain.findings.models import *
from ...domain.governance.review import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Audit Service")
printer = PrettyPrinter()


class AuditService:
    def __init__(self) -> None:
        logger(f"BIMAP audit Service successfully initialized")
        pass

__all__ = ["AuditService"]