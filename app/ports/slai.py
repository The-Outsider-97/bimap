"""
Stable application-facing interface for SLAI reasoning/governance.

It does not import bimap/slai/adapter.py. Instead:

app/ports/slai.py
       ↑ implements
slai/adapter.py

This inversion is critical.
"""

from __future__ import annotations

from .utils.app_errors import *
from .utils.app_helpers import *
from ...contracts.audit_job import *
from ...domain.evidence.models import *
from ...domain.findings.models import *
from ...domain.governance.review import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Repository")
printer = PrettyPrinter()


class Repository:
    def __init__(self) -> None:
        logger(f"BIMAP Repository successfully initialized")
        pass

__all__ = ["Repository"]