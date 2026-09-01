"""
Authoritative JSON Schema export and validation for BIMAP external contracts.

This module is the sole owner of generated JSON Schema documents for the
versioned interchange contracts in ``bimap.contracts``. It does not redefine
BIMAP domain semantics, contract-version policy, DTO serialization, or runtime
business validation. Instead, it translates the already-declared external
contract vocabulary into deterministic JSON Schema Draft 2020-12 documents.

Architectural boundary
----------------------
contracts.utils.contracts_errors
contracts.utils.contracts_helpers
contracts.versions
contract DTO modules / stable domain enums
        ↑
contracts.schema_export
        ↑
CI / developer tooling / external integrations / generated schema artifacts

No contract DTO module may import ``schema_export.py``. The dependency is
strictly one-directional: schema export consumes contracts; contracts do not
consume schema export. This keeps schema generation at the top of the contracts
subsystem and prevents circular imports.

Design principles
-----------------
1. Version identity comes only from ``contracts.versions``.
2. Shared enum vocabularies are imported from their authoritative definitions.
3. Reusable schema fragments are generated once and referenced through local
   ``$defs`` rather than copied by hand throughout schema documents.
4. JSON Schema expresses structural constraints that can be represented
   faithfully. Cross-object/domain invariants that JSON Schema cannot express
   reliably remain enforced by the corresponding contract/domain class.
5. Generated files are deterministic UTF-8 JSON and are written atomically.
6. Errors never include raw customer evidence or complete payload values.
7. Importing this module performs no file-system writes and configures no
   process-wide logging side effects.

Notes
-----
The schema documents describe BIMAP's external JSON boundary. Python-only input
conveniences (for example accepting a ``datetime`` instance before serialization)
are intentionally not represented; JSON timestamps are strings using the
``date-time`` format.
"""

from __future__ import annotations

import copy
import hashlib
import os
import tempfile

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from ..domain.findings.severity import SeverityLevel
from ..domain.orders.states import OrderState
from ..domain.products.models import ProductCode
from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from . import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Schema Export")
printer = PrettyPrinter()


JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
DEFAULT_SCHEMA_DIRECTORY = Path(__file__).resolve().parent / "schema" / "generated"

_SCHEMA_VERSION_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
_CONTRACT_KEY_PATTERN = r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$"
_HEX_DIGEST_PATTERN = r"^[0-9a-fA-F]+$"
_SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"


# Public contract classes are recorded here for diagnostics/documentation and
# registry-integrity checks. The schema definitions remain independent data
# documents rather than introspection-driven guesses from Python annotations.
CONTRACT_TYPES: Mapping[ContractName, type[Any]] = MappingProxyType(
    {
        ContractName.EVIDENCE: EvidenceContract,
        ContractName.FAMILY_EVIDENCE: FamilyEvidence,
        ContractName.PROJECT_EVIDENCE: ProjectEvidence,
        ContractName.FINDING: FindingContract,
        ContractName.REQUIREMENT: RequirementContract,
        ContractName.ORDER: OrderContract,
        ContractName.AUDIT_JOB: AuditJob,
        ContractName.REPORT_MANIFEST: ReportManifest,
    }
)


def _announce(action: str) -> None:
    """Emit a content-free method-start diagnostic."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "schema_export_method_start", "action": action})


def _string(*, nullable: bool = False, pattern: str | None = None) -> dict[str, Any]:
    """Return a non-empty external string schema, optionally nullable."""
    schema: dict[str, Any] = {"type": "string", "minLength": 1}
    if pattern is not None:
        schema["pattern"] = pattern
    if not nullable:
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _datetime_string(*, nullable: bool = False) -> dict[str, Any]:
    """Return an RFC 3339/JSON-Schema date-time string schema."""
    schema: dict[str, Any] = {
        "type": "string",
        "format": "date-time",
        "minLength": 1,
    }
    if not nullable:
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _enum(values: Sequence[str], *, nullable: bool = False) -> dict[str, Any]:
    """Return an enum schema with deterministic value ordering."""
    normalized = list(dict.fromkeys(str(value) for value in values))
    schema: dict[str, Any] = {"type": "string", "enum": normalized}
    if not nullable:
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _string_array(*, nullable: bool = False) -> dict[str, Any]:
    """Return the canonical unique non-empty-string array schema."""
    schema: dict[str, Any] = {
        "type": "array",
        "items": _string(),
        "uniqueItems": True,
    }
    if not nullable:
        return schema
    return {"anyOf": [schema, {"type": "null"}]}


def _object_or_null() -> dict[str, Any]:
    """Return a free-form JSON object or null schema."""
    return {
        "anyOf": [
            {"type": "object"},
            {"type": "null"},
        ]
    }


def _schema_version(version: str) -> dict[str, Any]:
    """Return a schema property locked to one canonical contract version."""
    normalized = normalize_schema_version(version)
    return {
        "type": "string",
        "const": normalized,
        "pattern": _SCHEMA_VERSION_PATTERN,
    }


def _closed_object(
    *,
    properties: Mapping[str, Any],
    required: Sequence[str] = (),
    defs: Mapping[str, Any] | None = None,
    all_of: Sequence[Mapping[str, Any]] = (),
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Build a closed object schema without mutating caller-owned mappings."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": copy.deepcopy(dict(properties)),
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(dict.fromkeys(required))
    if defs:
        schema["$defs"] = copy.deepcopy(dict(defs))
    if all_of:
        schema["allOf"] = copy.deepcopy(list(all_of))
    if title:
        schema["title"] = title
    if description:
        schema["description"] = description
    return schema


def _document(*, title: str, description: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Attach JSON Schema dialect metadata to a top-level contract schema."""
    result = copy.deepcopy(dict(body))
    result["$schema"] = JSON_SCHEMA_DRAFT
    result["title"] = title
    result["description"] = description
    return result


