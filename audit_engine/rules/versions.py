"""
Version identity for deterministic BIMAP rulesets.
This is different from contracts/versions.py because:

contracts/versions
    = data format version

rules/versions
    = audit logic version

Not duplicate responsibilities.
"""

from __future__ import annotations

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Rules Version")
printer = PrettyPrinter()


class VersionRule:
    def __init__(self) -> None:
        logger(f"Rules Version Rule successfully initialized")
        pass

__all__ = ["VersionRule"]