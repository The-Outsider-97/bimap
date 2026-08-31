"""
Version identifiers for externally visible data contracts.
"""

from __future__ import annotations

import json
import versions
import dataclasses

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Contracts version")
printer = PrettyPrinter()


FAMILY_EVIDENCE_SCHEMA_VERSION = ""
PROJECT_EVIDENCE_SCHEMA_VERSION = ""
FINDING_SCHEMA_VERSION = ""
AUDIT_JOB_SCHEMA_VERSION = ""


class ContractsVersion:
    def __init__(self) -> None:
        logger(f"Contracts Version successfully initialized")
        pass

__all__ = ["ContractsVersion"]