def _logical_location_schema() -> dict[str, Any]:
    """Schema for ``EvidenceLocationContract`` / domain ``LogicalLocation``."""
    _announce("Building logical-location schema definition")
    return _closed_object(
        title="BIMAP Evidence Logical Location",
        description=(
            "Logical location of evidence inside its source. At least one page, "
            "row, element, or path locator must be present."
        ),
        properties={
            "page": {"type": "integer", "minimum": 0},
            "row": {"type": "integer", "minimum": 0},
            "element": _string(),
            "path": _string(),
        },
        all_of=(
            {
                "anyOf": [
                    {"required": ["page"]},
                    {"required": ["row"]},
                    {"required": ["element"]},
                    {"required": ["path"]},
                ]
            },
        ),
    )


def _evidence_object_schema(version: str) -> dict[str, Any]:
    """Build the reusable evidence object definition."""
    _announce("Building evidence object schema definition")
    return _closed_object(
        title="BIMAP Evidence",
        description=(
            "Stable evidence representation preserving source identity, source "
            "integrity, logical location, extraction metadata, extracted value, "
            "and optional extraction confidence."
        ),
        properties={
            "schema_version": _schema_version(version),
            "evidence_id": _string(),
            "source_file_id": _string(),
            "source_hash": _string(pattern=_HEX_DIGEST_PATTERN),
            "hash_algorithm": _string(),
            "source_type": _string(),
            "logical_location": {
                "anyOf": [
                    {"$ref": "#/$defs/logical_location"},
                    {"type": "null"},
                ]
            },
            "extractor_name": _string(nullable=True),
            "extractor_version": _string(nullable=True),
            "extracted_at": _datetime_string(),
            "source_timestamp": _datetime_string(nullable=True),
            "original_filename": _string(nullable=True),
            "source_version": _string(nullable=True),
            "traceability_refs": _string_array(nullable=True),
            # Empty schema means any valid JSON value. Contract helpers/runtime
            # validation remain authoritative for deterministic JSON conversion.
            "extracted_value": {},
            "confidence": {
                "anyOf": [
                    {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    {"type": "null"},
                ]
            },
        },
        required=(
            "schema_version",
            "evidence_id",
            "source_file_id",
            "source_hash",
            "source_type",
            "extracted_at",
            "extracted_value",
        ),
    )


def _evidence_schema(version: str) -> dict[str, Any]:
    _announce("Building Evidence contract schema")
    body = _evidence_object_schema(version)
    body["$defs"] = {"logical_location": _logical_location_schema()}
    return _document(
        title="BIMAP Evidence Contract",
        description="Versioned external/base evidence contract used by BIMAP.",
        body=body,
    )


def _family_evidence_schema(version: str) -> dict[str, Any]:
    _announce("Building Family Evidence contract schema")
    evidence_version = CURRENT_SCHEMA_VERSIONS[ContractName.EVIDENCE.value]
    evidence_definition = _evidence_object_schema(evidence_version)

    section_names = (
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
    section_schema = {
        "anyOf": [
            {"type": "array", "items": {"$ref": "#/$defs/evidence"}},
            {"type": "null"},
        ]
    }
    properties: dict[str, Any] = {
        "schema_version": _schema_version(version),
        "source_manifest": _object_or_null(),
    }
    for name in section_names:
        properties[name] = copy.deepcopy(section_schema)

    body = _closed_object(
        properties=properties,
        required=("schema_version",),
        defs={
            "logical_location": _logical_location_schema(),
            "evidence": evidence_definition,
        },
    )
    return _document(
        title="BIMAP Family Evidence Contract",
        description=(
            "Versioned Family Evidence aggregate used by the Family Audit and "
            "Combined Audit evidence pipeline."
        ),
        body=body,
    )


def _project_evidence_schema(version: str) -> dict[str, Any]:
    _announce("Building Project Evidence contract schema")
    evidence_version = CURRENT_SCHEMA_VERSIONS[ContractName.EVIDENCE.value]
    evidence_definition = _evidence_object_schema(evidence_version)

    section_names = (
        "requirements",
        "schedules",
        "registers",
        "model_qa_evidence",
        "ifc_evidence",
        "images",
    )
    section_schema = {
        "anyOf": [
            {"type": "array", "items": {"$ref": "#/$defs/evidence"}},
            {"type": "null"},
        ]
    }
    properties: dict[str, Any] = {
        "schema_version": _schema_version(version),
        "project_id": _string(),
        "family_evidence_refs": _string_array(nullable=True),
        "source_manifest": _object_or_null(),
    }
    for name in section_names:
        properties[name] = copy.deepcopy(section_schema)

    body = _closed_object(
        properties=properties,
        required=("schema_version", "project_id"),
        defs={
            "logical_location": _logical_location_schema(),
            "evidence": evidence_definition,
        },
    )
    return _document(
        title="BIMAP Project Evidence Contract",
        description=(
            "Versioned project-scoped evidence package used by BIM QA and the "
            "Combined Audit."
        ),
        body=body,
    )


def _finding_schema(version: str) -> dict[str, Any]:
    _announce("Building Finding contract schema")

    severity_values = tuple(level.label for level in SeverityLevel)
    automation_values = tuple(item.value for item in AutomationType)
    status_values = tuple(item.value for item in AssessmentStatus)
    scope_values = tuple(item.value for item in FindingScope)

    body = _closed_object(
        properties={
            "schema_version": _schema_version(version),
            "finding_id": _string(),
            "scope": _enum(scope_values),
            "rule_id": _string(),
            "title": _string(),
            "category": _string(),
            "automation_type": _enum(automation_values),
            "severity": _enum(severity_values),
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "status": _enum(status_values),
            "observed_value": {},
            "expected_value": {},
            "evidence_refs": _string_array(nullable=True),
            "explanation": _string(),
            "remediation": _string(),
            "verification_method": _string(),
        },
        required=(
            "schema_version",
            "finding_id",
            "scope",
            "rule_id",
            "title",
            "category",
            "automation_type",
            "severity",
            "confidence",
            "status",
            "observed_value",
            "expected_value",
            "evidence_refs",
            "explanation",
            "remediation",
            "verification_method",
        ),
        all_of=(
            {
                "if": {
                    "properties": {
                        "automation_type": {
                            "const": AutomationType.DETERMINISTIC.value
                        }
                    },
                    "required": ["automation_type"],
                },
                "then": {
                    "properties": {
                        "evidence_refs": {
                            "type": "array",
                            "items": _string(),
                            "uniqueItems": True,
                            "minItems": 1,
                        }
                    }
                },
            },
        ),
    )
    return _document(
        title="BIMAP Finding Contract",
        description=(
            "Externally serializable finding with separate severity and "
            "confidence, evidence references, remediation, and verification."
        ),
        body=body,
    )


def _requirement_schema(version: str) -> dict[str, Any]:
    _announce("Building Requirement contract schema")

    assessed_statuses = (
        AssessmentStatus.PASS.value,
        AssessmentStatus.WARN.value,
        AssessmentStatus.FAIL.value,
    )
    body = _closed_object(
        properties={
            "schema_version": _schema_version(version),
            "requirement_id": _string(),
            "source_requirement": _string(),
            "evidence_refs": _string_array(nullable=True),
            "assessment": _enum(tuple(item.value for item in AssessmentStatus)),
            "automation_type": _enum(tuple(item.value for item in AutomationType)),
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "impact": _string(),
            "recommended_action": _string(),
        },
        required=(
            "schema_version",
            "requirement_id",
            "source_requirement",
            "evidence_refs",
            "assessment",
            "automation_type",
            "confidence",
            "impact",
            "recommended_action",
        ),
        all_of=(
            {
                "if": {
                    "properties": {"assessment": {"enum": list(assessed_statuses)}},
                    "required": ["assessment"],
                },
                "then": {
                    "properties": {
                        "evidence_refs": {
                            "type": "array",
                            "items": _string(),
                            "uniqueItems": True,
                            "minItems": 1,
                        }
                    }
                },
            },
        ),
    )
    return _document(
        title="BIMAP Requirement Contract",
        description=(
            "Versioned Requirement-Evidence Matrix row with assessment, "
            "automation classification, confidence, evidence, impact, and action."
        ),
        body=body,
    )


def _order_event_schema() -> dict[str, Any]:
    _announce("Building Order Event schema definition")
    state_values = tuple(item.value for item in OrderState)
    return _closed_object(
        title="BIMAP Order Event",
        description="Nested append-only order lifecycle event representation.",
        properties={
            "event_id": _string(),
            "order_id": _string(),
            "idempotency_key": _string(),
            "occurred_at": _datetime_string(),
            "from_state": _enum(state_values, nullable=True),
            "to_state": _enum(state_values),
            "reason": _string(nullable=True),
            "actor": _string(nullable=True),
            "metadata": _object_or_null(),
        },
        required=(
            "event_id",
            "order_id",
            "idempotency_key",
            "occurred_at",
            "from_state",
            "to_state",
            "reason",
            "actor",
            "metadata",
        ),
    )


def _order_schema(version: str) -> dict[str, Any]:
    _announce("Building Order contract schema")
    body = _closed_object(
        properties={
            "schema_version": _schema_version(version),
            "order_id": _string(),
            "product_code": _enum(tuple(item.value for item in ProductCode)),
            "tier_code": _string(nullable=True),
            "project_alias": _string(nullable=True),
            "state": _enum(tuple(item.value for item in OrderState)),
            "created_at": _datetime_string(),
            "updated_at": _datetime_string(),
            "upload_session_id": _string(nullable=True),
            "retention_expires_at": _datetime_string(nullable=True),
            "version": {"type": "integer", "minimum": 0},
            "metadata": _object_or_null(),
            "events": {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {"$ref": "#/$defs/order_event"},
                    },
                    {"type": "null"},
                ]
            },
        },
        required=(
            "schema_version",
            "order_id",
            "product_code",
            "state",
            "created_at",
            "updated_at",
            "version",
        ),
        defs={"order_event": _order_event_schema()},
    )
    body["$comment"] = (
        "Cross-field order/event invariants and legal lifecycle transitions are "
        "enforced by the BIMAP order domain and are not duplicated in JSON Schema."
    )
    return _document(
        title="BIMAP Order Contract",
        description="Versioned external representation of the BIMAP order aggregate.",
        body=body,
    )


