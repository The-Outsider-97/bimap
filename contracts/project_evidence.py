"""
Versioned external Project Evidence aggregate for BIMAP.

This contract represents project-level evidence supplied to BIM QA and the
Combined Audit. Its section vocabulary follows the implementation
specification's project evidence groups: requirements, schedules, registers,
model-QA evidence, IFC/openBIM evidence, images, and optional references to
family evidence used for cross-scope analysis.

All analyzable project items are represented through ``EvidenceContract`` so
source hashes, source identity, logical location, extraction metadata, and
confidence are owned once by the shared evidence contract.

Architectural boundary
----------------------
contracts.utils
contracts.versions
contracts.evidence
        ↑
contracts.project_evidence
        ↑
audit_engine.ingestion / normalization

The domain ``ProjectEvidence`` aggregate stores normalized evidence without the
external section partition. This contract can therefore convert *to* the domain
aggregate, but it intentionally does not fabricate a reverse section mapping
from a flat domain aggregate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from ..domain.evidence.project_evidence import ProjectEvidence as DomainProjectEvidence
from ..domain.utils.domain_errors import DomainError
from ..domain.utils.domain_helpers import *
from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from .evidence import EvidenceContract
from .versions import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Project Evidence")
printer = PrettyPrinter()

_CONTRACT = ContractName.PROJECT_EVIDENCE.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]

_SECTION_NAMES: tuple[str, ...] = (
    "requirements",
    "schedules",
    "registers",
    "model_qa_evidence",
    "ifc_evidence",
    "images",
)


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer evidence content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "project_evidence_method_start", "action": action})


def _normalize_text(value: Any, *, field: str) -> str:
    _announce(f"Normalizing Project Evidence text field: {field}")
    try:
        return require_text(value, field=field)
    except DomainError as exc:
        raise ContractValidationError(
            "Project Evidence contains invalid required text.",
            contract=_CONTRACT,
            field=field,
            cause=exc,
        ) from exc


def _normalize_manifest(value: Mapping[str, Any] | None) -> dict[str, Any]:
    _announce("Normalizing Project Evidence source manifest")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractValidationError(
            "source_manifest must be a mapping.",
            contract=_CONTRACT,
            field="source_manifest",
            context={"received_type": type(value).__name__},
        )
    primitive = to_json_primitive(
        value,
        contract=_CONTRACT,
        field="source_manifest",
    )
    if not isinstance(primitive, dict):
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
    _announce(f"Normalizing Project Evidence section: {field}")

    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise ContractValidationError(
            "Project evidence section must be a sequence of evidence objects.",
            contract=_CONTRACT,
            field=field,
            context={"received_type": type(values).__name__},
        )
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ContractValidationError(
            "Project evidence section must be iterable.",
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
                "Project evidence section contains an unsupported value.",
                contract=_CONTRACT,
                field=f"{field}[{index}]",
                context={"received_type": type(item).__name__},
            )

        if normalized.evidence_id in local_ids:
            raise ContractIntegrityError(
                "Project evidence section contains duplicate evidence identifiers.",
                contract=_CONTRACT,
                field=field,
                context={"evidence_id": normalized.evidence_id},
            )
        local_ids.add(normalized.evidence_id)
        result.append(normalized)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ProjectEvidence:
    """Stable project-scoped evidence package for BIM QA and Combined Audit."""

    project_id: str

    requirements: tuple[EvidenceContract, ...] = ()
    schedules: tuple[EvidenceContract, ...] = ()
    registers: tuple[EvidenceContract, ...] = ()
    model_qa_evidence: tuple[EvidenceContract, ...] = ()
    ifc_evidence: tuple[EvidenceContract, ...] = ()
    images: tuple[EvidenceContract, ...] = ()

    family_evidence_refs: tuple[str, ...] = ()
    source_manifest: Mapping[str, Any] = dataclass_field(default_factory=dict)
    schema_version: str = PROJECT_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating Project Evidence contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        project_id = _normalize_text(self.project_id, field="project_id")

        try:
            family_refs = stable_unique_text(
                self.family_evidence_refs,
                field="family_evidence_refs",
            )
        except DomainError as exc:
            raise ContractValidationError(
                "family_evidence_refs contains invalid identifiers.",
                contract=_CONTRACT,
                field="family_evidence_refs",
                cause=exc,
            ) from exc

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
                        "Evidence identifier occurs in more than one Project Evidence section.",
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

        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "family_evidence_refs", family_refs)
        object.__setattr__(self, "source_manifest", manifest)
        object.__setattr__(self, "schema_version", str(self.schema_version).strip())
        for section_name, section in normalized_sections.items():
            object.__setattr__(self, section_name, section)

        logger.debug(
            {
                "event": "project_evidence_validated",
                "project_id": self.project_id,
                "schema_version": self.schema_version,
                "evidence_count": len(all_ids),
                "family_evidence_ref_count": len(family_refs),
                "populated_sections": tuple(
                    name for name, values in normalized_sections.items() if values
                ),
            }
        )

    def section(self, name: str) -> tuple[EvidenceContract, ...]:
        """Return one canonical Project Evidence section."""
        _announce("Resolving Project Evidence section")

        if not isinstance(name, str):
            raise ContractValidationError(
                "Project Evidence section name must be a string.",
                contract=_CONTRACT,
                field="section",
                context={"received_type": type(name).__name__},
            )
        normalized = name.strip()
        if normalized not in _SECTION_NAMES:
            raise ContractValidationError(
                "Unknown Project Evidence section.",
                contract=_CONTRACT,
                field="section",
                context={"received": normalized, "allowed": _SECTION_NAMES},
            )
        return getattr(self, normalized)

    def all_evidence(self) -> tuple[EvidenceContract, ...]:
        """Return all project evidence in deterministic section order."""
        _announce("Collecting all Project Evidence items")
        return tuple(
            evidence
            for section_name in _SECTION_NAMES
            for evidence in getattr(self, section_name)
        )

    def evidence_ids(self) -> tuple[str, ...]:
        """Return all stable project evidence identifiers."""
        _announce("Collecting Project Evidence identifiers")
        return tuple(item.evidence_id for item in self.all_evidence())

    def get_evidence(self, evidence_id: str) -> EvidenceContract | None:
        """Resolve a project evidence item by stable identifier."""
        _announce("Resolving Project Evidence item")
        target = _normalize_text(evidence_id, field="evidence_id")
        for item in self.all_evidence():
            if item.evidence_id == target:
                return item
        return None

    def to_domain(self) -> DomainProjectEvidence:
        """Convert the external aggregate to canonical normalized domain evidence."""
        _announce("Converting Project Evidence contract to domain")
        try:
            return DomainProjectEvidence(
                project_id=self.project_id,
                evidence_items=tuple(item.to_domain() for item in self.all_evidence()),
            )
        except DomainError as exc:
            raise ContractIntegrityError(
                "Project Evidence cannot be converted to the canonical domain aggregate.",
                contract=_CONTRACT,
                field="evidence",
                cause=exc,
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-ready Project Evidence representation."""
        _announce("Serializing Project Evidence contract")

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "family_evidence_refs": list(self.family_evidence_refs),
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
        _announce("Encoding Project Evidence JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProjectEvidence":
        """Parse a strict versioned Project Evidence mapping."""
        _announce("Deserializing Project Evidence contract")

        data = validate_contract_fields(
            payload,
            required=("schema_version", "project_id"),
            optional=(
                "family_evidence_refs",
                "source_manifest",
                *_SECTION_NAMES,
            ),
            contract=_CONTRACT,
        )

        kwargs: dict[str, Any] = {
            "schema_version": data["schema_version"],
            "project_id": data["project_id"],
            "family_evidence_refs": data.get("family_evidence_refs") or (),
            "source_manifest": data.get("source_manifest") or {},
        }
        for section_name in _SECTION_NAMES:
            raw = data.get(section_name, ())
            if raw is None:
                raw = ()
            kwargs[section_name] = raw
        return cls(**kwargs)

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "ProjectEvidence":
        """Decode canonical JSON and validate it as Project Evidence."""
        _announce("Decoding Project Evidence JSON")
        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Project Evidence JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


__all__ = ["ProjectEvidence"]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Project Evidence Contract Self-Test ===\n")
    printer.status("TEST", "Project Evidence contract module initialized", "info")

    digest = hashlib.sha256(b"project-evidence").hexdigest()
    requirement = EvidenceContract(
        evidence_id="EV-PROJ-0001",
        source_file_id="SRC-PROJ-0001",
        source_hash=digest,
        source_type="pdf",
        logical_location={"page": 4},
        extracted_at="2026-09-01T00:00:00Z",
        extracted_value="Required information field",
        confidence=1.0,
    )

    contract = ProjectEvidence(
        project_id="PROJECT-0001",
        requirements=(requirement,),
        source_manifest={"source_file_id": "SRC-PROJ-0001"},
    )
    assert contract.evidence_ids() == ("EV-PROJ-0001",)
    assert contract.to_domain().project_id == "PROJECT-0001"
    assert ProjectEvidence.from_json(contract.to_json()) == contract
    printer.status("PASS", "Project Evidence round trip", "success")

    print("\n=== Test ran successfully ===\n")