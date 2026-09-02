"""
Applies BIMAP governance/review policy to findings.
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.repositories import *
from ...domain.findings.models import *
from ...domain.governance.review import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Review Service")
printer = PrettyPrinter()


class ReviewService:
    def __init__(self) -> None:
        logger(f"BIMAP Review Service successfully initialized")
        pass

__all__ = ["ReviewService"]