def _audit_job_schema(version: str) -> dict[str, Any]:
    _announce("Building Audit Job contract schema")
    body = _closed_object(
        properties={
            "schema_version": _schema_version(version),
            "job_id": _string(),
            "order_id": _string(),
            "order_version": {"type": "integer", "minimum": 0},
            "product_code": _enum(tuple(item.value for item in ProductCode)),
            "submitted_at": _datetime_string(),
            "evidence_refs": _string_array(nullable=True),
            "evidence_manifest_ref": _string(nullable=True),
            "metadata": _object_or_null(),
        },
        required=(
            "schema_version",
            "job_id",
            "order_id",
            "order_version",
            "product_code",
            "submitted_at",
        ),
        all_of=(
            {
                "anyOf": [
                    {
                        "required": ["evidence_refs"],
                        "properties": {
                            "evidence_refs": {
                                "type": "array",
                                "items": _string(),
                                "uniqueItems": True,
                                "minItems": 1,
                            }
                        },
                    },
                    {
                        "required": ["evidence_manifest_ref"],
                        "properties": {
                            "evidence_manifest_ref": _string()
                        },
                    },
                ]
            },
        ),
    )
    return _document(
        title="BIMAP Audit Job Contract",
        description=(
            "Reference-oriented work envelope submitted to the audit worker and "
            "SLAI integration boundary."
        ),
        body=body,
    )


