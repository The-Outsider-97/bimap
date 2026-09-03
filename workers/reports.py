"""

"""

from __future__ import annotations

import json
import yaml
import datetime

from .utils.workers_errors import *
from .utils.workers_helpers import *
from ..reporting.report_builder import *
from ..slai.result_mapper import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Worker Reports")
printer = PrettyPrinter()


class WorkerReports:
    """

    """
    def __init__(self) -> None:
        logger(f"BIMAP Worker Reports successfully initialized")
        pass

    def summarizer(self, reporting: ReportBuilder, slai_result: SLAIResultMapper, *, report_id: str | None = None) -> None:
        self.reporting = reporting
        self.slai_result = slai_result
        self.performance_report()
        pass

    def execute(self):
        pass

    def performance_report(self):
        pass

__all__ = ["WorkerReports"]