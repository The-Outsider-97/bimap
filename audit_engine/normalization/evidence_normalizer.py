"""
Canonical EvidenceContract -> EvidenceItem normalization for BIMAP Level 4.

The external contract already owns provenance validation and the authoritative
conversion into ``domain.evidence.models.EvidenceItem``.  This module therefore
does not rebuild Provenance, SourceIdentity, ContentHash, logical locations, or
confidence normalization.  It provides the audit-engine normalization boundary,
input-shape handling, error translation, deterministic batch handling, and
method diagnostics around that lower-level conversion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...contracts.evidence import EvidenceContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.evidence.models import EvidenceItem
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Evidence Normalizer")
printer = PrettyPrinter()

SerializedJSON = str | bytes | bytearray
EvidenceInput = EvidenceContract | EvidenceItem | Mapping[str, Any] | SerializedJSON


class EvidenceNormalizer:
    """Convert accepted external evidence DTOs into canonical domain evidence."""

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="evidence_normalizer",
            action="Initializing evidence normalizer",
            event="evidence_normalizer_init_start",
        )
        logger.debug({"event": "evidence_normalizer_initialized"})

    def normalize(self, value: EvidenceInput) -> EvidenceItem:
        """Normalize one supported evidence representation.

        ``EvidenceItem`` is accepted as an idempotent pass-through so callers can
        safely compose normalizers without reconstructing already canonical
        evidence. Mapping/JSON conveniences delegate validation to
        ``EvidenceContract``; they do not create a second schema implementation.
        """
        announce_engine_action(
            printer,
            logger,
            component="evidence_normalizer",
            action="Normalizing evidence",
            event="evidence_normalizer_normalize_start",
            context={"input_type": type(value).__name__},
        )

        if isinstance(value, EvidenceItem):
            return value

        try:
            if isinstance(value, EvidenceContract):
                contract = value
            elif isinstance(value, Mapping):
                contract = EvidenceContract.from_dict(value)
            elif isinstance(value, (str, bytes, bytearray)):
                contract = EvidenceContract.from_json(value)
            else:
                raise UnsupportedEngineInputError(
                    "EvidenceNormalizer input must be EvidenceContract, EvidenceItem, mapping, or JSON.",
                    component="evidence_normalizer",
                    operation="normalize",
                    field="value",
                    context={"received_type": type(value).__name__},
                )

            normalized = contract.to_domain()
        except EngineError:
            raise
        except ContractError as exc:
            raise EngineValidationError(
                "Evidence cannot be normalized because the external contract is invalid.",
                component="evidence_normalizer",
                operation="normalize",
                field="value",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        except DomainError as exc:
            raise EngineIntegrityError(
                "Evidence contract could not be represented by the canonical domain model.",
                component="evidence_normalizer",
                operation="normalize",
                field="value",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if not isinstance(normalized, EvidenceItem):
            raise EngineIntegrityError(
                "Evidence contract conversion returned an unexpected domain type.",
                component="evidence_normalizer",
                operation="normalize",
                field="value",
                context={"received_type": type(normalized).__name__},
            )

        logger.debug(
            {
                "event": "evidence_normalized",
                "evidence_id": normalized.evidence_id,
                "source_file_id": normalized.source_file_id,
                "source_type": normalized.source_type,
                "has_confidence": normalized.confidence is not None,
            }
        )
        return normalized

    def normalize_many(
        self,
        values: Iterable[EvidenceInput],
        *,
        allow_empty: bool = True,
    ) -> tuple[EvidenceItem, ...]:
        """Normalize an ordered evidence collection with collection integrity checks."""
        announce_engine_action(
            printer,
            logger,
            component="evidence_normalizer",
            action="Normalizing evidence collection",
            event="evidence_normalizer_many_start",
        )

        if isinstance(values, (str, bytes, bytearray, Mapping, EvidenceContract, EvidenceItem)):
            raise UnsupportedEngineInputError(
                "normalize_many expects an iterable of evidence records, not a single evidence value.",
                component="evidence_normalizer",
                operation="normalize_many",
                field="values",
                context={"received_type": type(values).__name__},
            )

        try:
            iterator = iter(values)
        except TypeError as exc:
            raise UnsupportedEngineInputError(
                "Evidence collection must be iterable.",
                component="evidence_normalizer",
                operation="normalize_many",
                field="values",
                context={"received_type": type(values).__name__},
                cause=exc,
            ) from exc

        normalized: list[EvidenceItem] = []
        seen_ids: set[str] = set()
        source_signatures: dict[str, tuple[str, str, str]] = {}

        for index, item in enumerate(iterator):
            try:
                evidence = self.normalize(item)
            except EngineError as exc:
                raise type(exc)(
                    exc.message,
                    component=exc.component or "evidence_normalizer",
                    operation=exc.operation or "normalize_many",
                    field=exc.field or f"values[{index}]",
                    context=exc.context,
                    cause=exc.cause or exc,
                ) from exc

            if evidence.evidence_id in seen_ids:
                raise EngineIntegrityError(
                    "Normalized evidence collection contains duplicate evidence identifiers.",
                    component="evidence_normalizer",
                    operation="normalize_many",
                    field=f"values[{index}]",
                    context={"evidence_id": evidence.evidence_id},
                )
            seen_ids.add(evidence.evidence_id)

            signature = (
                evidence.hash_algorithm,
                evidence.source_hash,
                evidence.source_type,
            )
            previous = source_signatures.get(evidence.source_file_id)
            if previous is None:
                source_signatures[evidence.source_file_id] = signature
            elif previous != signature:
                raise EngineIntegrityError(
                    "One source_file_id resolves to inconsistent provenance in normalized evidence.",
                    component="evidence_normalizer",
                    operation="normalize_many",
                    field=f"values[{index}].source_file_id",
                    context={"source_file_id": evidence.source_file_id},
                )

            normalized.append(evidence)

        if not normalized and not allow_empty:
            raise EngineValidationError(
                "Evidence collection must contain at least one record.",
                component="evidence_normalizer",
                operation="normalize_many",
                field="values",
            )

        logger.debug(
            {
                "event": "evidence_collection_normalized",
                "evidence_count": len(normalized),
                "source_count": len(source_signatures),
            }
        )
        return tuple(normalized)


__all__ = [
    "EvidenceInput",
    "EvidenceNormalizer",
]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Evidence Normalizer Self-Test ===\n")
    printer.status("TEST", "Evidence normalizer module initialized", "info")

    digest = hashlib.sha256(b"normalizer-self-test").hexdigest()
    contract = EvidenceContract(
        evidence_id="EV-NORM-1",
        source_file_id="SRC-NORM-1",
        source_hash=digest,
        source_type="json",
        extracted_at="2026-09-02T00:00:00Z",
        extracted_value={"value": 1},
        logical_location={"path": "parameters[0]"},
        confidence=1.0,
    )
    normalizer = EvidenceNormalizer()
    item = normalizer.normalize(contract)
    assert item.evidence_id == "EV-NORM-1"
    assert normalizer.normalize(item) is item
    assert normalizer.normalize_many((contract,)) == (item,)
    printer.status("PASS", "Evidence contract/domain normalization", "success")

    print("\n=== Test ran successfully ===\n")