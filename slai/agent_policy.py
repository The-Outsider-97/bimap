"""
Defines which SLAI capabilities/agents BIMAP is allowed to invoke.

It rceives slai_profile.yaml data from bootstrap and uses it to determine which SLAI agents are allowed to be invoked by BIMAP.
"""

from __future__ import annotations

import yaml

from .utils.slai_errors import *
from .utils.slai_helpers import *
from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("SLAI Agent Policy")
printer = PrettyPrinter()


class SLAIAgentPolicy:
    def __init__(self) -> None:
        logger(f"SLAIAgentPolicy successfully initialized")
        pass

__all__ = ["SLAIAgentPolicy"]
