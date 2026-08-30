"""
Canonical aggregate describing project-level BIM evidence after ingestion/normalization.
"""

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .models import *
from .provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Project Evidence")
printer = PrettyPrinter()

class ProjectEvidence:
    def __init__(self) -> None:
        logger(f"Project Evidence successfully initialized")
        pass

    def bimap_ingestion(self):
        pass

    def bimap_normalization(self):
        pass

__all__ = ["ProjectEvidence"]

