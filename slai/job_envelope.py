"""
Determines What exactly does BIMAP give to SLAI

audit_engine/result.py
        ↓
slai/job_envelope.py
        ↓
slai/orchestration.py
        ↓
SLAI agents
"""

from __future__ import annotations

import yaml

from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("SLAI Job Envelope")
printer = PrettyPrinter()


class SLAIJobEnvelope:
    def __init__(self) -> None:
        logger(f"SLAI Job Envelope successfully initialized")
        pass

__all__ = ["SLAIJobEnvelope"]
