"""
Analytical evidence-manifest validation for BIMAP ingestion.

This module validates the *analytical* consistency of already accepted BIMAP
evidence contracts before semantic normalization/rule execution.  It does not
perform upload-security checks such as file-size limits, malware scanning,
magic-number inspection, archive traversal protection, object-storage access,
or cryptographic verification against raw uploaded bytes.  Those concerns
belong to the outer upload/infrastructure boundary.

The source manifest embedded in Family/Project Evidence is deliberately treated
as extractor-specific JSON metadata.  BIMAP contracts intentionally do not
hard-code its future Revit/IFC exporter fields, so this validator preserves that
metadata without inventing mandatory keys.  Provenance consistency is derived
from the authoritative ``EvidenceContract`` objects themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...contracts.evidence import EvidenceContract
from ...contracts.family_evidence import FamilyEvidence as FamilyEvidenceContract
from ...contracts.project_evidence import ProjectEvidence as ProjectEvidenceContract
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Engine Manifest")
printer = PrettyPrinter()


@dataclass(frozen=True, slots=True)
class ManifestSource:
    """One immutable source identity represented by an accepted evidence package."""

    source_file_id: str
    source_hash: str
    hash_algorithm: str
    source_type: str
    evidence_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic source-manifest primitives."""
        announce_engine_action(
            printer,
            logger,
            component="manifest",
            action="Serializing manifest source",
            event="manifest_source_to_dict_start",
            context={"evidence_count": len(self.evidence_ids)},
        )
        return {
            "source_file_id": self.source_file_id,
            "source_hash": self.source_hash,
            "hash_algorithm": self.hash_algorithm,
            "source_type": self.source_type,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    """Derived analytical manifest for one validated evidence package."""

    ingestion_type: IngestionKind
    schema_version: str
    evidence_ids: tuple[str, ...]
    sources: tuple[ManifestSource, ...]
    source_manifest: dict[str, Any]

    @property
    def evidence_count(self) -> int:
        """Number of accepted canonical evidence objects."""
        return len(self.evidence_ids)

    @property
    def source_count(self) -> int:
        """Number of distinct source_file_id values represented in evidence."""
        return len(self.sources)

    def get_source(self, source_file_id: str) -> ManifestSource | None:
        """Resolve one derived source record without exposing evidence content."""
        announce_engine_action(
            printer,
            logger,
            component="manifest",
            action="Resolving manifest source",
            event="evidence_manifest_get_source_start",
        )
        if not isinstance(source_file_id, str) or not source_file_id.strip():
            raise ManifestValidationError(
                "source_file_id must be non-empty text.",
                component="manifest",
                operation="get_source",
                field="source_file_id",
            )
        target = source_file_id.strip()
        for source in self.sources:
            if source.source_file_id == target:
                return source
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready analytical manifest."""
        announce_engine_action(
            printer,
            logger,
            component="manifest",
            action="Serializing analytical evidence manifest",
            event="evidence_manifest_to_dict_start",
            context={
                "ingestion_type": self.ingestion_type.value,
                "evidence_count": self.evidence_count,
                "source_count": self.source_count,
            },
        )
        return {
            "ingestion_type": self.ingestion_type.value,
            "schema_version": self.schema_version,
            "evidence_count": self.evidence_count,
            "source_count": self.source_count,
            "evidence_ids": list(self.evidence_ids),
            "sources": [source.to_dict() for source in self.sources],
            "source_manifest": to_engine_primitive(
                self.source_manifest,
                field="source_manifest",
            ),
        }


class Manifest:
    """Validate accepted Family/Project Evidence and derive source provenance."""

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="manifest",
            action="Initializing ingestion manifest validator",
            event="manifest_init_start",
        )
        logger.debug({"event": "manifest_initialized"})

    def validate(
        self,
        package: FamilyEvidenceContract | ProjectEvidenceContract,
    ) -> EvidenceManifest:
        """
        Validate analytical package integrity and derive a stable manifest.

        Empty evidence collections are intentionally valid here.  Evidence
        sufficiency is a rule/assessment concern and must not be converted into
        a false ingestion failure merely because no observable evidence exists.
        """
        announce_engine_action(
            printer,
            logger,
            component="manifest",
            action="Validating analytical evidence manifest",
            event="manifest_validate_start",
            context={"package_type": type(package).__name__},
        )

        if isinstance(package, ProjectEvidenceContract):
            ingestion_type = IngestionKind.PROJECT_EVIDENCE
        elif isinstance(package, FamilyEvidenceContract):
            ingestion_type = IngestionKind.FAMILY_EVIDENCE
        else:
            raise ManifestValidationError(
                "Manifest accepts canonical FamilyEvidence or ProjectEvidence contracts only.",
                component="manifest",
                operation="validate",
                field="package",
                context={"received_type": type(package).__name__},
            )

        # The versioned contracts already guarantee EvidenceContract members
        # and globally unique evidence_id values.  The engine does not repeat
        # those lower-layer schema invariants; it adds the analytical
        # cross-record source-provenance check that the contract does not own.
        evidence_items = tuple(package.all_evidence())
        sources = self._build_sources(evidence_items)

        source_manifest_primitive = to_engine_primitive(
            package.source_manifest,
            field="source_manifest",
        )
        if not isinstance(source_manifest_primitive, dict):
            # Contract validation should already guarantee this.  Keeping this
            # defensive check makes the engine boundary explicit if a future
            # contract implementation regresses.
            raise ManifestValidationError(
                "source_manifest must resolve to a JSON object.",
                component="manifest",
                operation="validate",
                field="source_manifest",
                context={"received_type": type(source_manifest_primitive).__name__},
            )

        manifest = EvidenceManifest(
            ingestion_type=ingestion_type,
            schema_version=package.schema_version,
            evidence_ids=tuple(item.evidence_id for item in evidence_items),
            sources=sources,
            source_manifest=dict(source_manifest_primitive),
        )

        logger.debug(
            {
                "event": "manifest_validated",
                "ingestion_type": ingestion_type.value,
                "schema_version": package.schema_version,
                "evidence_count": manifest.evidence_count,
                "source_count": manifest.source_count,
            }
        )
        return manifest

    def _build_sources(
        self,
        evidence_items: tuple[EvidenceContract, ...],
    ) -> tuple[ManifestSource, ...]:
        """Build one source record per stable source_file_id in first-seen order."""
        announce_engine_action(
            printer,
            logger,
            component="manifest",
            action="Deriving manifest source provenance",
            event="manifest_build_sources_start",
            context={"evidence_count": len(evidence_items)},
        )

        signatures: dict[str, tuple[str, str, str]] = {}
        source_evidence: dict[str, list[str]] = {}
        source_order: list[str] = []

        for item in evidence_items:
            source_file_id = item.source_file_id
            signature = (
                item.hash_algorithm,
                item.source_hash,
                item.source_type,
            )
            previous = signatures.get(source_file_id)
            if previous is None:
                signatures[source_file_id] = signature
                source_evidence[source_file_id] = [item.evidence_id]
                source_order.append(source_file_id)
                continue

            if previous != signature:
                raise ManifestSourceConflictError(
                    "One source_file_id resolves to inconsistent source provenance.",
                    component="manifest",
                    operation="validate",
                    field="source_file_id",
                    context={"source_file_id": source_file_id},
                )
            source_evidence[source_file_id].append(item.evidence_id)

        return tuple(
            ManifestSource(
                source_file_id=source_file_id,
                hash_algorithm=signatures[source_file_id][0],
                source_hash=signatures[source_file_id][1],
                source_type=signatures[source_file_id][2],
                evidence_ids=tuple(source_evidence[source_file_id]),
            )
            for source_file_id in source_order
        )


__all__ = [
    "ManifestSource",
    "EvidenceManifest",
    "Manifest",
]


if __name__ == "__main__":
    import hashlib

    print("\n=== Running Ingestion Manifest Self-Test ===\n")
    printer.status("TEST", "Ingestion manifest module initialized", "info")

    digest = hashlib.sha256(b"manifest-self-test").hexdigest()
    evidence = EvidenceContract(
        evidence_id="EV-MANIFEST-1",
        source_file_id="SRC-MANIFEST-1",
        source_hash=digest,
        source_type="json",
        extracted_at="2026-09-02T00:00:00Z",
        extracted_value={"observable": True},
        logical_location={"path": "requirements[0]"},
        confidence=1.0,
    )
    package = ProjectEvidenceContract(
        project_id="PROJECT-MANIFEST-1",
        requirements=(evidence,),
        source_manifest={"extractor": "self-test"},
    )
    derived = Manifest().validate(package)
    assert derived.ingestion_type is IngestionKind.PROJECT_EVIDENCE
    assert derived.evidence_count == 1
    assert derived.source_count == 1
    assert derived.get_source("SRC-MANIFEST-1") is not None
    printer.status("PASS", "Analytical manifest derivation", "success")

    print("\n=== Test ran successfully ===\n")