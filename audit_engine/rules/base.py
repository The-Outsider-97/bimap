"""
Base deterministic audit-rule contract/protocol.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.findings.models import *
from ...domain.evidence.models import *
from ...domain.requirements.models import *
from ..context import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Base Engine Rule")
printer = PrettyPrinter()


class BaseRules:
    def __init__(self) -> None:
        logger(f"Base Engine Rule successfully initialized")
        pass

__all__ = ["BaseRules"]