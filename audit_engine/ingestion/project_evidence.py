"""
Project Evidence importer for BIMAP's deterministic ingestion boundary.

The importer accepts an already-constructed ``ProjectEvidence`` contract, a
mapping representing that contract, or serialized JSON.  It delegates external
schema validation to ``bimap.contracts.project_evidence`` and analytical source
consistency to ``ingestion.manifest``.

It deliberately does *not* parse arbitrary RVT/RFA/IFC/PDF/XLSX files and does
not duplicate canonical evidence normalization.  The next audit-engine layer is
responsible for normalization into domain analysis objects; this importer
returns the validated versioned contract plus its derived analytical manifest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .manifest import EvidenceManifest, Manifest
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...contracts.project_evidence import ProjectEvidence as ProjectEvidenceContract
from ...contracts.utils.contracts_errors import ContractError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Engine Project Evidence")
printer = PrettyPrinter()

SerializedJSON = str | bytes | bytearray
ProjectEvidenceInput = ProjectEvidenceContract | Mapping[str, Any] | SerializedJSON


@dataclass(frozen=True, slots=True)
class ProjectEvidenceIngestionResult:
    """Validated Project Evidence contract and its derived analytical manifest."""

    contract: ProjectEvidenceContract
    manifest: EvidenceManifest


class ProjectEvidence:
    """Parse and analytically validate canonical Project Evidence payloads."""

    def __init__(self, *, manifest_validator: Manifest | None = None) -> None:
        announce_engine_action(
            printer,
            logger,
            component="project_evidence",
            action="Initializing Project Evidence importer",
            event="project_evidence_init_start",
        )
        self._manifest = manifest_validator or Manifest()
        logger.debug({"event": "project_evidence_importer_initialized"})

    def parse(self, payload: ProjectEvidenceInput) -> ProjectEvidenceContract:
        """Parse one supported payload into the authoritative external contract."""
        announce_engine_action(
            printer,
            logger,
            component="project_evidence",
            action="Parsing Project Evidence payload",
            event="project_evidence_parse_start",
            context={"payload_type": type(payload).__name__},
        )

        if isinstance(payload, ProjectEvidenceContract):
            return payload

        try:
            if isinstance(payload, Mapping):
                return ProjectEvidenceContract.from_dict(payload)
            if isinstance(payload, (str, bytes, bytearray)):
                return ProjectEvidenceContract.from_json(payload)
        except ContractError as exc:
            raise ProjectEvidenceIngestionError(
                "Project Evidence payload does not satisfy the canonical contract.",
                component="project_evidence",
                operation="parse",
                field="payload",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        raise UnsupportedIngestionTypeError(
            "Unsupported Project Evidence payload type.",
            component="project_evidence",
            operation="parse",
            field="payload",
            context={"received_type": type(payload).__name__},
        )

    def ingest(self, payload: ProjectEvidenceInput) -> ProjectEvidenceIngestionResult:
        """Parse Project Evidence and validate its analytical source manifest."""
        announce_engine_action(
            printer,
            logger,
            component="project_evidence",
            action="Ingesting Project Evidence",
            event="project_evidence_ingest_start",
            context={"payload_type": type(payload).__name__},
        )

        contract = self.parse(payload)
        manifest = self._manifest.validate(contract)
        if manifest.ingestion_type is not IngestionKind.PROJECT_EVIDENCE:
            # Defensive only: Manifest derives the kind from the typed contract.
            raise ProjectEvidenceIngestionError(
                "Derived manifest type is inconsistent with Project Evidence.",
                component="project_evidence",
                operation="ingest",
                field="manifest.ingestion_type",
                context={"received": manifest.ingestion_type.value},
            )

        logger.debug(
            {
                "event": "project_evidence_ingested",
                "project_id": contract.project_id,
                "schema_version": contract.schema_version,
                "evidence_count": manifest.evidence_count,
                "source_count": manifest.source_count,
            }
        )
        return ProjectEvidenceIngestionResult(contract=contract, manifest=manifest)


__all__ = [
    "ProjectEvidenceInput",
    "ProjectEvidenceIngestionResult",
    "ProjectEvidence",
]


if __name__ == "__main__":
    print("\n=== Running Project Evidence Ingestion Self-Test ===\n")
    printer.status("TEST", "Project Evidence ingestion module initialized", "info")

    payload = {
        "schema_version": "1.0.0",
        "project_id": "PROJECT-INGEST-1",
        "requirements": [],
        "schedules": [],
        "registers": [],
        "model_qa_evidence": [],
        "ifc_evidence": [],
        "images": [],
        "family_evidence_refs": [],
        "source_manifest": {},
    }
    result = ProjectEvidence().ingest(payload)
    assert result.contract.project_id == "PROJECT-INGEST-1"
    assert result.manifest.ingestion_type is IngestionKind.PROJECT_EVIDENCE
    printer.status("PASS", "Project Evidence contract ingestion", "success")

    print("\n=== Test ran successfully ===\n")