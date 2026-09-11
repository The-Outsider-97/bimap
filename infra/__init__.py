"""
Concrete infrastructure adapters supplied outside BIMAP's application core.

Infrastructure adapters are exposed lazily.

This package initializer deliberately performs no eager imports. Importing one
adapter (for example ``infra.local``) must not require unrelated optional
infrastructure such as IFC extraction, PDF rendering, or artifact email
delivery to be installed and importable.

This keeps deployment composition deterministic and prevents optional adapters
from breaking BIMAP startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .email_artifact_mailer import EmailArtifactMailer
    from .reportlab_data_extraction_renderer import ReportLabDataExtractionPDFRenderer
    from .local import (
        DevelopmentMalware,
        DisabledPayment,
        InMemoryAuditResultStore,
        InMemoryRepository,
        InMemoryStorage,
        InProcessQueue,
        SystemClock,
    )


__all__ = [
    "EmailArtifactMailer",
    "ReportLabDataExtractionPDFRenderer",
    "DevelopmentMalware",
    "DisabledPayment",
    "InMemoryAuditResultStore",
    "InMemoryRepository",
    "InMemoryStorage",
    "InProcessQueue",
    "SystemClock",
]


def __getattr__(name: str) -> Any:
    """
    Resolve infrastructure adapters lazily.

    Lazy resolution preserves the package-level public API while preventing an
    unrelated optional adapter from becoming a startup dependency.
    """

    if name == "EmailArtifactMailer":
        from .email_artifact_mailer import EmailArtifactMailer
        return EmailArtifactMailer

    if name == "ReportLabDataExtractionPDFRenderer":
        from .reportlab_data_extraction_renderer import ReportLabDataExtractionPDFRenderer
        return ReportLabDataExtractionPDFRenderer

    if name in {
        "DevelopmentMalware",
        "DisabledPayment",
        "InMemoryAuditResultStore",
        "InMemoryRepository",
        "InMemoryStorage",
        "InProcessQueue",
        "SystemClock",
    }:
        from . import local

        return getattr(local, name)

    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )
