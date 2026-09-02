"""
Canonical evidence-package dispatcher for BIMAP ingestion.

The dispatcher selects exactly one supported versioned evidence contract:
``FamilyEvidence`` or ``ProjectEvidence``.  It accepts typed contracts, mapping
payloads, or serialized JSON objects.  Strings are treated as JSON content,
never as filesystem paths; controlled file retrieval and upload-security checks
belong to outer application/infrastructure adapters.

When callers know the package type they should declare it explicitly.  For an
undeclared mapping, the dispatcher uses the existing contract discriminator:
``ProjectEvidence`` requires ``project_id`` while ``FamilyEvidence`` does not.
It does not duplicate section vocabularies or infer product scope from file
extensions, filenames, or free-text content.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .manifest import EvidenceManifest, Manifest
from .project_evidence import ProjectEvidence as ProjectEvidenceImporter
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ...contracts.family_evidence import FamilyEvidence as FamilyEvidenceContract
from ...contracts.project_evidence import ProjectEvidence as ProjectEvidenceContract
from ...contracts.utils.contracts_errors import ContractError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Engine Dispatcher")
printer = PrettyPrinter()

SerializedJSON = str | bytes | bytearray
DispatchInput = (
    FamilyEvidenceContract
    | ProjectEvidenceContract
    | Mapping[str, Any]
    | SerializedJSON
)
DispatchedContract = FamilyEvidenceContract | ProjectEvidenceContract


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Typed accepted contract plus the analytical manifest derived from it."""

    ingestion_type: IngestionKind
    contract: DispatchedContract
    manifest: EvidenceManifest


