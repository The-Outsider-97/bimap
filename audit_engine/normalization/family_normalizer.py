"""
Family Evidence normalization for deterministic RFA auditing.

BIMAP currently has a stable external ``FamilyEvidence`` contract and a stable
canonical ``EvidenceItem`` domain model, but it intentionally does not define a
second family-level domain aggregate.  This module therefore preserves the
contract's section semantics as an immutable engine-layer index over canonical
EvidenceItem objects instead of inventing a competing domain model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field as dataclass_field, fields as dataclass_fields
from types import MappingProxyType
from typing import Any

from .evidence_normalizer import EvidenceNormalizer
from ..context import AuditContext
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...contracts.evidence import EvidenceContract
from ...contracts.family_evidence import FamilyEvidence as FamilyEvidenceContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.evidence.models import EvidenceItem
from ...domain.products.models import ProductCode
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Family Normalizer")
printer = PrettyPrinter()

SerializedJSON = str | bytes | bytearray
FamilyEvidenceInput = FamilyEvidenceContract | Mapping[str, Any] | SerializedJSON


def _section_names(contract: FamilyEvidenceContract) -> tuple[str, ...]:
    """Derive evidence-section names from the authoritative contract dataclass.

    This deliberately avoids copying the contract module's private section-name
    tuple into the engine.  Current evidence sections are tuple-valued dataclass
    fields whose members, when present, are EvidenceContract instances.
    """
    announce_engine_action(
        printer,
        logger,
        component="family_normalizer",
        action="Resolving Family Evidence sections",
        event="family_normalizer_section_names_start",
    )

    result: list[str] = []
    for definition in dataclass_fields(contract):
        if definition.name in {"source_manifest", "schema_version"}:
            continue
        value = getattr(contract, definition.name)
        if not isinstance(value, tuple):
            continue
        if value and not all(isinstance(item, EvidenceContract) for item in value):
            continue
        result.append(definition.name)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class NormalizedFamilyEvidence:
    """Immutable normalized RFA evidence plus its canonical section index."""

    schema_version: str
    evidence_items: tuple[EvidenceItem, ...]
    section_evidence_ids: Mapping[str, tuple[str, ...]]
    source_manifest: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Validating normalized Family Evidence",
            event="normalized_family_evidence_validate_start",
            context={"evidence_count": len(self.evidence_items)},
        )

        schema_version = require_engine_text(
            self.schema_version,
            field="schema_version",
            error_type=EngineValidationError,
        )

        items = tuple(self.evidence_items)
        index: dict[str, EvidenceItem] = {}
        for position, item in enumerate(items):
            if not isinstance(item, EvidenceItem):
                raise UnsupportedEngineInputError(
                    "NormalizedFamilyEvidence accepts EvidenceItem values only.",
                    component="family_normalizer",
                    operation="validate_normalized_family",
                    field=f"evidence_items[{position}]",
                    context={"received_type": type(item).__name__},
                )
            if item.evidence_id in index:
                raise EngineIntegrityError(
                    "Normalized Family Evidence contains duplicate evidence identifiers.",
                    component="family_normalizer",
                    operation="validate_normalized_family",
                    field="evidence_items",
                    context={"evidence_id": item.evidence_id},
                )
            index[item.evidence_id] = item

        raw_sections = require_engine_mapping(
            self.section_evidence_ids,
            field="section_evidence_ids",
            error_type=EngineValidationError,
        )
        normalized_sections: dict[str, tuple[str, ...]] = {}
        assigned: dict[str, str] = {}

        for raw_name, raw_ids in raw_sections.items():
            name = require_engine_text(
                raw_name,
                field="section_evidence_ids.name",
                error_type=EngineValidationError,
            )
            if isinstance(raw_ids, (str, bytes, bytearray, Mapping)):
                raise EngineValidationError(
                    "Family evidence section index must contain evidence-ID sequences.",
                    component="family_normalizer",
                    operation="validate_normalized_family",
                    field=f"section_evidence_ids.{name}",
                    context={"received_type": type(raw_ids).__name__},
                )

            ordered: list[str] = []
            local_seen: set[str] = set()
            try:
                iterator = iter(raw_ids)
            except TypeError as exc:
                raise EngineValidationError(
                    "Family evidence section identifiers must be iterable.",
                    component="family_normalizer",
                    operation="validate_normalized_family",
                    field=f"section_evidence_ids.{name}",
                    cause=exc,
                ) from exc

            for position, raw_id in enumerate(iterator):
                evidence_id = require_engine_text(
                    raw_id,
                    field=f"section_evidence_ids.{name}[{position}]",
                    error_type=EngineValidationError,
                )
                if evidence_id in local_seen:
                    continue
                if evidence_id not in index:
                    raise EngineIntegrityError(
                        "Family evidence section references an unknown normalized evidence identifier.",
                        component="family_normalizer",
                        operation="validate_normalized_family",
                        field=f"section_evidence_ids.{name}",
                        context={"evidence_id": evidence_id},
                    )
                previous_section = assigned.get(evidence_id)
                if previous_section is not None and previous_section != name:
                    raise EngineIntegrityError(
                        "Normalized Family Evidence assigns one evidence identifier to multiple canonical sections.",
                        component="family_normalizer",
                        operation="validate_normalized_family",
                        field=f"section_evidence_ids.{name}",
                        context={
                            "evidence_id": evidence_id,
                            "first_section": previous_section,
                            "duplicate_section": name,
                        },
                    )
                assigned[evidence_id] = name
                local_seen.add(evidence_id)
                ordered.append(evidence_id)
            normalized_sections[name] = tuple(ordered)

        unassigned = tuple(item.evidence_id for item in items if item.evidence_id not in assigned)
        if unassigned:
            raise EngineIntegrityError(
                "Normalized Family Evidence contains evidence not represented by a canonical section.",
                component="family_normalizer",
                operation="validate_normalized_family",
                field="section_evidence_ids",
                context={"unassigned_evidence_ids": unassigned},
            )

        manifest = to_engine_primitive(self.source_manifest, field="source_manifest")
        if not isinstance(manifest, dict):
            raise EngineSerializationError(
                "Family source manifest must normalize to a JSON object.",
                component="family_normalizer",
                operation="validate_normalized_family",
                field="source_manifest",
            )

        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "evidence_items", items)
        object.__setattr__(self, "section_evidence_ids", MappingProxyType(normalized_sections))
        object.__setattr__(self, "source_manifest", MappingProxyType(dict(manifest)))

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_items)

    @property
    def section_names(self) -> tuple[str, ...]:
        return tuple(self.section_evidence_ids)

    def section(self, name: str) -> tuple[EvidenceItem, ...]:
        """Return canonical domain evidence for one Family Evidence section."""
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Resolving normalized Family Evidence section",
            event="normalized_family_evidence_section_start",
        )
        normalized = require_engine_text(
            name,
            field="section",
            error_type=EngineValidationError,
        )
        ids = self.section_evidence_ids.get(normalized)
        if ids is None:
            raise EngineValidationError(
                "Unknown normalized Family Evidence section.",
                component="family_normalizer",
                operation="section",
                field="section",
                context={"received": normalized, "available": self.section_names},
            )
        index = {item.evidence_id: item for item in self.evidence_items}
        return tuple(index[evidence_id] for evidence_id in ids)

    def get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        """Resolve normalized family evidence by stable evidence identifier."""
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Resolving normalized Family Evidence item",
            event="normalized_family_evidence_get_start",
        )
        target = require_engine_text(
            evidence_id,
            field="evidence_id",
            error_type=EngineValidationError,
        )
        for item in self.evidence_items:
            if item.evidence_id == target:
                return item
        return None

    def to_context(
        self,
        *,
        product_code: ProductCode | str = ProductCode.FAMILY_AUDIT,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditContext:
        """Create the generic deterministic AuditContext without losing sections."""
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Creating audit context from normalized Family Evidence",
            event="normalized_family_evidence_to_context_start",
            context={"evidence_count": self.evidence_count},
        )
        return AuditContext(
            product_code=product_code,
            evidence_items=self.evidence_items,
            evidence_groups=self.section_evidence_ids,
            source_manifest=self.source_manifest,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic engine primitives while preserving section identity."""
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Serializing normalized Family Evidence",
            event="normalized_family_evidence_to_dict_start",
            context={"evidence_count": self.evidence_count},
        )
        payload = {
            "schema_version": self.schema_version,
            "evidence": [item.to_dict() for item in self.evidence_items],
            "sections": {
                name: list(ids) for name, ids in self.section_evidence_ids.items()
            },
            "source_manifest": dict(self.source_manifest),
        }
        primitive = to_engine_primitive(payload, field="normalized_family_evidence")
        if not isinstance(primitive, dict):
            raise EngineSerializationError(
                "Normalized Family Evidence did not serialize to a JSON object.",
                component="family_normalizer",
                operation="to_dict",
                field="normalized_family_evidence",
            )
        return primitive


