"""
Dependency-neutral BIMAP package version metadata.

This module intentionally imports only the Python standard library so BIMAP
version information can be inspected without initializing SLAI, logging,
FastAPI, the Audit Engine, or application infrastructure.

BIMAP currently has no declared tagged/package release version in the
repository. The source tree is therefore explicitly marked as an unreleased
development version until the first release number is intentionally selected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


_RELEASE_LEVELS: Final[frozenset[str]] = frozenset(
    {
        "dev",
        "alpha",
        "beta",
        "rc",
        "final",
    }
)


@dataclass(frozen=True, slots=True)
class Version:
    """
    Validated PEP 440-compatible BIMAP release identifier.

    Parameters
    ----------
    major:
        Major release component.
    minor:
        Minor release component.
    patch:
        Patch release component.
    release_level:
        One of ``dev``, ``alpha``, ``beta``, ``rc`` or ``final``.
    serial:
        Pre-release/development sequence number. Final releases use ``0``.
    """

    major: int
    minor: int
    patch: int

    release_level: str = "final"
    serial: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "major",
            "minor",
            "patch",
            "serial",
        ):
            value = getattr(
                self,
                field_name,
            )

            if (
                isinstance(value, bool)
                or not isinstance(value, int)
            ):
                raise TypeError(
                    f"{field_name} must be an integer."
                )

            if value < 0:
                raise ValueError(
                    f"{field_name} must be non-negative."
                )

        level = str(
            self.release_level
        ).strip().lower()

        if level not in _RELEASE_LEVELS:
            raise ValueError(
                "release_level must be one of: "
                + ", ".join(
                    sorted(_RELEASE_LEVELS)
                )
            )

        if (
            level == "final"
            and self.serial != 0
        ):
            raise ValueError(
                "Final releases must use serial=0."
            )

        object.__setattr__(
            self,
            "release_level",
            level,
        )

    @property
    def base_version(self) -> str:
        """Return the canonical three-component release base."""

        return (
            f"{self.major}."
            f"{self.minor}."
            f"{self.patch}"
        )

    @property
    def public(self) -> str:
        """Return the canonical PEP 440 public version."""

        if self.release_level == "final":
            return self.base_version

        if self.release_level == "dev":
            return (
                f"{self.base_version}"
                f".dev{self.serial}"
            )

        if self.release_level == "alpha":
            return (
                f"{self.base_version}"
                f"a{self.serial}"
            )

        if self.release_level == "beta":
            return (
                f"{self.base_version}"
                f"b{self.serial}"
            )

        return (
            f"{self.base_version}"
            f"rc{self.serial}"
        )

    @property
    def is_prerelease(self) -> bool:
        """Return whether this identifier represents a non-final release."""

        return self.release_level != "final"

    def as_tuple(
        self,
    ) -> tuple[int, int, int, str, int]:
        """Return stable structured version metadata."""

        return (
            self.major,
            self.minor,
            self.patch,
            self.release_level,
            self.serial,
        )

    def __str__(self) -> str:
        """Return the canonical public version."""

        return self.public


# ---------------------------------------------------------------------------
# Canonical package version
# ---------------------------------------------------------------------------
#
# No tagged/package BIMAP release version currently exists in the repository.
# Do not silently promote this value when implementation work advances.
#
# Change VERSION only as part of an intentional release.
#

VERSION: Final[Version] = Version(
    0,
    0,
    0,
    "dev",
    0,
)


__version__: Final[str] = VERSION.public

__version_info__: Final[
    tuple[int, int, int, str, int]
] = VERSION.as_tuple()


# ---------------------------------------------------------------------------
# Stable package metadata
# ---------------------------------------------------------------------------

__title__: Final[str] = "bimap"

__product_name__: Final[str] = (
    "R3D BIM Audit Platform"
)

__description__: Final[str] = (
    "Evidence-grounded deterministic BIM auditing "
    "with governed SLAI integration."
)


__all__ = [
    "Version",
    "VERSION",
    "__version__",
    "__version_info__",
    "__title__",
    "__product_name__",
    "__description__",
]