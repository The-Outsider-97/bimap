"""
Serializes finding data to JSON format.
"""

from __future__ import annotations

import json

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...domain.findings.models import *
from ...contracts.finding import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Finding JSON")
printer = PrettyPrinter()


class FindingJSON:
    def __init__(self) -> None:
        logger(f"Finding JSON successfully initialized")
        pass

    def serialize(self, finding: Finding) -> str:
        """
        Serializes a finding to JSON format.
        """
        logger(f"Serializing finding to JSON")
        return json.dumps(finding.__dict__)

__all__ = ["FindingJSON"]