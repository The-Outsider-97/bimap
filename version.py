"""
BIMAP package/release version metadata.
"""

from logs.logger import get_logger, PrettyPrinter # type: ignore

logger = get_logger("BIMAP Version")
printer = PrettyPrinter()

class Version:
    def __init__(self) -> None:
        pass

__all__ = ["Version"]

