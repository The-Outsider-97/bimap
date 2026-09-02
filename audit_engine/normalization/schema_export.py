"""
Converts extracted/client BIM requirements into canonical requirement models.
"""

from __future__ import annotations

from ..utils.engine_error import *
from ..utils.engine_helpers import *
from ...contracts.requirement import *
from ...domain.requirements.models import *
from ...domain.evidence.provenance import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Schema Exporter")
printer = PrettyPrinter()


class SchemaExporter:
    def __init__(self) -> None:
        logger(f"Schema Exporter successfully initialized")
        pass

__all__ = ["SchemaExporter"]