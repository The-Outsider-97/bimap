"""
Validates the evidence package manifest before semantic processing begins.
"""

from __future__ import annotations

import json
import sys
import time
import hashlib

from ..utils.engine_error import *
from ..utils.engine_helpers import *
from ...contracts.evidence import *
from ...contracts.family_evidence import *
from ...contracts.project_evidence import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Manifest")
printer = PrettyPrinter()


class Manifest:
    def __init__(self) -> None:
        logger(f"Manifest successfully initialized")
        pass

__all__ = ["Manifest"]
