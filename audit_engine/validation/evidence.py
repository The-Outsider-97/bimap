"""
Post-normalization evidence validation for the deterministic BIMAP audit engine.

This module validates *canonical* :class:`~bimap.audit_engine.context.AuditContext`
evidence.  It intentionally does not parse uploads, open files, perform malware
screening, infer missing evidence, or redefine the invariants already owned by
``EvidenceItem``/``Provenance``/``AuditContext``.

The validation boundary has three responsibilities:

1. expose measurable metadata coverage for accepted evidence without treating
   optional metadata as a pass/fail quality score;
2. resolve evidence-reference collections against the exact ``AuditContext``;
3. optionally verify caller-supplied source bytes against canonical provenance
   hashes, without performing file I/O itself.

This distinction matters academically and operationally: structural validity,
provenance integrity, metadata richness, and analytical sufficiency are related
but are not interchangeable concepts.  In particular, absence of optional
confidence/location/extractor metadata is reported rather than silently promoted
into a fabricated audit failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...domain.evidence.models import EvidenceItem
from ...domain.utils.domain_errors import DomainError
from ..context import AuditContext
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Evidence Validation")
printer = PrettyPrinter()

_COMPONENT = "validation_evidence"


def _ratio(numerator: int, denominator: int) -> float | None:
    """Return a bounded ratio, or ``None`` when no denominator exists."""
    if denominator == 0:
        return None
    return numerator / denominator


def _normalize_reference_iterable(
    values: Iterable[str],
    *,
    field: str,
) -> tuple[str, ...]:
    """Normalize evidence references with stable de-duplication."""
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            f"{field} must be an iterable of evidence identifiers.",
            component=_COMPONENT,
            operation="normalize_references",
            field=field,
            context={"received_type": type(values).__name__},
        )

    try:
        raw_values = tuple(values)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            f"{field} must be iterable.",
            component=_COMPONENT,
            operation="normalize_references",
            field=field,
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    # Sets have no caller-significant order. Sorting them avoids hash-order
    # changes leaking into deterministic validation output.
    if isinstance(values, (set, frozenset)):
        raw_values = tuple(sorted(raw_values, key=str))

    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw_value in enumerate(raw_values):
        evidence_id = require_engine_text(
            raw_value,
            field=f"{field}[{index}]",
            error_type=EngineValidationError,
        )
        if evidence_id not in seen:
            seen.add(evidence_id)
            normalized.append(evidence_id)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class EvidenceValidationSummary:
    """Descriptive metadata coverage for one accepted evidence context.

    Every count is descriptive.  The optional provenance/extraction fields are
    not converted into pass/fail semantics here because the current canonical
    evidence model deliberately permits them to be absent.
    """

    evidence_count: int
    source_count: int
    logical_location_count: int
    extraction_confidence_count: int
    extractor_name_count: int
    extractor_version_count: int
    schema_version_count: int
    traceability_count: int
    source_timestamp_count: int
    original_filename_count: int
    source_version_count: int
    non_null_value_count: int

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating evidence validation summary",
            event="evidence_validation_summary_validate_start",
        )
        counts = {
            "evidence_count": self.evidence_count,
            "source_count": self.source_count,
            "logical_location_count": self.logical_location_count,
            "extraction_confidence_count": self.extraction_confidence_count,
            "extractor_name_count": self.extractor_name_count,
            "extractor_version_count": self.extractor_version_count,
            "schema_version_count": self.schema_version_count,
            "traceability_count": self.traceability_count,
            "source_timestamp_count": self.source_timestamp_count,
            "original_filename_count": self.original_filename_count,
            "source_version_count": self.source_version_count,
            "non_null_value_count": self.non_null_value_count,
        }
        for field, value in counts.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EngineIntegrityError(
                    "Evidence validation summary contains an invalid count.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={"received_type": type(value).__name__},
                )

        for field in (
            "logical_location_count",
            "extraction_confidence_count",
            "extractor_name_count",
            "extractor_version_count",
            "schema_version_count",
            "traceability_count",
            "source_timestamp_count",
            "original_filename_count",
            "source_version_count",
            "non_null_value_count",
        ):
            if getattr(self, field) > self.evidence_count:
                raise EngineIntegrityError(
                    "Evidence metadata count exceeds total evidence count.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={
                        "metadata_count": getattr(self, field),
                        "evidence_count": self.evidence_count,
                    },
                )
        if self.source_count > self.evidence_count:
            raise EngineIntegrityError(
                "Evidence source count exceeds evidence count.",
                component=_COMPONENT,
                operation="validate_summary",
                field="source_count",
                context={
                    "source_count": self.source_count,
                    "evidence_count": self.evidence_count,
                },
            )

    @property
    def metadata_coverage(self) -> Mapping[str, float | None]:
        """Return per-field descriptive coverage ratios over evidence records."""
        return MappingProxyType(
            {
                "logical_location": _ratio(self.logical_location_count, self.evidence_count),
                "extraction_confidence": _ratio(
                    self.extraction_confidence_count,
                    self.evidence_count,
                ),
                "extractor_name": _ratio(self.extractor_name_count, self.evidence_count),
                "extractor_version": _ratio(
                    self.extractor_version_count,
                    self.evidence_count,
                ),
                "schema_version": _ratio(self.schema_version_count, self.evidence_count),
                "traceability_refs": _ratio(self.traceability_count, self.evidence_count),
                "source_timestamp": _ratio(
                    self.source_timestamp_count,
                    self.evidence_count,
                ),
                "original_filename": _ratio(
                    self.original_filename_count,
                    self.evidence_count,
                ),
                "source_version": _ratio(self.source_version_count, self.evidence_count),
                "non_null_value": _ratio(self.non_null_value_count, self.evidence_count),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic primitive summary data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing evidence validation summary",
            event="evidence_validation_summary_to_dict_start",
        )
        return {
            "evidence_count": self.evidence_count,
            "source_count": self.source_count,
            "logical_location_count": self.logical_location_count,
            "extraction_confidence_count": self.extraction_confidence_count,
            "extractor_name_count": self.extractor_name_count,
            "extractor_version_count": self.extractor_version_count,
            "schema_version_count": self.schema_version_count,
            "traceability_count": self.traceability_count,
            "source_timestamp_count": self.source_timestamp_count,
            "original_filename_count": self.original_filename_count,
            "source_version_count": self.source_version_count,
            "non_null_value_count": self.non_null_value_count,
            "metadata_coverage": dict(self.metadata_coverage),
        }


@dataclass(frozen=True, slots=True)
class EvidenceValidationResult:
    """Immutable structural view produced by :class:`EvidenceValidation`."""

    summary: EvidenceValidationSummary
    source_to_evidence: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating evidence validation result",
            event="evidence_validation_result_validate_start",
        )
        if not isinstance(self.summary, EvidenceValidationSummary):
            raise EngineIntegrityError(
                "EvidenceValidationResult requires EvidenceValidationSummary.",
                component=_COMPONENT,
                operation="validate_result",
                field="summary",
                context={"received_type": type(self.summary).__name__},
            )
        if not isinstance(self.source_to_evidence, Mapping):
            raise EngineIntegrityError(
                "source_to_evidence must be a mapping.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_to_evidence",
                context={"received_type": type(self.source_to_evidence).__name__},
            )
        normalized = {
            str(source_id): tuple(evidence_ids)
            for source_id, evidence_ids in self.source_to_evidence.items()
        }
        if len(normalized) != self.summary.source_count:
            raise EngineIntegrityError(
                "Evidence source index count does not match validation summary.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_to_evidence",
                context={
                    "index_source_count": len(normalized),
                    "summary_source_count": self.summary.source_count,
                },
            )
        indexed_ids = tuple(
            evidence_id
            for evidence_ids in normalized.values()
            for evidence_id in evidence_ids
        )
        if len(indexed_ids) != self.summary.evidence_count or len(set(indexed_ids)) != len(indexed_ids):
            raise EngineIntegrityError(
                "Evidence source index must contain every accepted evidence ID exactly once.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_to_evidence",
                context={
                    "indexed_evidence_count": len(indexed_ids),
                    "summary_evidence_count": self.summary.evidence_count,
                },
            )
        object.__setattr__(self, "source_to_evidence", MappingProxyType(normalized))

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe validation data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing evidence validation result",
            event="evidence_validation_result_to_dict_start",
        )
        payload = {
            "summary": self.summary.to_dict(),
            "source_to_evidence": {
                source_id: list(evidence_ids)
                for source_id, evidence_ids in self.source_to_evidence.items()
            },
        }
        primitive = to_engine_primitive(payload, field="evidence_validation_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "Evidence validation result did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="evidence_validation_result",
            )
        return primitive


@dataclass(frozen=True, slots=True)
class SourceIntegrityValidationResult:
    """Result of optional source-byte hash verification."""

    source_count: int
    verified_source_ids: tuple[str, ...]
    unchecked_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating source integrity result",
            event="source_integrity_result_validate_start",
        )
        if not isinstance(self.source_count, int) or isinstance(self.source_count, bool) or self.source_count < 0:
            raise EngineIntegrityError(
                "Source integrity result contains an invalid source count.",
                component=_COMPONENT,
                operation="validate_source_integrity_result",
                field="source_count",
            )
        if len(self.verified_source_ids) + len(self.unchecked_source_ids) != self.source_count:
            raise EngineIntegrityError(
                "Source integrity result does not partition the accepted source set.",
                component=_COMPONENT,
                operation="validate_source_integrity_result",
                field="source_count",
            )
        if set(self.verified_source_ids).intersection(self.unchecked_source_ids):
            raise EngineIntegrityError(
                "A source cannot be both verified and unchecked.",
                component=_COMPONENT,
                operation="validate_source_integrity_result",
                field="verified_source_ids",
            )

    @property
    def complete(self) -> bool:
        """Return whether every accepted source was byte-verified."""
        return not self.unchecked_source_ids

    @property
    def verification_coverage(self) -> float | None:
        """Return verified-source proportion, or ``None`` for an empty context."""
        return _ratio(len(self.verified_source_ids), self.source_count)

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic primitive verification data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing source integrity result",
            event="source_integrity_result_to_dict_start",
        )
        return {
            "source_count": self.source_count,
            "verified_source_ids": list(self.verified_source_ids),
            "unchecked_source_ids": list(self.unchecked_source_ids),
            "complete": self.complete,
            "verification_coverage": self.verification_coverage,
        }


class EvidenceValidation:
    """Validate accepted evidence references and optional cryptographic integrity.

    ``AuditContext`` remains authoritative for canonical evidence structure.  This
    class intentionally consumes it instead of reconstructing a parallel evidence
    model or repeating its normalization policy.
    """

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing evidence validation",
            event="evidence_validation_init_start",
        )
        logger.debug({"event": "evidence_validation_initialized"})

    @staticmethod
    def _require_context(context: AuditContext, *, operation: str) -> AuditContext:
        if not isinstance(context, AuditContext):
            raise UnsupportedEngineInputError(
                "Evidence validation requires an AuditContext.",
                component=_COMPONENT,
                operation=operation,
                field="context",
                context={"received_type": type(context).__name__},
            )
        return context

    @staticmethod
    def _build_source_index(context: AuditContext) -> dict[str, tuple[EvidenceItem, ...]]:
        grouped: dict[str, list[EvidenceItem]] = {}
        for item in context.evidence_items:
            grouped.setdefault(item.source_file_id, []).append(item)
        return {source_id: tuple(items) for source_id, items in grouped.items()}

    def validate(self, context: AuditContext) -> EvidenceValidationResult:
        """Summarize accepted evidence and expose its stable source index."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating canonical audit evidence",
            event="evidence_validation_validate_start",
        )
        audit_context = self._require_context(context, operation="validate")

        # AuditContext/EvidenceItem already own identity, hash-shape, confidence,
        # source-signature and JSON-freezing invariants.  Re-running those rules
        # here would create a second source of truth.  We only inspect the stable
        # canonical values needed for analytical validation/coverage.
        items = audit_context.evidence_items
        source_index = self._build_source_index(audit_context)

        summary = EvidenceValidationSummary(
            evidence_count=len(items),
            source_count=len(source_index),
            logical_location_count=sum(item.logical_location is not None for item in items),
            extraction_confidence_count=sum(
                item.has_extraction_confidence for item in items
            ),
            extractor_name_count=sum(
                item.provenance.extractor_name is not None for item in items
            ),
            extractor_version_count=sum(
                item.provenance.extractor_version is not None for item in items
            ),
            schema_version_count=sum(
                item.provenance.schema_version is not None for item in items
            ),
            traceability_count=sum(
                bool(item.provenance.traceability_refs) for item in items
            ),
            source_timestamp_count=sum(
                item.provenance.source_timestamp is not None for item in items
            ),
            original_filename_count=sum(
                item.provenance.source.original_filename is not None for item in items
            ),
            source_version_count=sum(
                item.provenance.source.source_version is not None for item in items
            ),
            non_null_value_count=sum(item.extracted_value is not None for item in items),
        )
        result = EvidenceValidationResult(
            summary=summary,
            source_to_evidence=MappingProxyType(
                {
                    source_id: tuple(item.evidence_id for item in source_items)
                    for source_id, source_items in source_index.items()
                }
            ),
        )
        logger.info(
            {
                "event": "canonical_evidence_validated",
                "evidence_count": summary.evidence_count,
                "source_count": summary.source_count,
            }
        )
        return result

    def resolve_references(
        self,
        references: Iterable[str],
        *,
        context: AuditContext,
        field: str = "evidence_refs",
        allow_empty: bool = True,
    ) -> tuple[str, ...]:
        """Normalize evidence references and require each ID to exist in context."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving evidence references",
            event="evidence_validation_resolve_references_start",
            context={"field": field},
        )
        audit_context = self._require_context(context, operation="resolve_references")
        normalized = _normalize_reference_iterable(references, field=field)
        if not normalized and not allow_empty:
            raise EngineValidationError(
                "Evidence reference collection must not be empty.",
                component=_COMPONENT,
                operation="resolve_references",
                field=field,
            )

        accepted = set(audit_context.evidence_ids)
        unresolved = tuple(
            evidence_id for evidence_id in normalized if evidence_id not in accepted
        )
        if unresolved:
            raise EngineIntegrityError(
                "Evidence references do not resolve in the accepted AuditContext.",
                component=_COMPONENT,
                operation="resolve_references",
                field=field,
                context={"unresolved_evidence_refs": unresolved},
            )
        return normalized

    def verify_source_bytes(
        self,
        context: AuditContext,
        source_payloads: Mapping[str, bytes | bytearray | memoryview],
        *,
        require_all_sources: bool = False,
    ) -> SourceIntegrityValidationResult:
        """Verify caller-supplied source bytes against canonical provenance hashes.

        No files are opened here.  The caller is responsible for obtaining the
        bytes through the appropriate storage/application boundary.  Because
        ``AuditContext`` already guarantees one provenance signature per
        ``source_file_id``, one representative EvidenceItem is sufficient to
        verify each supplied source payload.
        """
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Verifying evidence source bytes",
            event="evidence_validation_verify_source_bytes_start",
            context={"require_all_sources": require_all_sources},
        )
        audit_context = self._require_context(context, operation="verify_source_bytes")
        payload_mapping = require_engine_mapping(
            source_payloads,
            field="source_payloads",
            error_type=EngineValidationError,
        )
        source_index = self._build_source_index(audit_context)
        accepted_source_ids = tuple(source_index)
        accepted_source_set = set(accepted_source_ids)

        unknown_sources = tuple(
            source_id for source_id in payload_mapping if source_id not in accepted_source_set
        )
        if unknown_sources:
            raise EngineIntegrityError(
                "Source-byte verification received source identifiers absent from AuditContext.",
                component=_COMPONENT,
                operation="verify_source_bytes",
                field="source_payloads",
                context={"unknown_source_file_ids": unknown_sources},
            )

        missing_sources = tuple(
            source_id for source_id in accepted_source_ids if source_id not in payload_mapping
        )
        if require_all_sources and missing_sources:
            raise EngineValidationError(
                "Source-byte verification requires payloads for every accepted source.",
                component=_COMPONENT,
                operation="verify_source_bytes",
                field="source_payloads",
                context={"missing_source_file_ids": missing_sources},
            )

        verified: list[str] = []
        for source_id in accepted_source_ids:
            if source_id not in payload_mapping:
                continue
            raw_payload = payload_mapping[source_id]
            if not isinstance(raw_payload, (bytes, bytearray, memoryview)):
                raise UnsupportedEngineInputError(
                    "Source payloads must be bytes-like values.",
                    component=_COMPONENT,
                    operation="verify_source_bytes",
                    field=f"source_payloads.{source_id}",
                    context={"received_type": type(raw_payload).__name__},
                )

            representative = source_index[source_id][0]
            try:
                representative.assert_source_integrity(bytes(raw_payload))
            except DomainError as exc:
                raise EngineIntegrityError(
                    "Source payload does not match canonical evidence provenance.",
                    component=_COMPONENT,
                    operation="verify_source_bytes",
                    field=f"source_payloads.{source_id}",
                    context={"source_file_id": source_id, **lower_error_context(exc)},
                    cause=exc,
                ) from exc
            verified.append(source_id)

        verified_set = set(verified)
        unchecked = tuple(
            source_id for source_id in accepted_source_ids if source_id not in verified_set
        )
        result = SourceIntegrityValidationResult(
            source_count=len(accepted_source_ids),
            verified_source_ids=tuple(verified),
            unchecked_source_ids=unchecked,
        )
        logger.info(
            {
                "event": "evidence_source_bytes_verified",
                "source_count": result.source_count,
                "verified_source_count": len(result.verified_source_ids),
                "unchecked_source_count": len(result.unchecked_source_ids),
            }
        )
        return result


# Backward-compatible alias for the typo present in the original scaffold.
# New code should use ``EvidenceValidation``.
EvidenceValdation = EvidenceValidation


__all__ = [
    "EvidenceValidationSummary",
    "EvidenceValidationResult",
    "SourceIntegrityValidationResult",
    "EvidenceValidation",
    "EvidenceValdation",
]


if __name__ == "__main__":
    from ...domain.products.models import ProductCode

    print("\n=== Running Evidence Validation Self-Test ===\n")
    printer.status("TEST", "Evidence validation module initialized", "info")

    context = AuditContext(product_code=ProductCode.FAMILY_AUDIT)
    validator = EvidenceValidation()
    result = validator.validate(context)
    assert result.summary.evidence_count == 0
    assert result.summary.metadata_coverage["logical_location"] is None
    assert validator.resolve_references((), context=context) == ()
    integrity = validator.verify_source_bytes(context, {}, require_all_sources=True)
    assert integrity.complete
    printer.status("PASS", "Empty-context evidence validation", "success")

    print("\n=== Test ran successfully ===\n")