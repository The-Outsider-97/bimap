"""
BIMAP's composition root. It is the only module allowed to know about most concrete application layers and wire them together.
"""

from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("Bootstrap")
printer = PrettyPrinter()

class Bootstrap:
    def __init__(self) -> None:
        logger(f"Bootstrap successfully initialized")
        pass

__all__ = ["Bootstrap"]
