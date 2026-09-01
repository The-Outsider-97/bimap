"""
Versioned external Family Evidence aggregate for BIMAP.

The Family Evidence contract is the stable interchange representation consumed
by BIMAP after controlled extraction and before product-specific audit logic.
It follows the canonical family-evidence categories defined by the BIMAP
implementation specification: family identity, type catalogue, parameters,
formulas/references, materials, connectors, nested components, geometry
metrics, documentation, source manifest, and organization rules.

Each analyzable section contains ``EvidenceContract`` objects rather than
inventing a second evidence/provenance model. This keeps source identity,
content hashes, logical locations, extractor metadata, extracted values, and
confidence under the single evidence contract defined in ``evidence.py``.

Architectural boundary
----------------------
contracts.utils
contracts.versions
contracts.evidence
        ↑
contracts.family_evidence
        ↑
audit_engine.ingestion / normalization

This module MUST NOT import the audit engine, SLAI integration, API, workers,
reporting, or persistence. It defines a versioned DTO only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from .evidence import EvidenceContract
from .versions import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Family Evidence")
printer = PrettyPrinter()

_CONTRACT = ContractName.FAMILY_EVIDENCE.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]

_SECTION_NAMES: tuple[str, ...] = (
    "family_identity",
    "type_catalog",
    "parameters",
    "formulas",
    "materials",
    "connectors",
    "nested_components",
    "geometry_metrics",
    "documentation",
    "organization_rules",
)


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer evidence content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "family_evidence_method_start", "action": action})


def _normalize_manifest(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate/copy the package-level source manifest as deterministic JSON."""
    _announce("Normalizing Family Evidence source manifest")

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractValidationError(
            "source_manifest must be a mapping.",
            contract=_CONTRACT,
            field="source_manifest",
            context={"received_type": type(value).__name__},
        )

    primitive = to_json_primitive(value, contract=_CONTRACT, field="source_manifest")
    if not isinstance(primitive, dict):  # defensive: Mapping should become dict
        raise ContractValidationError(
            "source_manifest must serialize to a JSON object.",
            contract=_CONTRACT,
            field="source_manifest",
        )
    return primitive


