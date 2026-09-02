"""
Stable application-facing interface for SLAI reasoning/governance.

It does not import bimap/slai/adapter.py. Instead:

app/ports/slai.py
       ↑ implements
slai/adapter.py

This inversion is critical.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping

from .utils.app_errors import *
from .utils.app_helpers import *
from ...contracts.audit_job import *
from ...audit_engine.result import *
# BIMAP result/governance types
from ...domain.evidence.models import *
from ...domain.findings.models import *
from ...domain.governance.review import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Repository")
printer = PrettyPrinter()

@dataclass
class SlaiResult:
    pass

class Repository:
    def __init__(self) -> None:
        logger(f"BIMAP Repository successfully initialized")
        pass

    def analyze(
        self,
        job: AuditJob,
        audit_result: AuditResult,
    ) -> SlaiResult:
        ...

    def health(self) -> Mapping[str, Any]:
        ...

__all__ = ["Repository"]