class FamilyNormalizer:
    """Normalize one versioned FamilyEvidence package for RFA audit logic."""

    def __init__(self, *, evidence_normalizer: EvidenceNormalizer | None = None) -> None:
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Initializing Family Evidence normalizer",
            event="family_normalizer_init_start",
        )
        self._evidence_normalizer = evidence_normalizer or EvidenceNormalizer()
        logger.debug({"event": "family_normalizer_initialized"})

    def parse(self, payload: FamilyEvidenceInput) -> FamilyEvidenceContract:
        """Return a validated FamilyEvidence contract without duplicating its schema."""
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Parsing Family Evidence for normalization",
            event="family_normalizer_parse_start",
            context={"input_type": type(payload).__name__},
        )
        if isinstance(payload, FamilyEvidenceContract):
            return payload
        try:
            if isinstance(payload, Mapping):
                return FamilyEvidenceContract.from_dict(payload)
            if isinstance(payload, (str, bytes, bytearray)):
                return FamilyEvidenceContract.from_json(payload)
        except ContractError as exc:
            raise EngineValidationError(
                "Family Evidence does not satisfy the canonical external contract.",
                component="family_normalizer",
                operation="parse",
                field="payload",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        raise UnsupportedEngineInputError(
            "FamilyNormalizer input must be FamilyEvidence, mapping, or JSON.",
            component="family_normalizer",
            operation="parse",
            field="payload",
            context={"received_type": type(payload).__name__},
        )

    def normalize(self, payload: FamilyEvidenceInput) -> NormalizedFamilyEvidence:
        """Convert a FamilyEvidence package to canonical domain evidence once."""
        announce_engine_action(
            printer,
            logger,
            component="family_normalizer",
            action="Normalizing Family Evidence package",
            event="family_normalizer_normalize_start",
            context={"input_type": type(payload).__name__},
        )

        contract = self.parse(payload)
        contract_items = tuple(contract.all_evidence())
        normalized_items = self._evidence_normalizer.normalize_many(contract_items)

        section_index = {
            name: tuple(item.evidence_id for item in contract.section(name))
            for name in _section_names(contract)
        }

        result = NormalizedFamilyEvidence(
            schema_version=contract.schema_version,
            evidence_items=normalized_items,
            section_evidence_ids=section_index,
            source_manifest=contract.source_manifest,
        )
        logger.debug(
            {
                "event": "family_evidence_normalized",
                "schema_version": result.schema_version,
                "evidence_count": result.evidence_count,
                "section_count": len(result.section_names),
            }
        )
        return result


__all__ = [
    "FamilyEvidenceInput",
    "NormalizedFamilyEvidence",
    "FamilyNormalizer",
]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Family Normalizer Self-Test ===\n")
    printer.status("TEST", "Family normalizer module initialized", "info")

    digest = hashlib.sha256(b"family-normalizer").hexdigest()
    family = FamilyEvidenceContract(
        family_identity=(
            EvidenceContract(
                evidence_id="EV-FAMILY-NORM-1",
                source_file_id="SRC-FAMILY-NORM-1",
                source_hash=digest,
                source_type="json",
                extracted_at="2026-09-02T00:00:00Z",
                extracted_value="ExampleFamily",
                logical_location={"path": "family_identity.name"},
                confidence=1.0,
            ),
        ),
        source_manifest={"extractor": "self-test"},
    )
    normalized = FamilyNormalizer().normalize(family)
    assert normalized.evidence_count == 1
    assert normalized.section("family_identity")[0].evidence_id == "EV-FAMILY-NORM-1"
    assert normalized.to_context().evidence_count == 1
    printer.status("PASS", "Family Evidence normalization", "success")

    print("\n=== Test ran successfully ===\n")