def _report_artifact_schema() -> dict[str, Any]:
    _announce("Building Report Artifact schema definition")
    return _closed_object(
        title="BIMAP Report Artifact",
        description="Integrity metadata for one generated report artifact.",
        properties={
            "artifact_id": _string(),
            "filename": _string(pattern=r"^[^/\\]+$"),
            "sha256": _string(pattern=_SHA256_PATTERN),
            "size_bytes": {"type": "integer", "minimum": 0},
        },
        required=("artifact_id", "filename", "sha256", "size_bytes"),
    )


def _version_mapping_schema(*, contract_keys_only: bool) -> dict[str, Any]:
    """Build report-manifest name->version metadata schema."""
    if contract_keys_only:
        property_names: dict[str, Any] = {
            "enum": [item.value for item in ContractName]
        }
        value_schema: dict[str, Any] = {
            "type": "string",
            "pattern": _SCHEMA_VERSION_PATTERN,
        }
    else:
        property_names = {"pattern": _CONTRACT_KEY_PATTERN}
        value_schema = _string()

    return {
        "anyOf": [
            {
                "type": "object",
                "propertyNames": property_names,
                "additionalProperties": value_schema,
            },
            {"type": "null"},
        ]
    }


def _report_manifest_schema(version: str) -> dict[str, Any]:
    _announce("Building Report Manifest contract schema")
    body = _closed_object(
        properties={
            "schema_version": _schema_version(version),
            "report_id": _string(),
            "order_id": _string(),
            "report_version": _string(),
            "generated_at": _datetime_string(),
            "expires_at": _datetime_string(nullable=True),
            "artifacts": {
                "type": "array",
                "items": {"$ref": "#/$defs/report_artifact"},
                "minItems": 1,
            },
            "finding_refs": _string_array(nullable=True),
            "requirement_refs": _string_array(nullable=True),
            "evidence_refs": _string_array(nullable=True),
            "contract_versions": _version_mapping_schema(contract_keys_only=True),
            "software_versions": _version_mapping_schema(contract_keys_only=False),
            "ruleset_versions": _version_mapping_schema(contract_keys_only=False),
        },
        required=(
            "schema_version",
            "report_id",
            "order_id",
            "report_version",
            "generated_at",
            "artifacts",
        ),
        defs={"report_artifact": _report_artifact_schema()},
    )
    body["$comment"] = (
        "Uniqueness of artifact_id and filename, ordering of timestamps, and "
        "cross-artifact/report invariants remain enforced by ReportManifest."
    )
    return _document(
        title="BIMAP Report Manifest Contract",
        description=(
            "Immutable versioned manifest identifying generated report artifacts, "
            "content hashes, references, and reproducibility metadata."
        ),
        body=body,
    )


