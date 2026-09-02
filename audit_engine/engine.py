"""
Top-level deterministic BIMAP audit orchestrator.
audit_engine/engine.py
│
├── ingestion/dispatcher.py
│
├── normalization/
│   ├── evidence_normalizer.py
│   ├── family_normalizer.py
│   └── requirement_normalizer.py
│
├── rfa/auditor.py
│       └── rules/executor.py
│               └── rules/registry.py
│                       └── rules/base.py
│
├── bim_qa/auditor.py
│       ├── bim_qa/requirement_matrix.py
│       └── rules/executor.py
│
├── combined/auditor.py
│       └── combined/evidence_graph.py
│
├── validation/evidence.py
├── validation/findings.py
├── validation/coverage.py
│
└── result.py

And crucially:

audit_engine/engine.py
    DOES NOT import:
        slai/*
        reporting/*
        api/*
        workers/*
        app/commands/*

That keeps the audit engine independently testable.
"""

from __future__ import annotations

from .utils.engine_errors import *
from .utils.engine_helpers import *
from .ingestion.dispatcher import *
from .normalization.evidence_normalizer import *
from .normalization.family_normalizer import *
from .normalization.schema_export import *
from .rfa.auditor import *
from .bim_qa.auditor import *
from .combined.auditor import *
from .validation.coverage import *
from .validation.findings import *
from .validation.evidence import *
from .context import *
from .result import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Audit Engine")
printer = PrettyPrinter()


class AuditEngine:
    def __init__(self) -> None:
        logger(f"Audit Engine successfully initialized")
        pass

__all__ = ["AuditEngine"]

