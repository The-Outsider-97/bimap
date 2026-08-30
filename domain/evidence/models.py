"""
Canonical internal BIMAP evidence entities.
"""

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Evidence Models")
printer = PrettyPrinter()

class EvidenceModels:
    def __init__(self) -> None:
        logger(f"Evidence Models successfully initialized")
        pass

__all__ = ["EvidenceModels"]