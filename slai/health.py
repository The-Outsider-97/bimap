"""
Checks availability/readiness of the SLAI runtime.
"""

from __future__ import annotations

import yaml

from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("SLAI Health Check")
printer = PrettyPrinter()


class SLAIHealthCheck:
    def __init__(self) -> None:
        logger(f"SLAIHealthCheck successfully initialized")
        pass

__all__ = ["SLAIHealthCheck"]