SchemaBuilder = Callable[[str], dict[str, Any]]

_SCHEMA_BUILDERS: Mapping[ContractName, SchemaBuilder] = MappingProxyType(
    {
        ContractName.EVIDENCE: _evidence_schema,
        ContractName.FAMILY_EVIDENCE: _family_evidence_schema,
        ContractName.PROJECT_EVIDENCE: _project_evidence_schema,
        ContractName.FINDING: _finding_schema,
        ContractName.REQUIREMENT: _requirement_schema,
        ContractName.ORDER: _order_schema,
        ContractName.AUDIT_JOB: _audit_job_schema,
        ContractName.REPORT_MANIFEST: _report_manifest_schema,
    }
)


def _json_path(parts: Sequence[Any]) -> str:
    """Render a validation path without exposing the offending value."""
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            text = str(part)
            if text.isidentifier():
                rendered += f".{text}"
            else:
                escaped = text.replace("\\", "\\\\").replace("'", "\\'")
                rendered += f"['{escaped}']"
    return rendered


class SchemaExporter:
    """
    Generate, validate, fingerprint, and atomically export BIMAP JSON Schemas.

    ``SchemaExporter`` is deliberately read-only with respect to contract
    registration. Contract identity/version policy is source-controlled in
    ``contracts.versions``; this service cannot mutate that registry at runtime.
    """

    def __init__(self, *, output_dir: str | os.PathLike[str] | None = None) -> None:
        _announce("Initializing contract schema exporter")
        self._output_dir = (
            Path(output_dir).expanduser()
            if output_dir is not None
            else DEFAULT_SCHEMA_DIRECTORY
        )
        self._format_checker = FormatChecker()
        self._validate_registry_integrity()
        logger.info(
            {
                "event": "schema_exporter_initialized",
                "contracts": len(_SCHEMA_BUILDERS),
                "output_dir": str(self._output_dir),
            }
        )

    @property
    def output_dir(self) -> Path:
        """Return the configured schema output directory without creating it."""
        _announce("Resolving schema output directory")
        return self._output_dir

    def _validate_registry_integrity(self) -> None:
        """Fail closed if version/type/schema registries drift apart."""
        _announce("Validating schema-export registry integrity")

        names = set(ContractName)
        builder_names = set(_SCHEMA_BUILDERS)
        type_names = set(CONTRACT_TYPES)
        current_names = set(CURRENT_SCHEMA_VERSIONS)
        supported_names = set(SUPPORTED_SCHEMA_VERSIONS)
        expected_text_names = {item.value for item in names}

        if builder_names != names or type_names != names:
            raise ContractSchemaDefinitionError(
                "Schema/type registry does not cover the canonical ContractName set.",
                context={
                    "missing_builders": tuple(
                        sorted(item.value for item in names - builder_names)
                    ),
                    "unexpected_builders": tuple(
                        sorted(item.value for item in builder_names - names)
                    ),
                    "missing_types": tuple(
                        sorted(item.value for item in names - type_names)
                    ),
                    "unexpected_types": tuple(
                        sorted(item.value for item in type_names - names)
                    ),
                },
            )

        if current_names != expected_text_names or supported_names != expected_text_names:
            raise ContractSchemaDefinitionError(
                "Schema exporter and version registry expose different contract keys.",
                context={
                    "version_registry_keys": tuple(sorted(current_names)),
                    "supported_registry_keys": tuple(sorted(supported_names)),
                    "expected": tuple(sorted(expected_text_names)),
                },
            )

        for name in ContractName:
            current = CURRENT_SCHEMA_VERSIONS[name.value]
            ensure_supported_schema_version(
                current,
                supported=SUPPORTED_SCHEMA_VERSIONS[name.value],
                contract=name.value,
            )

    def _resolve_contract(self, contract: str | ContractName) -> ContractName:
        _announce("Resolving schema contract identity")
        return ContractName.parse(contract)

    def _resolve_version(
        self,
        contract: ContractName,
        version: str | None,
    ) -> str:
        _announce(f"Resolving {contract.value} schema version")
        requested = (
            CURRENT_SCHEMA_VERSIONS[contract.value]
            if version is None
            else normalize_schema_version(version, contract=contract.value)
        )
        return ensure_supported_schema_version(
            requested,
            supported=SUPPORTED_SCHEMA_VERSIONS[contract.value],
            contract=contract.value,
        )

    def contract_type(self, contract: str | ContractName) -> type[Any]:
        """Return the Python DTO class corresponding to an external contract."""
        _announce("Resolving contract DTO type")
        name = self._resolve_contract(contract)
        return CONTRACT_TYPES[name]

    def schema(
        self,
        contract: str | ContractName,
        *,
        version: str | None = None,
        validate: bool = True,
    ) -> dict[str, Any]:
        """Build and return one standalone JSON Schema document."""
        _announce("Generating contract JSON Schema")
        name = self._resolve_contract(contract)
        resolved_version = self._resolve_version(name, version)

        builder = _SCHEMA_BUILDERS.get(name)
        if builder is None:  # defensive; registry check normally makes this impossible
            raise ContractSchemaDefinitionError(
                "No JSON Schema builder is registered for the contract.",
                contract=name.value,
                version=resolved_version,
            )

        try:
            document = builder(resolved_version)
        except ContractError:
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected failure while building BIMAP schema for %s",
                name.value,
            )
            raise ContractSchemaDefinitionError(
                "Contract JSON Schema generation failed.",
                contract=name.value,
                version=resolved_version,
                cause=exc,
            ) from exc

        if validate:
            self.validate_schema(document, contract=name, version=resolved_version)

        return copy.deepcopy(document)

    def all_schemas(self, *, validate: bool = True) -> dict[str, dict[str, Any]]:
        """Build all current BIMAP contract schemas in canonical name order."""
        _announce("Generating all current contract JSON Schemas")
        return {
            name.value: self.schema(name, validate=validate)
            for name in sorted(ContractName, key=lambda item: item.value)
        }

    def validate_schema(
        self,
        schema: Mapping[str, Any],
        *,
        contract: str | ContractName,
        version: str | None = None,
    ) -> None:
        """Validate a generated schema against JSON Schema Draft 2020-12."""
        _announce("Validating JSON Schema definition")
        name = self._resolve_contract(contract)
        resolved_version = self._resolve_version(name, version)

        if not isinstance(schema, Mapping):
            raise ContractSchemaDefinitionError(
                "Generated schema root must be a mapping.",
                contract=name.value,
                version=resolved_version,
                context={"received_type": type(schema).__name__},
            )

        try:
            Draft202012Validator.check_schema(dict(schema))
        except SchemaError as exc:
            path = _json_path(tuple(exc.absolute_schema_path))
            raise ContractSchemaDefinitionError(
                "Generated JSON Schema is not valid Draft 2020-12 schema.",
                contract=name.value,
                version=resolved_version,
                path=path,
                context={
                    "validator": exc.validator,
                    "schema_path": path,
                },
                cause=exc,
            ) from exc

    def validate_payload(
        self,
        contract: str | ContractName,
        payload: Any,
        *,
        version: str | None = None,
    ) -> None:
        """
        Validate external JSON-compatible data against a BIMAP contract schema.

        This performs structural JSON Schema validation. DTO/domain constructors
        remain authoritative for invariants that require Python/domain logic,
        such as cross-record identifier consistency or source-hash algorithm
        relationships.
        """
        _announce("Validating payload against contract JSON Schema")
        name = self._resolve_contract(contract)
        resolved_version = self._resolve_version(name, version)
        schema = self.schema(name, version=resolved_version, validate=True)

        validator = Draft202012Validator(
            schema,
            format_checker=self._format_checker,
        )
        errors = sorted(
            validator.iter_errors(payload),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                tuple(str(part) for part in item.absolute_schema_path),
            ),
        )
        if not errors:
            return

        first: ValidationError = errors[0]
        instance_path = _json_path(tuple(first.absolute_path))
        schema_path = _json_path(tuple(first.absolute_schema_path))

        logger.warning(
            {
                "event": "contract_schema_validation_failed",
                "contract": name.value,
                "version": resolved_version,
                "error_count": len(errors),
                "instance_path": instance_path,
                "validator": first.validator,
            }
        )
        raise ContractSchemaValidationError(
            "External contract payload does not conform to its JSON Schema.",
            contract=name.value,
            version=resolved_version,
            path=instance_path,
            context={
                "validator": first.validator,
                "schema_path": schema_path,
                "error_count": len(errors),
            },
            cause=first,
        ) from first

    def filename(
        self,
        contract: str | ContractName,
        *,
        version: str | None = None,
    ) -> str:
        """Return the deterministic generated filename for one schema."""
        _announce("Generating schema artifact filename")
        name = self._resolve_contract(contract)
        resolved_version = self._resolve_version(name, version)
        return f"{name.value}-v{resolved_version}.schema.json"

    def digest(
        self,
        contract: str | ContractName,
        *,
        version: str | None = None,
    ) -> str:
        """Return the SHA-256 digest of canonical compact schema JSON bytes."""
        _announce("Calculating contract schema digest")
        name = self._resolve_contract(contract)
        resolved_version = self._resolve_version(name, version)
        schema = self.schema(name, version=resolved_version, validate=True)
        data = canonical_json_bytes(schema, contract=name.value)
        return hashlib.sha256(data).hexdigest()

    def inventory(self) -> dict[str, dict[str, str]]:
        """Return deterministic metadata for all current generated schemas."""
        _announce("Building contract schema inventory")
        return {
            name.value: {
                "version": CURRENT_SCHEMA_VERSIONS[name.value],
                "filename": self.filename(name),
                "sha256": self.digest(name),
                "dto": CONTRACT_TYPES[name].__name__,
            }
            for name in sorted(ContractName, key=lambda item: item.value)
        }

    def _resolve_output_directory(
        self,
        output_dir: str | os.PathLike[str] | None,
    ) -> Path:
        _announce("Resolving schema export target directory")
        target = (
            Path(output_dir).expanduser()
            if output_dir is not None
            else self._output_dir
        )

        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ContractSchemaError(
                "Unable to create schema export directory.",
                context={"output_dir": str(target)},
                cause=exc,
            ) from exc

        if not target.is_dir():
            raise ContractSchemaError(
                "Schema export target is not a directory.",
                context={"output_dir": str(target)},
            )
        return target

    @staticmethod
    def _atomic_write_text(
        target: Path,
        content: str,
        *,
        overwrite: bool,
    ) -> None:
        """Write UTF-8 text atomically without exposing partial schema files."""
        _announce("Writing schema artifact atomically")

        if target.exists() and not overwrite:
            raise ContractSchemaError(
                "Schema artifact already exists and overwrite is disabled.",
                context={"target": str(target)},
            )

        fd: int | None = None
        temporary_path: Path | None = None
        try:
            fd, raw_path = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
                text=True,
            )
            temporary_path = Path(raw_path)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                fd = None
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())

            if not overwrite and target.exists():
                raise ContractSchemaError(
                    "Schema artifact appeared during export and overwrite is disabled.",
                    context={"target": str(target)},
                )

            os.replace(temporary_path, target)
            temporary_path = None
        except ContractError:
            raise
        except OSError as exc:
            raise ContractSchemaError(
                "Failed to write schema artifact.",
                context={"target": str(target)},
                cause=exc,
            ) from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        {
                            "event": "schema_temp_cleanup_failed",
                            "temporary_path": str(temporary_path),
                        }
                    )

    def export(
        self,
        contract: str | ContractName,
        *,
        version: str | None = None,
        output_dir: str | os.PathLike[str] | None = None,
        overwrite: bool = False,
    ) -> Path:
        """Generate, validate, and atomically write one JSON Schema artifact."""
        _announce("Exporting contract JSON Schema")
        name = self._resolve_contract(contract)
        resolved_version = self._resolve_version(name, version)
        schema = self.schema(name, version=resolved_version, validate=True)
        directory = self._resolve_output_directory(output_dir)
        target = directory / self.filename(name, version=resolved_version)

        content = canonical_json_dumps(
            schema,
            contract=name.value,
            pretty=True,
        ) + "\n"
        self._atomic_write_text(target, content, overwrite=overwrite)

        logger.info(
            {
                "event": "contract_schema_exported",
                "contract": name.value,
                "version": resolved_version,
                "target": str(target),
                "sha256": hashlib.sha256(
                    canonical_json_bytes(schema, contract=name.value)
                ).hexdigest(),
            }
        )
        return target

    def export_all(
        self,
        *,
        output_dir: str | os.PathLike[str] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Path]:
        """Export all current contract schemas in deterministic name order."""
        _announce("Exporting all current contract JSON Schemas")
        return {
            name.value: self.export(
                name,
                output_dir=output_dir,
                overwrite=overwrite,
            )
            for name in sorted(ContractName, key=lambda item: item.value)
        }


