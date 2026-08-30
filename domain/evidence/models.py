"""
Canonical internal BIMAP evidence entities.
"""

from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Evidence Models")
printer = PrettyPrinter()

class EvidenceModels:
    def __init__(self) -> None:
        logger(f"Evidence Models successfully initialized")
        pass

__all__ = ["EvidenceModels"]