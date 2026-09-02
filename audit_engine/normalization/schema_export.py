"""
Deterministic normalization of BIMAP requirement-matrix exports.

The initial scaffold described this module as converting extracted/client BIM
requirements into canonical domain requirement models.  The current repository
does not yet provide such a stable domain model: ``domain/requirements/models.py``
is still a scaffold, and ``contracts.requirement`` explicitly forbids inventing
a domain conversion until that model is stabilized.

Accordingly, this module fails conservatively rather than fabricating domain
semantics.  It normalizes already structured requirement rows into the existing
versioned ``RequirementContract``, validates collection-level identity and
optional evidence-reference integrity, and exposes deterministic JSON-ready
exports for downstream audit/reporting work.

This is *not* BIMAP's JSON Schema generator.  Authoritative external JSON Schema
generation remains exclusively owned by ``contracts/schema_export.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...contracts.requirement import RequirementContract
from ...contracts.utils.contracts_errors import ContractError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Requirement Schema Export Normalizer")
printer = PrettyPrinter()

SerializedJSON = str | bytes | bytearray
RequirementInput = RequirementContract | Mapping[str, Any] | SerializedJSON


@dataclass(frozen=True, slots=True)
class NormalizedRequirementSet:
    """Immutable, duplicate-free collection of validated RequirementContract rows."""

    requirements: tuple[RequirementContract, ...] = ()

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Validating normalized requirement set",
            event="normalized_requirement_set_validate_start",
            context={"requirement_count": len(self.requirements)},
        )

        items = tuple(self.requirements)
        seen: set[str] = set()
        for index, requirement in enumerate(items):
            if not isinstance(requirement, RequirementContract):
                raise UnsupportedEngineInputError(
                    "NormalizedRequirementSet accepts RequirementContract values only.",
                    component="schema_export",
                    operation="validate_requirement_set",
                    field=f"requirements[{index}]",
                    context={"received_type": type(requirement).__name__},
                )
            if requirement.requirement_id in seen:
                raise EngineIntegrityError(
                    "Requirement set contains duplicate requirement identifiers.",
                    component="schema_export",
                    operation="validate_requirement_set",
                    field="requirements",
                    context={"requirement_id": requirement.requirement_id},
                )
            seen.add(requirement.requirement_id)
        object.__setattr__(self, "requirements", items)

    @property
    def requirement_count(self) -> int:
        return len(self.requirements)

    @property
    def requirement_ids(self) -> tuple[str, ...]:
        return tuple(item.requirement_id for item in self.requirements)

    def get(self, requirement_id: str) -> RequirementContract | None:
        """Resolve one normalized requirement by stable identifier."""
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Resolving normalized requirement",
            event="normalized_requirement_set_get_start",
        )
        target = require_engine_text(
            requirement_id,
            field="requirement_id",
            error_type=EngineValidationError,
        )
        for item in self.requirements:
            if item.requirement_id == target:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-ready requirement rows."""
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Serializing normalized requirement set",
            event="normalized_requirement_set_to_dict_start",
            context={"requirement_count": self.requirement_count},
        )
        payload = {
            "requirements": [item.to_dict() for item in self.requirements],
        }
        primitive = to_engine_primitive(payload, field="normalized_requirements")
        if not isinstance(primitive, dict):
            raise EngineSerializationError(
                "Normalized requirement set did not serialize to a JSON object.",
                component="schema_export",
                operation="to_dict",
                field="normalized_requirements",
            )
        return primitive


