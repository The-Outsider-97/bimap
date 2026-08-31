"""
Only module responsible for generating JSON Schema from BIMAP contracts.
"""

from __future__ import annotations

import json
import sys
import time
import hashlib

from .utils.contracts_error import *
from .utils.contracts_helpers import *
from .versions import *
from .evidence import *
from .family_evidence import *
from .project_evidence import *
from .finding import *
from .requirement import *
from .order import *
from .audit_job import *
from .report_manifest import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Schema Report")
printer = PrettyPrinter()


class SchemaReport:
    def __init__(self) -> None:
        logger(f"Schema Report successfully initialized")
        pass

__all__ = ["SchemaReport"]
