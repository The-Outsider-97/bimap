"""
Defines evidence origin, source identity, hashes, timestamps, versions and traceability references.
"""

from __future__ import division

import traceback
import hashlib
import datetime

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Provenance")
printer = PrettyPrinter()

class Provenance:
    def __init__(self) -> None:
        logger(f"Provenance successfully initialized")
        pass

    def evidence_origin(self):
        pass

    def source_identity(self):
        pass

    def hashes(self):
        pass

__all__ = ["Provenance"]
