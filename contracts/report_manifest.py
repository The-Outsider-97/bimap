"""
Versioned immutable manifest for BIMAP report-package artifacts.

The report manifest records which generated artifacts belong to one delivered
BIMAP report package and the version information required to reproduce or audit
that package. It does not duplicate the contents of ``findings.json``,
``requirement_matrix.csv``, or the evidence manifest; those remain separate
versioned deliverables referenced by artifact identity and content hash.

The implementation specification requires versioned reports, report hashes,
software/schema/ruleset version disclosure, and explicit deliverable manifests.
This module encodes those structural requirements while leaving product-specific
release policy (for example whether a requirement matrix is mandatory for a
particular product) to reporting/application services.

Dependency direction
--------------------
contracts.utils
contracts.versions
        ↑
contracts.report_manifest
        ↑
reporting.artifact_manifest / package_builder / app services

The manifest does not import the reporting implementation or SLAI runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Any

from ..domain.utils.domain_errors import DomainError
from ..domain.utils.domain_helpers import *
from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from .versions import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Report Manifest")
printer = PrettyPrinter()

_CONTRACT = ContractName.REPORT_MANIFEST.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without report/customer content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "report_manifest_method_start", "action": action})


def _normalize_text(value: Any, *, field: str) -> str:
    _announce(f"Normalizing Report Manifest text field: {field}")
    try:
        return require_text(value, field=field)
    except DomainError as exc:
        raise ContractValidationError(
            "Report Manifest contains invalid required text.",
            contract=_CONTRACT,
            field=field,
            cause=exc,
        ) from exc


def _normalize_non_negative_int(value: Any, *, field: str) -> int:
    _announce(f"Normalizing Report Manifest integer field: {field}")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(
            "Report artifact size must be an integer.",
            contract=_CONTRACT,
            field=field,
            context={"received_type": type(value).__name__},
        )
    if value < 0:
        raise ContractValidationError(
            "Report artifact size must be non-negative.",
            contract=_CONTRACT,
            field=field,
            context={"received": value},
        )
    return value


def _normalize_version_mapping(
    value: Mapping[str, Any] | None,
    *,
    field: str,
    contract_keys_only: bool,
) -> dict[str, str]:
    """Validate deterministic name->version metadata without guessing versions."""
    _announce(f"Normalizing Report Manifest version mapping: {field}")

    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractValidationError(
            "Version metadata must be a mapping.",
            contract=_CONTRACT,
            field=field,
            context={"received_type": type(value).__name__},
        )

    result: dict[str, str] = {}
    for raw_key, raw_version in value.items():
        if contract_keys_only:
            try:
                key = ContractName.parse(raw_key).value
            except ContractError as exc:
                raise ContractValidationError(
                    "contract_versions contains an unknown contract key.",
                    contract=_CONTRACT,
                    field=field,
                    cause=exc,
                ) from exc
            version = normalize_schema_version(
                raw_version,
                field=f"{field}.{key}",
                contract=key,
            )
        else:
            key = normalize_contract_key(raw_key, field=f"{field}.key")
            if not isinstance(raw_version, str) or not raw_version.strip():
                raise ContractValidationError(
                    "Version metadata values must be non-empty strings.",
                    contract=_CONTRACT,
                    field=f"{field}.{key}",
                    context={"received_type": type(raw_version).__name__},
                )
            version = raw_version.strip()

        if key in result:
            raise ContractIntegrityError(
                "Version mapping contains duplicate normalized keys.",
                contract=_CONTRACT,
                field=field,
                context={"key": key},
            )
        result[key] = version

    return dict(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class ReportArtifactContract:
    """Integrity metadata for one immutable generated report artifact."""

    artifact_id: str
    filename: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        _announce("Validating Report Artifact contract")

        artifact_id = _normalize_text(self.artifact_id, field="artifact_id")
        filename = _normalize_text(self.filename, field="filename")
        if "/" in filename or "\\" in filename:
            raise ContractValidationError(
                "Report artifact filename must be a basename, not a path.",
                contract=_CONTRACT,
                field="filename",
            )
        try:
            digest = normalize_hex_digest(
                self.sha256,
                algorithm="sha256",
                field="sha256",
            )
        except DomainError as exc:
            raise ContractValidationError(
                "Report artifact SHA-256 digest is invalid.",
                contract=_CONTRACT,
                field="sha256",
                cause=exc,
            ) from exc

        size_bytes = _normalize_non_negative_int(
            self.size_bytes,
            field="size_bytes",
        )

        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "size_bytes", size_bytes)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-ready artifact integrity record."""
        _announce("Serializing Report Artifact contract")
        return {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReportArtifactContract":
        """Parse a strict artifact metadata mapping."""
        _announce("Deserializing Report Artifact contract")
        data = validate_contract_fields(
            payload,
            required=("artifact_id", "filename", "sha256", "size_bytes"),
            contract=_CONTRACT,
        )
        return cls(
            artifact_id=data["artifact_id"],
            filename=data["filename"],
            sha256=data["sha256"],
            size_bytes=data["size_bytes"],
        )


@dataclass(frozen=True, slots=True)
class ReportManifest:
    """Immutable versioned manifest for one BIMAP report package."""

    report_id: str
    order_id: str
    report_version: str
    generated_at: str | datetime
    artifacts: tuple[ReportArtifactContract, ...]

    finding_refs: tuple[str, ...] = ()
    requirement_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    contract_versions: Mapping[str, str] = dataclass_field(default_factory=dict)
    software_versions: Mapping[str, str] = dataclass_field(default_factory=dict)
    ruleset_versions: Mapping[str, str] = dataclass_field(default_factory=dict)

    expires_at: str | datetime | None = None
    schema_version: str = REPORT_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating Report Manifest contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        report_id = _normalize_text(self.report_id, field="report_id")
        order_id = _normalize_text(self.order_id, field="order_id")
        report_version = _normalize_text(
            self.report_version,
            field="report_version",
        )

        try:
            generated_at = ensure_utc_datetime(
                self.generated_at,
                field="generated_at",
            )
            expires_at = (
                ensure_utc_datetime(self.expires_at, field="expires_at")
                if self.expires_at is not None
                else None
            )
            finding_refs = stable_unique_text(
                self.finding_refs,
                field="finding_refs",
            )
            requirement_refs = stable_unique_text(
                self.requirement_refs,
                field="requirement_refs",
            )
            evidence_refs = stable_unique_text(
                self.evidence_refs,
                field="evidence_refs",
            )
        except DomainError as exc:
            raise ContractValidationError(
                "Report Manifest contains invalid timestamp or reference data.",
                contract=_CONTRACT,
                cause=exc,
            ) from exc

        if expires_at is not None and expires_at < generated_at:
            raise ContractIntegrityError(
                "Report expiration timestamp cannot precede generation.",
                contract=_CONTRACT,
                field="expires_at",
            )

        if isinstance(self.artifacts, (str, bytes, bytearray, Mapping)):
            raise ContractValidationError(
                "artifacts must be a sequence of artifact objects.",
                contract=_CONTRACT,
                field="artifacts",
                context={"received_type": type(self.artifacts).__name__},
            )
        try:
            raw_artifacts = tuple(self.artifacts)
        except TypeError as exc:
            raise ContractValidationError(
                "artifacts must be iterable.",
                contract=_CONTRACT,
                field="artifacts",
                context={"received_type": type(self.artifacts).__name__},
                cause=exc,
            ) from exc
        if not raw_artifacts:
            raise ContractIntegrityError(
                "Report Manifest must contain at least one generated artifact.",
                contract=_CONTRACT,
                field="artifacts",
            )

        artifacts: list[ReportArtifactContract] = []
        artifact_ids: set[str] = set()
        filenames: set[str] = set()
        for index, item in enumerate(raw_artifacts):
            if isinstance(item, ReportArtifactContract):
                artifact = item
            elif isinstance(item, Mapping):
                artifact = ReportArtifactContract.from_dict(item)
            else:
                raise ContractValidationError(
                    "artifacts contains an unsupported value.",
                    contract=_CONTRACT,
                    field=f"artifacts[{index}]",
                    context={"received_type": type(item).__name__},
                )

            if artifact.artifact_id in artifact_ids:
                raise ContractIntegrityError(
                    "Report Manifest contains duplicate artifact identifiers.",
                    contract=_CONTRACT,
                    field="artifacts",
                    context={"artifact_id": artifact.artifact_id},
                )
            if artifact.filename in filenames:
                raise ContractIntegrityError(
                    "Report Manifest contains duplicate artifact filenames.",
                    contract=_CONTRACT,
                    field="artifacts",
                    context={"filename": artifact.filename},
                )
            artifact_ids.add(artifact.artifact_id)
            filenames.add(artifact.filename)
            artifacts.append(artifact)

        contract_versions = _normalize_version_mapping(
            self.contract_versions,
            field="contract_versions",
            contract_keys_only=True,
        )
        software_versions = _normalize_version_mapping(
            self.software_versions,
            field="software_versions",
            contract_keys_only=False,
        )
        ruleset_versions = _normalize_version_mapping(
            self.ruleset_versions,
            field="ruleset_versions",
            contract_keys_only=False,
        )

        object.__setattr__(self, "report_id", report_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "report_version", report_version)
        object.__setattr__(self, "generated_at", format_utc_datetime(generated_at))
        object.__setattr__(
            self,
            "expires_at",
            format_utc_datetime(expires_at) if expires_at is not None else None,
        )
        object.__setattr__(self, "artifacts", tuple(artifacts))
        object.__setattr__(self, "finding_refs", finding_refs)
        object.__setattr__(self, "requirement_refs", requirement_refs)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "contract_versions", contract_versions)
        object.__setattr__(self, "software_versions", software_versions)
        object.__setattr__(self, "ruleset_versions", ruleset_versions)
        object.__setattr__(self, "schema_version", str(self.schema_version).strip())

        logger.debug(
            {
                "event": "report_manifest_validated",
                "report_id": self.report_id,
                "order_id": self.order_id,
                "artifact_count": len(artifacts),
                "finding_ref_count": len(finding_refs),
                "requirement_ref_count": len(requirement_refs),
                "evidence_ref_count": len(evidence_refs),
            }
        )

    def artifact(self, artifact_id: str) -> ReportArtifactContract | None:
        """Resolve one artifact by its stable identifier."""
        _announce("Resolving Report Manifest artifact")
        target = _normalize_text(artifact_id, field="artifact_id")
        for item in self.artifacts:
            if item.artifact_id == target:
                return item
        return None

    def artifact_by_filename(self, filename: str) -> ReportArtifactContract | None:
        """Resolve one artifact by exact generated filename."""
        _announce("Resolving Report Manifest artifact by filename")
        target = _normalize_text(filename, field="filename")
        for item in self.artifacts:
            if item.filename == target:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-ready report-manifest representation."""
        _announce("Serializing Report Manifest contract")
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "order_id": self.order_id,
            "report_version": self.report_version,
            "generated_at": self.generated_at,
            "expires_at": self.expires_at,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "finding_refs": list(self.finding_refs),
            "requirement_refs": list(self.requirement_refs),
            "evidence_refs": list(self.evidence_refs),
            "contract_versions": dict(self.contract_versions),
            "software_versions": dict(self.software_versions),
            "ruleset_versions": dict(self.ruleset_versions),
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the manifest using BIMAP canonical JSON rules."""
        _announce("Encoding Report Manifest JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReportManifest":
        """Parse a strict versioned report-manifest mapping."""
        _announce("Deserializing Report Manifest contract")
        data = validate_contract_fields(
            payload,
            required=(
                "schema_version",
                "report_id",
                "order_id",
                "report_version",
                "generated_at",
                "artifacts",
            ),
            optional=(
                "expires_at",
                "finding_refs",
                "requirement_refs",
                "evidence_refs",
                "contract_versions",
                "software_versions",
                "ruleset_versions",
            ),
            contract=_CONTRACT,
        )
        return cls(
            schema_version=data["schema_version"],
            report_id=data["report_id"],
            order_id=data["order_id"],
            report_version=data["report_version"],
            generated_at=data["generated_at"],
            expires_at=data.get("expires_at"),
            artifacts=data["artifacts"],
            finding_refs=data.get("finding_refs") or (),
            requirement_refs=data.get("requirement_refs") or (),
            evidence_refs=data.get("evidence_refs") or (),
            contract_versions=data.get("contract_versions") or {},
            software_versions=data.get("software_versions") or {},
            ruleset_versions=data.get("ruleset_versions") or {},
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "ReportManifest":
        """Decode canonical JSON and validate it as a Report Manifest."""
        _announce("Decoding Report Manifest JSON")
        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Report Manifest JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


__all__ = ["ReportArtifactContract", "ReportManifest"]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Report Manifest Contract Self-Test ===\n")
    printer.status("TEST", "Report Manifest contract module initialized", "info")

    artifact = ReportArtifactContract(
        artifact_id="ART-0001",
        filename="R3D_Audit_Report.pdf",
        sha256=hashlib.sha256(b"report").hexdigest(),
        size_bytes=6,
    )
    manifest = ReportManifest(
        report_id="REPORT-0001",
        order_id="ORD-0001",
        report_version="1.0",
        generated_at="2026-09-01T00:00:00Z",
        artifacts=(artifact,),
        contract_versions={"report_manifest": REPORT_MANIFEST_SCHEMA_VERSION},
        software_versions={"bimap": "1.0.0"},
        ruleset_versions={"rfa": "1.0.0"},
    )
    assert manifest.artifact("ART-0001") == artifact
    assert ReportManifest.from_json(manifest.to_json()) == manifest
    printer.status("PASS", "Report Manifest round trip", "success")

    print("\n=== Test ran successfully ===\n")