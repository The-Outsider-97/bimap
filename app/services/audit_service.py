"""
Application-level coordinator between deterministic BIMAP auditing, the queue, SLAI and persistence.

Runtime logic:

AuditService.run_audit()
        │
        ├── 1. run deterministic BIMAP AuditEngine
        │
        │       ↓
        │    AuditResult
        │
        ├── 2. persist deterministic result
        │
        ├── 3. construct SLAI analysis request
        │
        ├── 4. self.slai.analyze(...)
        │
        ├── 5. receive SLAI intelligence/governance result
        │
        ├── 6. combine it with deterministic findings
        │
        └── 7. persist final audit result
"""

from __future__ import annotations

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..ports.queue import *
from ..ports.repositories import *
from ..ports.slai import *
from ...audit_engine.engine import *
from ...audit_engine.result import *
from ...contracts.audit_job import *
from ...domain.findings.models import *
from ...domain.governance.review import *
from ...slai.job_envelope import * # via port/adapter boundary
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Audit Service")
printer = PrettyPrinter()


class AuditService:
    def __init__(
        self,
        audit_engine,
        slai: SLAIPort,
        repository: Repository,
    ):
        self.audit_engine = audit_engine
        self.slai = slai # But because self.slai is an injected SlaiAdapter, AuditService remains independent of SLAI internals.
        self.repository = repository
        logger(f"BIMAP audit Service successfully initialized")

    def run_audit(self, job):
        deterministic = self.audit_engine.run(job)

        intelligence = self.slai.analyze(
            job=job,
            audit_result=deterministic,
        )

__all__ = ["AuditService"]