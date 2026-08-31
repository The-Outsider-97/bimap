"""
BIMAP domain-layer error hierarchy.

The domain error hierarchy is intentionally independent from concrete
application, API, persistence, SLAI, and reporting layers.

Domain objects raise structured exceptions and leave logging, HTTP mapping,
retry policy, and user-facing presentation to higher architectural layers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DomainError(Exception):
    """
    Base exception for all BIMAP domain-layer failures.

    Parameters
    ----------
    message:
        Human-readable technical description of the failure.
    field:
        Optional domain field associated with the failure.
    context:
        Optional structured diagnostic context. Context should contain
        identifiers or other non-sensitive diagnostic values rather than raw
        customer evidence.

    Notes
    -----
    ``code`` is intentionally stable and machine-readable so API/application
    boundaries can map domain failures without inspecting exception strings.
    """

    code = "BIMAP.DOMAIN.ERROR"

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_message = str(message).strip()

        if not normalized_message:
            normalized_message = self.__class__.__name__

        self.message = normalized_message
        self.field = str(field).strip() if field is not None else None
        self.context = dict(context or {})

        rendered = self.message

        if self.field:
            rendered = f"{rendered} [field={self.field}]"

        super().__init__(rendered)

    def to_dict(self) -> dict[str, Any]:
        """
        Return a structured representation of the error.

        This representation is suitable for application-boundary logging,
        observability, testing, or HTTP error mapping.
        """

        payload: dict[str, Any] = {
            "code": self.code,
            "type": self.__class__.__name__,
            "message": self.message,
        }

        if self.field:
            payload["field"] = self.field

        if self.context:
            payload["context"] = dict(self.context)

        return payload


class DomainValidationError(DomainError):
    """
    Raised when a supplied value cannot satisfy a domain constraint.

    Examples include malformed identifiers, invalid confidence values,
    naive timestamps, or unsupported value types.
    """

    code = "BIMAP.DOMAIN.VALIDATION"


class DomainInvariantError(DomainError):
    """
    Raised when an operation would violate a domain invariant.

    Unlike a simple field-validation failure, this normally involves a
    relationship between otherwise valid domain values.
    """

    code = "BIMAP.DOMAIN.INVARIANT"


class DomainSerializationError(DomainError):
    """
    Raised when a domain value cannot be represented deterministically.

    Evidence values must remain reproducible and machine-readable. Arbitrary
    runtime objects are therefore not silently serialized.
    """

    code = "BIMAP.DOMAIN.SERIALIZATION"


class EvidenceIntegrityError(DomainInvariantError):
    """
    Raised when evidence identity or provenance becomes inconsistent.

    Examples include one source identifier resolving to different source
    hashes or otherwise contradictory evidence provenance.
    """

    code = "BIMAP.DOMAIN.EVIDENCE.INTEGRITY"


class ProvenanceIntegrityError(EvidenceIntegrityError):
    """
    Raised when source content conflicts with recorded provenance.

    This specifically covers cryptographic source-integrity failures.
    """

    code = "BIMAP.DOMAIN.EVIDENCE.PROVENANCE_INTEGRITY"


class DuplicateEvidenceError(EvidenceIntegrityError):
    """
    Raised when a project aggregate contains duplicate evidence identifiers.
    """

    code = "BIMAP.DOMAIN.EVIDENCE.DUPLICATE"


class EvidenceNotFoundError(DomainError):
    """
    Raised when a required evidence identifier is absent from an aggregate.
    """

    code = "BIMAP.DOMAIN.EVIDENCE.NOT_FOUND"


__all__ = [
    "DomainError",
    "DomainValidationError",
    "DomainInvariantError",
    "DomainSerializationError",
    "EvidenceIntegrityError",
    "ProvenanceIntegrityError",
    "DuplicateEvidenceError",
    "EvidenceNotFoundError",
]