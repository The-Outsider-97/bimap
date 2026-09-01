"""
Packages generated report artifacts into the delivery bundle.
"""

from __future__ import annotations

from .utils.reporting_errors import *
from .utils.reporting_helpers import *
from ..contracts.report_manifest import *
from .artifact_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Package Builder")
printer = PrettyPrinter()


class PackageBuilder:
    def __init__(self) -> None:
        logger(f"Package Builder successfully initialized")
        pass

__all__ = ["PackageBuilder"]

