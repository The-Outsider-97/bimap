"""
External/base evidence DTO/schema shared by family and project evidence.
"""

from __future__ import annotations

import json
import versions
import dataclasses

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from ..domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts Evidence")
printer = PrettyPrinter()


class ContractsEvidence:
    def __init__(self) -> None:
        logger(f"Contracts Evidence successfully initialized")
        pass

__all__ = ["ContractsEvidence"]
