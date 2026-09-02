"""
Converts Family Evidence into normalized evidence structures suitable for deterministic RFA auditing.
"""

from __future__ import annotations

from ..utils.engine_error import *
from ..utils.engine_helpers import *
from ...contracts.family_evidence import *
from ...domain.evidence.models import *
from ...domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Family Normalizer")
printer = PrettyPrinter()


class FamilyNormalizer:
    def __init__(self) -> None:
        logger(f"Family Normalizer successfully initialized")
        pass

__all__ = ["FamilyNormalizer"]