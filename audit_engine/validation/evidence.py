"""
Verifies evidence integrity, completeness and provenance before findings are trusted.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...domain.evidence.models import *
from ...domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Evidence Valdation")
printer = PrettyPrinter()


class EvidenceValdation:
    def __init__(self) -> None:
        logger(f"Evidence Valdation successfully initialized")
        pass

__all__ = ["EvidenceValdation"]