# Backward-compatible alias retained from the initial scaffold. New code should
# prefer the semantically accurate ``SchemaExporter`` name.
SchemaReport = SchemaExporter


__all__ = [
    "JSON_SCHEMA_DRAFT",
    "DEFAULT_SCHEMA_DIRECTORY",
    "CONTRACT_TYPES",
    "SchemaExporter",
    "SchemaReport",
]


if __name__ == "__main__":
    print("\n=== Running BIMAP Contracts Schema Export Self-Test ===\n")
    printer.status("TEST", "Schema exporter initialized", "info")

    exporter = SchemaExporter()

    schemas = exporter.all_schemas(validate=True)
    assert set(schemas) == {item.value for item in ContractName}
    printer.status("PASS", "All registered schemas are valid Draft 2020-12", "success")

    exporter.validate_payload(
        ContractName.FAMILY_EVIDENCE,
        {"schema_version": CURRENT_SCHEMA_VERSIONS["family_evidence"]},
    )
    exporter.validate_payload(
        ContractName.PROJECT_EVIDENCE,
        {
            "schema_version": CURRENT_SCHEMA_VERSIONS["project_evidence"],
            "project_id": "PROJECT-0001",
        },
    )
    exporter.validate_payload(
        ContractName.FINDING,
        {
            "schema_version": CURRENT_SCHEMA_VERSIONS["finding"],
            "finding_id": "RFA-PARAM-00042",
            "scope": "family",
            "rule_id": "R3D.RFA.PARAM.001",
            "title": "Required parameter missing",
            "category": "parameter_governance",
            "automation_type": "deterministic",
            "severity": "high",
            "confidence": 0.97,
            "status": "fail",
            "observed_value": None,
            "expected_value": "Required parameter",
            "evidence_refs": ["EV-0041"],
            "explanation": "Required evidence-backed parameter was not present.",
            "remediation": "Add the approved parameter according to the rule source.",
            "verification_method": "Re-export evidence and rerun the rule.",
        },
    )
    exporter.validate_payload(
        ContractName.REQUIREMENT,
        {
            "schema_version": CURRENT_SCHEMA_VERSIONS["requirement"],
            "requirement_id": "REQ-0001",
            "source_requirement": "Required information must be present.",
            "evidence_refs": ["EV-0001"],
            "assessment": "fail",
            "automation_type": "deterministic",
            "confidence": 1.0,
            "impact": "Required information is absent.",
            "recommended_action": "Provide the missing information.",
        },
    )
    exporter.validate_payload(
        ContractName.ORDER,
        {
            "schema_version": CURRENT_SCHEMA_VERSIONS["order"],
            "order_id": "ORD-0001",
            "product_code": "family_audit",
            "state": "draft",
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": "2026-09-01T00:00:00Z",
            "version": 0,
        },
    )
    exporter.validate_payload(
        ContractName.AUDIT_JOB,
        {
            "schema_version": CURRENT_SCHEMA_VERSIONS["audit_job"],
            "job_id": "JOB-0001",
            "order_id": "ORD-0001",
            "order_version": 1,
            "product_code": "family_audit",
            "submitted_at": "2026-09-01T00:00:00Z",
            "evidence_manifest_ref": "manifest://ORD-0001",
        },
    )
    exporter.validate_payload(
        ContractName.REPORT_MANIFEST,
        {
            "schema_version": CURRENT_SCHEMA_VERSIONS["report_manifest"],
            "report_id": "REPORT-0001",
            "order_id": "ORD-0001",
            "report_version": "1.0",
            "generated_at": "2026-09-01T00:00:00Z",
            "artifacts": [
                {
                    "artifact_id": "ART-0001",
                    "filename": "R3D_Audit_Report.pdf",
                    "sha256": hashlib.sha256(b"report").hexdigest(),
                    "size_bytes": 6,
                }
            ],
        },
    )
    printer.status("PASS", "Representative payload validation", "success")

    try:
        exporter.validate_payload(
            ContractName.FINDING,
            {
                "schema_version": CURRENT_SCHEMA_VERSIONS["finding"],
                "finding_id": "F-INVALID",
                "scope": "family",
                "rule_id": "R3D.TEST.001",
                "title": "Invalid deterministic finding",
                "category": "test",
                "automation_type": "deterministic",
                "severity": "low",
                "confidence": 1.0,
                "status": "fail",
                "observed_value": None,
                "expected_value": None,
                "evidence_refs": [],
                "explanation": "Test",
                "remediation": "Test",
                "verification_method": "Test",
            },
        )
        raise AssertionError("Expected ContractSchemaValidationError")
    except ContractSchemaValidationError:
        printer.status("PASS", "Deterministic evidence invariant rejected", "success")

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = exporter.export_all(output_dir=tmpdir)
        assert len(paths) == len(ContractName)
        assert all(path.is_file() for path in paths.values())
        printer.status("PASS", "Atomic schema export", "success")

    inventory = exporter.inventory()
    assert all(len(item["sha256"]) == 64 for item in inventory.values())
    printer.status("PASS", "Deterministic schema inventory", "success")

    print("\n=== Test ran successfully ===\n")