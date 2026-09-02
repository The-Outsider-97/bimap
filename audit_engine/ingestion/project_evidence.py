"""
Converts accepted project-evidence payloads into canonical project evidence.
"""

from __future__ import annotations

from ..utils.engine_error import *
from ..utils.engine_helpers import *
from ...contracts.project_evidence import ProjectEvidence as ProjectEvidenceContract
from ...domain.evidence.project_evidence import ProjectEvidence as ProjectEvidenceDomain
from ...domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Project Evidence")
printer = PrettyPrinter()


class ProjectEvidence:
    def __init__(self) -> None:
        logger(f"Project evidence successfully initialized")
        pass

__all__ = ["ProjectEvidence"]