class Dispatcher:
    """Route incoming canonical evidence payloads to the correct importer."""

    def __init__(
        self,
        *,
        manifest_validator: Manifest | None = None,
        project_importer: ProjectEvidenceImporter | None = None,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Initializing ingestion dispatcher",
            event="dispatcher_init_start",
        )

        self._manifest = manifest_validator or Manifest()
        self._project_importer = project_importer or ProjectEvidenceImporter(
            manifest_validator=self._manifest
        )
        logger.debug({"event": "dispatcher_initialized"})

    def dispatch(
        self,
        payload: DispatchInput,
        *,
        declared_type: IngestionKind | str | None = None,
    ) -> DispatchResult:
        """Validate the declared/derived type and dispatch one evidence package."""
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Dispatching evidence package",
            event="dispatcher_dispatch_start",
            context={
                "payload_type": type(payload).__name__,
                "declared_type": (
                    declared_type.value
                    if isinstance(declared_type, IngestionKind)
                    else declared_type
                ),
            },
        )

        declared = normalize_ingestion_kind(declared_type)

        if isinstance(payload, ProjectEvidenceContract):
            self._require_declared_match(
                declared=declared,
                actual=IngestionKind.PROJECT_EVIDENCE,
            )
            return self._dispatch_project(payload)

        if isinstance(payload, FamilyEvidenceContract):
            self._require_declared_match(
                declared=declared,
                actual=IngestionKind.FAMILY_EVIDENCE,
            )
            return self._dispatch_family(payload)

        mapping = self._coerce_payload_mapping(payload)
        inferred = infer_ingestion_kind(mapping)
        self._require_mapping_declared_consistency(
            declared=declared,
            inferred=inferred,
            mapping=mapping,
        )
        actual = declared or inferred

        if actual is IngestionKind.PROJECT_EVIDENCE:
            return self._dispatch_project(mapping)
        if actual is IngestionKind.FAMILY_EVIDENCE:
            return self._dispatch_family(mapping)

        # ``normalize_ingestion_kind`` currently makes this unreachable.  Keep a
        # closed dispatch guard so adding an enum member cannot silently bypass
        # implementation support in the future.
        raise UnsupportedIngestionTypeError(
            "Ingestion type has no registered dispatcher implementation.",
            component="dispatcher",
            operation="dispatch",
            field="declared_type",
            context={"ingestion_type": actual.value},
        )

    def _coerce_payload_mapping(self, payload: DispatchInput) -> dict[str, Any]:
        """Normalize untyped dispatcher input to one object-shaped mapping."""
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Preparing evidence payload for dispatch",
            event="dispatcher_coerce_payload_start",
            context={"payload_type": type(payload).__name__},
        )

        if isinstance(payload, Mapping):
            return require_engine_mapping(payload, field="payload")
        if isinstance(payload, (str, bytes, bytearray)):
            return decode_json_object(payload, field="payload")

        raise UnsupportedIngestionTypeError(
            "Dispatcher input must be a supported evidence contract, mapping, or JSON payload.",
            component="dispatcher",
            operation="dispatch",
            field="payload",
            context={"received_type": type(payload).__name__},
        )

    def _require_mapping_declared_consistency(
        self,
        *,
        declared: IngestionKind | None,
        inferred: IngestionKind,
        mapping: Mapping[str, Any],
    ) -> None:
        """Reject an explicit family declaration when project_id proves otherwise."""
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Checking declared type against payload structure",
            event="dispatcher_mapping_declared_consistency_start",
            context={
                "declared": declared.value if declared is not None else None,
                "inferred": inferred.value,
                "has_project_id": "project_id" in mapping,
            },
        )

        # project_id is a required ProjectEvidence field and is not part of the
        # closed FamilyEvidence contract.  Its presence is therefore a strong
        # contradiction to a family declaration.  The inverse is not true:
        # project evidence missing project_id is simply malformed and should be
        # reported by the ProjectEvidence contract as a missing required field.
        if (
            declared is IngestionKind.FAMILY_EVIDENCE
            and inferred is IngestionKind.PROJECT_EVIDENCE
        ):
            raise DeclaredIngestionTypeMismatchError(
                "Declared Family Evidence type conflicts with the payload structure.",
                component="dispatcher",
                operation="dispatch",
                field="declared_type",
                context={
                    "declared": declared.value,
                    "inferred": inferred.value,
                },
            )

    def _require_declared_match(
        self,
        *,
        declared: IngestionKind | None,
        actual: IngestionKind,
    ) -> None:
        """Reject a caller declaration that contradicts a typed contract."""
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Validating declared ingestion type",
            event="dispatcher_declared_match_start",
            context={
                "declared": declared.value if declared is not None else None,
                "actual": actual.value,
            },
        )

        if declared is None or declared is actual:
            return
        raise DeclaredIngestionTypeMismatchError(
            "Declared ingestion type conflicts with the typed evidence contract.",
            component="dispatcher",
            operation="dispatch",
            field="declared_type",
            context={"declared": declared.value, "actual": actual.value},
        )

    def _dispatch_family(
        self,
        payload: FamilyEvidenceContract | Mapping[str, Any],
    ) -> DispatchResult:
        """Parse/validate Family Evidence without inventing a second importer."""
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Dispatching Family Evidence",
            event="dispatcher_family_start",
            context={"payload_type": type(payload).__name__},
        )

        try:
            contract = (
                payload
                if isinstance(payload, FamilyEvidenceContract)
                else FamilyEvidenceContract.from_dict(payload)
            )
        except ContractError as exc:
            raise IngestionValidationError(
                "Family Evidence payload does not satisfy the canonical contract.",
                component="dispatcher",
                operation="dispatch_family",
                field="payload",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        manifest = self._manifest.validate(contract)
        if manifest.ingestion_type is not IngestionKind.FAMILY_EVIDENCE:
            raise IngestionValidationError(
                "Derived manifest type is inconsistent with Family Evidence.",
                component="dispatcher",
                operation="dispatch_family",
                field="manifest.ingestion_type",
                context={"received": manifest.ingestion_type.value},
            )

        logger.debug(
            {
                "event": "dispatcher_family_complete",
                "schema_version": contract.schema_version,
                "evidence_count": manifest.evidence_count,
                "source_count": manifest.source_count,
            }
        )
        return DispatchResult(
            ingestion_type=IngestionKind.FAMILY_EVIDENCE,
            contract=contract,
            manifest=manifest,
        )

    def _dispatch_project(
        self,
        payload: ProjectEvidenceContract | Mapping[str, Any],
    ) -> DispatchResult:
        """Delegate Project Evidence parsing/manifest validation to its importer."""
        announce_engine_action(
            printer,
            logger,
            component="dispatcher",
            action="Dispatching Project Evidence",
            event="dispatcher_project_start",
            context={"payload_type": type(payload).__name__},
        )

        result = self._project_importer.ingest(payload)
        logger.debug(
            {
                "event": "dispatcher_project_complete",
                "project_id": result.contract.project_id,
                "schema_version": result.contract.schema_version,
                "evidence_count": result.manifest.evidence_count,
                "source_count": result.manifest.source_count,
            }
        )
        return DispatchResult(
            ingestion_type=IngestionKind.PROJECT_EVIDENCE,
            contract=result.contract,
            manifest=result.manifest,
        )


__all__ = [
    "DispatchInput",
    "DispatchedContract",
    "DispatchResult",
    "Dispatcher",
]


if __name__ == "__main__":
    print("\n=== Running Ingestion Dispatcher Self-Test ===\n")
    printer.status("TEST", "Ingestion dispatcher module initialized", "info")

    dispatcher = Dispatcher()

    project_payload = {
        "schema_version": "1.0.0",
        "project_id": "PROJECT-DISPATCH-1",
        "requirements": [],
        "schedules": [],
        "registers": [],
        "model_qa_evidence": [],
        "ifc_evidence": [],
        "images": [],
        "family_evidence_refs": [],
        "source_manifest": {},
    }
    project_result = dispatcher.dispatch(project_payload)
    assert project_result.ingestion_type is IngestionKind.PROJECT_EVIDENCE

    family_payload = {
        "schema_version": "1.0.0",
        "source_manifest": {},
    }
    family_result = dispatcher.dispatch(
        family_payload,
        declared_type=IngestionKind.FAMILY_EVIDENCE,
    )
    assert family_result.ingestion_type is IngestionKind.FAMILY_EVIDENCE
    printer.status("PASS", "Canonical ingestion dispatch", "success")

    print("\n=== Test ran successfully ===\n")