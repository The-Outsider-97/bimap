"""
Generate the machine-readable BIMAP evidence manifest.

The evidence manifest is a reporting artifact, not a second evidence model. It
therefore consumes the authoritative ``EvidenceContract`` (or a canonical
``EvidenceItem`` that can be converted to that contract) and preserves the
contract fields without reinterpreting evidence.

The generated manifest contains two views over the same validated data:
``sources`` provides a deduplicated source-level inventory and ``evidence``
contains the complete external evidence records. Source deduplication is strict:
one ``source_file_id`` may not resolve to conflicting hashes/types/versions.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...contracts.evidence import EvidenceContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.evidence.models import EvidenceItem
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Evidence Manifest")
printer = PrettyPrinter()

_COMPONENT = "evidence_manifest"


class EvidenceManifest:
    """Validate evidence records and generate ``evidence_manifest.json`` data."""

    def __init__(self, *, sort_records: bool = True) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing evidence manifest serializer",
            event="evidence_manifest_init",
            context={"sort_records": sort_records},
        )
        self.sort_records = bool(sort_records)
        logger.info({"event": "evidence_manifest_initialized"})

    def _as_contract(self, value: Any, *, index: int) -> EvidenceContract:
        if isinstance(value, EvidenceContract):
            return value
        if isinstance(value, EvidenceItem):
            try:
                return EvidenceContract.from_domain(value)
            except (ContractError, DomainError) as exc:
                raise EvidenceError(
                    "Canonical evidence cannot be converted to the external evidence contract.",
                    component=_COMPONENT,
                    field=f"evidence[{index}]",
                    context={
                        "evidence_id": value.evidence_id,
                        "received_type": type(value).__name__,
                    },
                    cause=exc,
                ) from exc

        raise EvidenceError(
            "Evidence manifest accepts only EvidenceContract or EvidenceItem records.",
            component=_COMPONENT,
            field=f"evidence[{index}]",
            context={"received_type": type(value).__name__},
        )

    def validate(
        self,
        evidence: Iterable[EvidenceContract | EvidenceItem],
    ) -> tuple[EvidenceContract, ...]:
        """Validate record types, identifiers, and source-provenance consistency."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating evidence manifest records",
            event="evidence_manifest_validate_start",
        )

        try:
            raw_records = require_record_sequence(
                evidence,
                accepted_types=(EvidenceContract, EvidenceItem),
                field="evidence",
                allow_empty=False,
            )
            contracts = tuple(
                self._as_contract(record, index=index)
                for index, record in enumerate(raw_records)
            )

            ensure_unique_records(
                contracts,
                identifier=lambda item: item.evidence_id,
                identifier_name="evidence_id",
                component=_COMPONENT,
            )

            source_signatures: dict[str, tuple[str, str, str, str | None]] = {}
            for index, item in enumerate(contracts):
                signature = (
                    item.source_hash,
                    item.hash_algorithm,
                    item.source_type,
                    item.source_version,
                )
                previous = source_signatures.get(item.source_file_id)
                if previous is not None and previous != signature:
                    raise EvidenceProvenanceError(
                        "One source_file_id resolves to conflicting source provenance.",
                        component=_COMPONENT,
                        field=f"evidence[{index}].source_file_id",
                        context={
                            "source_file_id": item.source_file_id,
                            "evidence_id": item.evidence_id,
                        },
                    )
                source_signatures[item.source_file_id] = signature

            if self.sort_records:
                contracts = tuple(sorted(contracts, key=lambda item: item.evidence_id))

            logger.debug(
                {
                    "event": "evidence_manifest_validated",
                    "evidence_count": len(contracts),
                    "source_count": len(source_signatures),
                }
            )
            return contracts
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise EvidenceManifestError(
                "Evidence manifest validation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def validate_evidence(
        self,
        evidence: Iterable[EvidenceContract | EvidenceItem],
    ) -> tuple[EvidenceContract, ...]:
        """Compatibility entry point; delegates to the canonical validator."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating evidence records",
            event="evidence_manifest_validate_evidence_start",
        )
        return self.validate(evidence)

    def validate_provenance(
        self,
        evidence: Iterable[EvidenceContract | EvidenceItem],
    ) -> tuple[EvidenceContract, ...]:
        """Compatibility entry point; provenance is validated by ``validate``."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating evidence provenance",
            event="evidence_manifest_validate_provenance_start",
        )
        return self.validate(evidence)

    @staticmethod
    def _source_inventory(
        contracts: tuple[EvidenceContract, ...],
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}

        for item in contracts:
            current = grouped.get(item.source_file_id)
            if current is None:
                current = {
                    "source_file_id": item.source_file_id,
                    "source_hash": item.source_hash,
                    "hash_algorithm": item.hash_algorithm,
                    "source_type": item.source_type,
                    "original_filename": item.original_filename,
                    "source_version": item.source_version,
                    "extractor_names": set(),
                    "extractor_versions": set(),
                    "schema_versions": set(),
                    "evidence_ids": [],
                }
                grouped[item.source_file_id] = current

            if item.extractor_name:
                current["extractor_names"].add(item.extractor_name)
            if item.extractor_version:
                current["extractor_versions"].add(item.extractor_version)
            if item.schema_version:
                current["schema_versions"].add(item.schema_version)
            current["evidence_ids"].append(item.evidence_id)

        inventory: list[dict[str, Any]] = []
        for source_file_id in sorted(grouped):
            source = grouped[source_file_id]
            inventory.append(
                {
                    "source_file_id": source["source_file_id"],
                    "source_hash": source["source_hash"],
                    "hash_algorithm": source["hash_algorithm"],
                    "source_type": source["source_type"],
                    "original_filename": source["original_filename"],
                    "source_version": source["source_version"],
                    "extractor_names": sorted(source["extractor_names"]),
                    "extractor_versions": sorted(source["extractor_versions"]),
                    "schema_versions": sorted(source["schema_versions"]),
                    "evidence_ids": sorted(source["evidence_ids"]),
                }
            )
        return inventory

    def generate_manifest(
        self,
        evidence: Iterable[EvidenceContract | EvidenceItem],
    ) -> dict[str, Any]:
        """Return the deterministic evidence-manifest payload."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Generating evidence manifest",
            event="evidence_manifest_generate_start",
        )

        try:
            contracts = self.validate(evidence)
            sources = self._source_inventory(contracts)
            payload = {
                "evidence_count": len(contracts),
                "source_count": len(sources),
                "schema_versions": sorted(
                    {item.schema_version for item in contracts if item.schema_version}
                ),
                "sources": sources,
                "evidence": [item.to_dict() for item in contracts],
            }
            logger.info(
                {
                    "event": "evidence_manifest_generated",
                    "evidence_count": len(contracts),
                    "source_count": len(sources),
                }
            )
            return payload
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise EvidenceManifestError(
                "Evidence manifest generation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def serialize(
        self,
        evidence: Iterable[EvidenceContract | EvidenceItem],
        *,
        pretty: bool = True,
    ) -> str:
        """Generate and encode ``evidence_manifest.json`` deterministically."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing evidence manifest JSON",
            event="evidence_manifest_serialize_start",
            context={"pretty": pretty},
        )
        try:
            return canonical_reporting_json(
                self.generate_manifest(evidence),
                pretty=pretty,
            )
        except ReportingError:
            raise
        except (TypeError, ValueError) as exc:
            raise EvidenceManifestError(
                "Evidence manifest JSON serialization failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc


__all__ = ["EvidenceManifest"]