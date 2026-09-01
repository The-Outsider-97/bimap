"""
Creates/verifies the manifest of generated customer artifacts.
"""

from __future__ import annotations

from .utils.reporting_errors import *
from .utils.reporting_helpers import *
from ..contracts.report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Reporting Artifact Manifest")
printer = PrettyPrinter()


class ArtifactManifest:
    def __init__(self) -> None:
        logger(f"Reporting Artifact Manifest successfully initialized")
        pass

__all__ = ["ArtifactManifest"]