class SchemaExporter:
    """Normalize structured BIMAP requirement exports without inventing a domain model.

    The class name is retained for compatibility with the original Level-4
    scaffold.  The authoritative JSON Schema exporter is
    ``contracts.schema_export.SchemaExporter`` and is intentionally not wrapped
    or duplicated here.
    """

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Initializing requirement schema-export normalizer",
            event="schema_export_normalizer_init_start",
        )
        logger.debug({"event": "schema_export_normalizer_initialized"})

    def normalize(self, payload: RequirementInput) -> RequirementContract:
        """Normalize one structured requirement row to the versioned contract."""
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Normalizing requirement export row",
            event="schema_export_normalize_start",
            context={"input_type": type(payload).__name__},
        )

        if isinstance(payload, RequirementContract):
            return payload

        try:
            if isinstance(payload, Mapping):
                return RequirementContract.from_dict(payload)
            if isinstance(payload, (str, bytes, bytearray)):
                return RequirementContract.from_json(payload)
        except ContractError as exc:
            raise EngineValidationError(
                "Requirement export does not satisfy the canonical Requirement contract.",
                component="schema_export",
                operation="normalize",
                field="payload",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        raise UnsupportedEngineInputError(
            "Requirement export input must be RequirementContract, mapping, or JSON.",
            component="schema_export",
            operation="normalize",
            field="payload",
            context={"received_type": type(payload).__name__},
        )

    def normalize_many(
        self,
        payloads: Iterable[RequirementInput] | RequirementInput,
        *,
        known_evidence_ids: Iterable[str] | None = None,
        allow_empty: bool = True,
    ) -> NormalizedRequirementSet:
        """Normalize requirement rows and optionally verify evidence references.

        ``known_evidence_ids`` is optional because requirement rows may be
        normalized before their evidence aggregate is attached.  When supplied,
        every ``evidence_ref`` must resolve; the normalizer never silently drops
        dangling references.
        """
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Normalizing requirement export collection",
            event="schema_export_normalize_many_start",
        )

        if isinstance(payloads, (RequirementContract, Mapping, str, bytes, bytearray)):
            candidates: tuple[RequirementInput, ...] = (cast(RequirementInput, payloads),)
        else:
            try:
                candidates = tuple(cast(Iterable[RequirementInput], payloads))
            except TypeError as exc:
                raise UnsupportedEngineInputError(
                    "Requirement export collection must be iterable.",
                    component="schema_export",
                    operation="normalize_many",
                    field="payloads",
                    context={"received_type": type(payloads).__name__},
                    cause=exc,
                ) from exc

        if not candidates and not allow_empty:
            raise EngineValidationError(
                "Requirement export collection must contain at least one row.",
                component="schema_export",
                operation="normalize_many",
                field="payloads",
            )

        normalized: list[RequirementContract] = []
        seen_ids: set[str] = set()
        for index, candidate in enumerate(candidates):
            try:
                requirement = self.normalize(candidate)
            except EngineError as exc:
                raise EngineValidationError(
                    "Requirement export collection contains an invalid row.",
                    component="schema_export",
                    operation="normalize_many",
                    field=f"payloads[{index}]",
                    context={
                        "lower_error_type": type(exc).__name__,
                        "lower_error_code": exc.code,
                    },
                    cause=exc,
                ) from exc

            if requirement.requirement_id in seen_ids:
                raise EngineIntegrityError(
                    "Requirement export collection contains duplicate requirement identifiers.",
                    component="schema_export",
                    operation="normalize_many",
                    field=f"payloads[{index}].requirement_id",
                    context={"requirement_id": requirement.requirement_id},
                )
            seen_ids.add(requirement.requirement_id)
            normalized.append(requirement)

        known_ids = self._normalize_known_evidence_ids(known_evidence_ids)
        if known_ids is not None:
            self._validate_evidence_references(normalized, known_ids=known_ids)

        result = NormalizedRequirementSet(tuple(normalized))
        logger.debug(
            {
                "event": "requirement_export_collection_normalized",
                "requirement_count": result.requirement_count,
                "evidence_reference_validation": known_ids is not None,
            }
        )
        return result

    def export(
        self,
        payloads: Iterable[RequirementInput] | RequirementInput,
        *,
        known_evidence_ids: Iterable[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return canonical JSON-ready RequirementContract rows in input order."""
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Exporting normalized requirement rows",
            event="schema_export_export_start",
        )
        normalized = self.normalize_many(
            payloads,
            known_evidence_ids=known_evidence_ids,
        )
        rows = tuple(item.to_dict() for item in normalized.requirements)
        primitive = to_engine_primitive(rows, field="requirement_export")
        if not isinstance(primitive, list):
            # Tuple normalization through the canonical JSON layer becomes list.
            raise EngineSerializationError(
                "Requirement export did not normalize to a JSON array.",
                component="schema_export",
                operation="export",
                field="requirement_export",
                context={"received_type": type(primitive).__name__},
            )
        return tuple(dict(item) for item in primitive)

    def _normalize_known_evidence_ids(
        self,
        values: Iterable[str] | None,
    ) -> set[str] | None:
        """Normalize an optional evidence-ID universe for cross-reference checks."""
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Normalizing known evidence identifiers",
            event="schema_export_known_evidence_start",
        )
        if values is None:
            return None
        if isinstance(values, (str, bytes, bytearray, Mapping)):
            raise EngineValidationError(
                "known_evidence_ids must be an iterable of identifiers.",
                component="schema_export",
                operation="normalize_known_evidence_ids",
                field="known_evidence_ids",
                context={"received_type": type(values).__name__},
            )
        try:
            iterator = iter(values)
        except TypeError as exc:
            raise EngineValidationError(
                "known_evidence_ids must be iterable.",
                component="schema_export",
                operation="normalize_known_evidence_ids",
                field="known_evidence_ids",
                cause=exc,
            ) from exc

        result: set[str] = set()
        for index, raw_id in enumerate(iterator):
            result.add(
                require_engine_text(
                    raw_id,
                    field=f"known_evidence_ids[{index}]",
                    error_type=EngineValidationError,
                )
            )
        return result

    def _validate_evidence_references(
        self,
        requirements: Iterable[RequirementContract],
        *,
        known_ids: set[str],
    ) -> None:
        """Require every referenced evidence identifier to resolve."""
        announce_engine_action(
            printer,
            logger,
            component="schema_export",
            action="Validating requirement evidence references",
            event="schema_export_validate_refs_start",
            context={"known_evidence_count": len(known_ids)},
        )
        for requirement in requirements:
            missing = tuple(
                ref for ref in requirement.evidence_refs if ref not in known_ids
            )
            if missing:
                raise EngineIntegrityError(
                    "Requirement export contains evidence references absent from the normalized evidence set.",
                    component="schema_export",
                    operation="validate_evidence_references",
                    field="evidence_refs",
                    context={
                        "requirement_id": requirement.requirement_id,
                        "missing_evidence_refs": missing,
                    },
                )


__all__ = [
    "RequirementInput",
    "NormalizedRequirementSet",
    "SchemaExporter",
]


if __name__ == "__main__":
    print("\n=== Running Requirement Schema Export Normalizer Self-Test ===\n")
    printer.status("TEST", "Requirement schema-export normalizer initialized", "info")

    exporter = SchemaExporter()
    requirement = RequirementContract(
        requirement_id="REQ-NORM-1",
        source_requirement="Required parameter shall be present.",
        evidence_refs=("EV-NORM-1",),
        assessment="fail", # type: ignore
        automation_type="deterministic", # type: ignore
        confidence=1.0,
        impact="Required information is absent.",
        recommended_action="Provide the required parameter and re-run the audit.",
    )
    result = exporter.normalize_many(
        (requirement,),
        known_evidence_ids=("EV-NORM-1",),
    )
    assert result.requirement_ids == ("REQ-NORM-1",)
    assert exporter.export((requirement,))[0]["requirement_id"] == "REQ-NORM-1"
    printer.status("PASS", "Requirement export normalization", "success")

    print("\n=== Test ran successfully ===\n")