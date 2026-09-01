"""
Serializes remediation data to CSV format.
"""

from __future__ import annotations

import csv

from io import StringIO

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...domain.findings.models import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Remediation CSV")
printer = PrettyPrinter()


class RemediationCSV:
    def __init__(self) -> None:
        logger(f"Remediation CSV successfully initialized")
        pass

    def serialize(self, remediation: Remediation) -> str:
        """
        Serializes a remediation to CSV format.
        """
        logger(f"Serializing remediation to CSV")
        output = StringIO()
        fieldnames = remediation.__dict__.keys()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(remediation.__dict__)
        return output.getvalue()

__all__ = ["RemediationCSV"]
