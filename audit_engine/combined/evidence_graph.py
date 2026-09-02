"""
Constructs cross-scope evidence/finding relationships for the Combined Audit.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.findings.models import *
from ...domain.evidence.models import *
from ...domain.evidence.project_evidence import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Evidence Graph")
printer = PrettyPrinter()


class EvidenceGraph:
    def __init__(self) -> None:
        logger(f"Evidence Graph successfully initialized")
        pass

__all__ = ["EvidenceGraph"]

