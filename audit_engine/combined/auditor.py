"""
Correlates the completed RFA and BIM QA findings into higher-order cross-scope findings.
Very important:

combined/auditor.py
    SHOULD NOT import:
        rfa/auditor.py
        bim_qa/auditor.py

Why? Otherwise this could happen:

engine
  ├─ run RFA
  ├─ run BIM QA
  └─ run Combined
       ├─ run RFA AGAIN
       └─ run BIM QA AGAIN

Instead:

engine
  ├─ RFA Auditor ───────┐
  ├─ BIM QA Auditor ────┼─→ Combined Auditor
  └─────────────────────┘

The engine passes the already-produced findings into Combined.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ..context import *
from .versions import *
from .evidence_graph import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Combined Audit Auditor")
printer = PrettyPrinter()


class CombinedAuditor:
    def __init__(self) -> None:
        logger(f"Combined audit auditor successfully initialized")
        pass

__all__ = ["CombinedAuditor"]

