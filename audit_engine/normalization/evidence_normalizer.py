"""
Converts accepted external evidence DTOs into canonical domain evidence.
"""

from __future__ import annotations

from ..utils.engine_error import *
from ..utils.engine_helpers import *
from ...contracts.evidence import *
from ...domain.evidence.models import *
from ...domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Evidence Normalizer")
printer = PrettyPrinter()


class EvidenceNormalizer:
    def __init__(self) -> None:
        logger(f"Evidence Normalizer successfully initialized")
        pass

__all__ = ["EvidenceNormalizer"]