def _normalize_evidence_sequence(
    values: Iterable[EvidenceContract | Mapping[str, Any]] | None,
    *,
    field: str,
) -> tuple[EvidenceContract, ...]:
    """Normalize one named Family Evidence section into EvidenceContract DTOs."""
    _announce(f"Normalizing Family Evidence section: {field}")

    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise ContractValidationError(
            "Evidence section must be a sequence of evidence objects.",
            contract=_CONTRACT,
            field=field,
            context={"received_type": type(values).__name__},
        )

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ContractValidationError(
            "Evidence section must be iterable.",
            contract=_CONTRACT,
            field=field,
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    result: list[EvidenceContract] = []
    local_ids: set[str] = set()

    for index, item in enumerate(iterator):
        if isinstance(item, EvidenceContract):
            normalized = item
        elif isinstance(item, Mapping):
            normalized = EvidenceContract.from_dict(item)
        else:
            raise ContractValidationError(
                "Evidence section contains an unsupported value.",
                contract=_CONTRACT,
                field=f"{field}[{index}]",
                context={"received_type": type(item).__name__},
            )

        if normalized.evidence_id in local_ids:
            raise ContractIntegrityError(
                "Evidence section contains duplicate evidence identifiers.",
                contract=_CONTRACT,
                field=field,
                context={"evidence_id": normalized.evidence_id},
            )

        local_ids.add(normalized.evidence_id)
        result.append(normalized)

    return tuple(result)


@dataclass(frozen=True, slots=True)
class FamilyEvidence:
    """
    Stable versioned aggregate of evidence describing one family audit package.

    The section vocabulary mirrors BIMAP's canonical Family Evidence Model but
    deliberately leaves the *values* inside individual ``EvidenceContract``
    objects. Consequently this aggregate does not redefine provenance,
    extraction confidence, source hashes, or logical locations.

    ``source_manifest`` is package-level metadata. Its exact extractor-specific
    fields are intentionally not hard-coded here because the implementation
    roadmap supports both an export-based MVP and a later Revit-native
    exporter. Extractor-specific manifest schemas can evolve independently
    while remaining deterministic JSON.
    """

    family_identity: tuple[EvidenceContract, ...] = ()
    type_catalog: tuple[EvidenceContract, ...] = ()
    parameters: tuple[EvidenceContract, ...] = ()
    formulas: tuple[EvidenceContract, ...] = ()
    materials: tuple[EvidenceContract, ...] = ()
    connectors: tuple[EvidenceContract, ...] = ()
    nested_components: tuple[EvidenceContract, ...] = ()
    geometry_metrics: tuple[EvidenceContract, ...] = ()
    documentation: tuple[EvidenceContract, ...] = ()
    organization_rules: tuple[EvidenceContract, ...] = ()

    source_manifest: Mapping[str, Any] = dataclass_field(default_factory=dict)
    schema_version: str = FAMILY_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating Family Evidence contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        normalized_sections: dict[str, tuple[EvidenceContract, ...]] = {}
        all_ids: dict[str, str] = {}

        for section_name in _SECTION_NAMES:
            section = _normalize_evidence_sequence(
                getattr(self, section_name),
                field=section_name,
            )
            normalized_sections[section_name] = section

            for item in section:
                previous_section = all_ids.get(item.evidence_id)
                if previous_section is not None:
                    raise ContractIntegrityError(
                        "Evidence identifier occurs in more than one Family Evidence section.",
                        contract=_CONTRACT,
                        field=section_name,
                        context={
                            "evidence_id": item.evidence_id,
                            "first_section": previous_section,
                            "duplicate_section": section_name,
                        },
                    )
                all_ids[item.evidence_id] = section_name

        manifest = _normalize_manifest(self.source_manifest)

        object.__setattr__(self, "schema_version", str(self.schema_version).strip())
        object.__setattr__(self, "source_manifest", manifest)
        for section_name, section in normalized_sections.items():
            object.__setattr__(self, section_name, section)

        logger.debug(
            {
                "event": "family_evidence_validated",
                "schema_version": self.schema_version,
                "evidence_count": len(all_ids),
                "populated_sections": tuple(
                    name for name, values in normalized_sections.items() if values
                ),
                "has_source_manifest": bool(manifest),
            }
        )

    def section(self, name: str) -> tuple[EvidenceContract, ...]:
        """Return one canonical Family Evidence section by exact section name."""
        _announce("Resolving Family Evidence section")

        if not isinstance(name, str):
            raise ContractValidationError(
                "Family Evidence section name must be a string.",
                contract=_CONTRACT,
                field="section",
                context={"received_type": type(name).__name__},
            )
        normalized = name.strip()
        if normalized not in _SECTION_NAMES:
            raise ContractValidationError(
                "Unknown Family Evidence section.",
                contract=_CONTRACT,
                field="section",
                context={
                    "received": normalized,
                    "allowed": _SECTION_NAMES,
                },
            )
        return getattr(self, normalized)

    def all_evidence(self) -> tuple[EvidenceContract, ...]:
        """Return all evidence in deterministic canonical section order."""
        _announce("Collecting all Family Evidence items")
        return tuple(
            evidence
            for section_name in _SECTION_NAMES
            for evidence in getattr(self, section_name)
        )

    def evidence_ids(self) -> tuple[str, ...]:
        """Return all evidence identifiers in deterministic section order."""
        _announce("Collecting Family Evidence identifiers")
        return tuple(item.evidence_id for item in self.all_evidence())

    def get_evidence(self, evidence_id: str) -> EvidenceContract | None:
        """Resolve an evidence object by its stable identifier."""
        _announce("Resolving Family Evidence item")

        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ContractValidationError(
                "evidence_id must be a non-empty string.",
                contract=_CONTRACT,
                field="evidence_id",
            )
        target = evidence_id.strip()
        for item in self.all_evidence():
            if item.evidence_id == target:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-ready Family Evidence representation."""
        _announce("Serializing Family Evidence contract")

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "source_manifest": to_json_primitive(
                self.source_manifest,
                contract=_CONTRACT,
                field="source_manifest",
            ),
        }
        for section_name in _SECTION_NAMES:
            payload[section_name] = [
                item.to_dict() for item in getattr(self, section_name)
            ]
        return payload

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the aggregate using BIMAP canonical JSON rules."""
        _announce("Encoding Family Evidence JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FamilyEvidence":
        """Parse a strict versioned Family Evidence mapping."""
        _announce("Deserializing Family Evidence contract")

        data = validate_contract_fields(
            payload,
            required=("schema_version",),
            optional=("source_manifest", *_SECTION_NAMES),
            contract=_CONTRACT,
        )

        kwargs: dict[str, Any] = {
            "schema_version": data["schema_version"],
            "source_manifest": data.get("source_manifest") or {},
        }
        for section_name in _SECTION_NAMES:
            raw = data.get(section_name, ())
            if raw is None:
                raw = ()
            kwargs[section_name] = raw
        return cls(**kwargs)

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "FamilyEvidence":
        """Decode canonical JSON and validate it as Family Evidence."""
        _announce("Decoding Family Evidence JSON")

        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Family Evidence JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


__all__ = ["FamilyEvidence"]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Family Evidence Contract Self-Test ===\n")
    printer.status("TEST", "Family Evidence contract module initialized", "info")

    digest = hashlib.sha256(b"family-evidence").hexdigest()
    identity = EvidenceContract(
        evidence_id="EV-FAM-0001",
        source_file_id="SRC-FAM-0001",
        source_hash=digest,
        source_type="json",
        logical_location={"path": "family_identity.name"},
        extracted_at="2026-09-01T00:00:00Z",
        extracted_value="ExampleFamily",
        confidence=1.0,
    )

    contract = FamilyEvidence(
        family_identity=(identity,),
        source_manifest={"source_file_id": "SRC-FAM-0001"},
    )
    assert contract.evidence_ids() == ("EV-FAM-0001",)
    assert contract.get_evidence("EV-FAM-0001") == identity
    assert FamilyEvidence.from_json(contract.to_json()) == contract
    printer.status("PASS", "Family Evidence round trip", "success")

    print("\n=== Test ran successfully ===\n")