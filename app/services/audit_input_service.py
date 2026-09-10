"""
Audit-source preparation service for BIMAP.

Converts already staged and security-admitted BIM model sources into the
canonical evidence contracts consumed by AuditEngine.

This service does not:
- create orders;
- stage uploads;
- consume audit entitlement;
- enqueue jobs;
- define audit rules;
- fabricate unsupported Revit parsing.
"""

from __future__ import annotations

import hashlib

from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Any

from ..ports.clock import Clock
from ..ports.data_extraction import (
    DataExtractionCapability,
    DataExtractor,
    ExtractedModelData,
    ExtractionDataset,
    ExtractionSourceFormat,
)
from ..ports.storage import Storage, StoredObject
from ..utils.app_errors import *
from ..utils.app_helpers import *

from ...contracts.evidence import EvidenceContract
from ...contracts.family_evidence import FamilyEvidence
from ...contracts.project_evidence import ProjectEvidence
from ...domain.products.models import ProductCode
from ...domain.utils.domain_errors import DomainError

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Input Service")
printer = PrettyPrinter()

_COMPONENT = "audit_input_service"
_MANIFEST_CONTENT_TYPE = "application/vnd.bimap.audit-input+json"
_MANIFEST_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class AuditSourceRef:
    source_ref: str
    filename: str

    def __post_init__(self) -> None:
        source_ref = require_app_text(
            self.source_ref,
            field="source_ref",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_source",
        )

        filename = require_app_text(
            self.filename,
            field="filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_source",
            max_length=255,
        )

        if (
            "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
        ):
            raise AppValidationError(
                "Audit source filename must be a safe basename.",
                component=_COMPONENT,
                operation="validate_source",
                field="filename",
            )

        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "filename", filename)


@dataclass(frozen=True, slots=True)
class PreparedAuditInput:
    order_id: str
    product_code: ProductCode
    manifest_ref: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedAuditInput:
    order_id: str
    product_code: ProductCode
    family_payload: FamilyEvidence | None
    project_payload: ProjectEvidence | None


@dataclass(frozen=True, slots=True)
class _ExtractedSource:
    source: AuditSourceRef
    stored: StoredObject
    source_format: ExtractionSourceFormat
    data: ExtractedModelData


class AuditInputService:
    """Prepare and resolve canonical audit input packages."""

    __slots__ = (
        "_storage",
        "_extractor",
        "_clock",
    )

    def __init__(
        self,
        storage: Storage,
        extractor: DataExtractor,
        clock: Clock,
    ) -> None:
        if not isinstance(storage, Storage):
            raise AppConfigurationError(
                "storage must implement Storage.",
                component=_COMPONENT,
                operation="initialize",
                field="storage",
            )

        if not isinstance(extractor, DataExtractor):
            raise AppConfigurationError(
                "extractor must implement DataExtractor.",
                component=_COMPONENT,
                operation="initialize",
                field="extractor",
            )

        if not isinstance(clock, Clock):
            raise AppConfigurationError(
                "clock must implement Clock.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
            )

        self._storage = storage
        self._extractor = extractor
        self._clock = clock

    @property
    def capabilities(
        self,
    ) -> tuple[DataExtractionCapability, ...]:
        return self._extractor.capabilities

    def _capability_for(
        self,
        filename: str,
    ) -> DataExtractionCapability:
        extension = PurePath(filename).suffix.casefold()

        matches = tuple(
            capability
            for capability in self.capabilities
            if extension in capability.extensions
        )

        if not matches:
            raise UnsupportedAppInputError(
                "Audit source format is not supported by the configured extractor.",
                component=_COMPONENT,
                operation="resolve_source_format",
                field="filename",
                context={
                    "extension": extension or None,
                    "supported_extensions": tuple(
                        sorted(
                            {
                                item
                                for capability in self.capabilities
                                for item in capability.extensions
                            }
                        )
                    ),
                },
            )

        if len(matches) != 1:
            raise AppIntegrityError(
                "Multiple extractors claim the same audit source extension.",
                component=_COMPONENT,
                operation="resolve_source_format",
                field="filename",
                context={
                    "extension": extension,
                    "match_count": len(matches),
                },
            )

        return matches[0]

    def _extract_source(
        self,
        source: AuditSourceRef,
    ) -> _ExtractedSource:
        stored = self._storage.stat(
            source.source_ref
        )

        if stored is None:
            raise AppValidationError(
                "Referenced audit source does not exist.",
                component=_COMPONENT,
                operation="extract_source",
                field="source_ref",
                context={
                    "source_ref": source.source_ref,
                },
            )

        capability = self._capability_for(
            source.filename
        )

        source_format = ExtractionSourceFormat.parse(
            capability.source_format
        )

        datasets = tuple(
            ExtractionDataset.parse(dataset)
            for dataset in capability.datasets
        )

        with closing(
            self._storage.open(source.source_ref)
        ) as stream:
            extracted = self._extractor.extract(
                stream,
                source_format=source_format,
                datasets=datasets,
            )

        if not isinstance(
            extracted,
            ExtractedModelData,
        ):
            raise AppIntegrityError(
                "Audit extractor returned an unsupported result.",
                component=_COMPONENT,
                operation="extract_source",
                field="result",
                context={
                    "received_type": type(
                        extracted
                    ).__name__,
                },
            )

        return _ExtractedSource(
            source=source,
            stored=stored,
            source_format=source_format,
            data=extracted,
        )

    @staticmethod
    def _element_identifier(
        value: Any,
    ) -> str | None:
        if not isinstance(value, dict):
            try:
                value = dict(value)
            except (TypeError, ValueError):
                return None

        keys = (
            "global_id",
            "GlobalId",
            "globalId",
            "guid",
            "GUID",
            "element_id",
            "elementId",
            "id",
        )

        for key in keys:
            candidate = value.get(key)

            if isinstance(candidate, str):
                normalized = candidate.strip()
                if normalized:
                    return normalized

            if isinstance(candidate, int) and not isinstance(
                candidate,
                bool,
            ):
                return str(candidate)

        return None

    def _evidence(
        self,
        source: _ExtractedSource,
        *,
        path: str,
        value: Any,
    ) -> EvidenceContract:
        primitive = to_app_primitive(
            value,
            field="audit_evidence_value",
        )

        identity_payload = canonical_app_json(
            {
                "source_ref": source.source.source_ref,
                "path": path,
                "value": primitive,
            }
        ).encode("utf-8")

        evidence_id = (
            "EV-"
            + hashlib.sha256(
                identity_payload
            ).hexdigest()[:24].upper()
        )

        element = self._element_identifier(
            primitive
        )

        return EvidenceContract(
            evidence_id=evidence_id,
            source_file_id=source.source.source_ref,
            source_hash=source.stored.content_hash,
            hash_algorithm=source.stored.hash_algorithm,
            source_type=source.source_format.value,
            original_filename=source.source.filename,
            extracted_at=self._clock.now(),
            logical_location={
                "path": path,
                **(
                    {"element": element}
                    if element is not None
                    else {}
                ),
            },
            extracted_value=primitive,
            confidence=None,
        )

    def _source_manifest(
        self,
        source: _ExtractedSource,
    ) -> dict[str, Any]:
        return {
            "source_file_id":
                source.source.source_ref,
            "original_filename":
                source.source.filename,
            "source_type":
                source.source_format.value,
            "source_hash":
                source.stored.content_hash,
            "hash_algorithm":
                source.stored.hash_algorithm,
            "size_bytes":
                source.stored.size_bytes,
            "schema":
                source.data.inspection.schema,
            "product_count":
                source.data.inspection.product_count,
        }

    def _family_contract(
        self,
        sources: tuple[_ExtractedSource, ...],
    ) -> FamilyEvidence:
        family_identity: list[EvidenceContract] = []
        type_catalog: list[EvidenceContract] = []
        parameters: list[EvidenceContract] = []
        materials: list[EvidenceContract] = []
        geometry_metrics: list[EvidenceContract] = []

        for source in sources:
            family_identity.append(
                self._evidence(
                    source,
                    path="source.identity",
                    value={
                        "inspection":
                            source.data.inspection.to_dict(),
                        "project":
                            dict(source.data.project),
                        "units": [
                            dict(item)
                            for item
                            in source.data.units
                        ],
                        "counts":
                            dict(source.data.counts),
                        "ifc_class_counts":
                            dict(
                                source.data.ifc_class_counts
                            ),
                    },
                )
            )

            targets = {
                "elements": type_catalog,
                "properties": parameters,
                "materials": materials,
                "quantities": geometry_metrics,
            }

            for dataset_name, target in targets.items():
                rows = source.data.datasets.get(
                    dataset_name,
                    (),
                )

                for index, row in enumerate(rows):
                    target.append(
                        self._evidence(
                            source,
                            path=(
                                f"datasets."
                                f"{dataset_name}[{index}]"
                            ),
                            value=dict(row),
                        )
                    )

        return FamilyEvidence(
            family_identity=tuple(family_identity),
            type_catalog=tuple(type_catalog),
            parameters=tuple(parameters),
            materials=tuple(materials),
            geometry_metrics=tuple(
                geometry_metrics
            ),
            source_manifest={
                "sources": [
                    self._source_manifest(source)
                    for source in sources
                ],
            },
        )

    def _project_contract(
        self,
        source: _ExtractedSource,
    ) -> ProjectEvidence:
        identity = self._evidence(
            source,
            path="source.identity",
            value={
                "inspection":
                    source.data.inspection.to_dict(),
                "project":
                    dict(source.data.project),
                "units": [
                    dict(item)
                    for item
                    in source.data.units
                ],
                "counts":
                    dict(source.data.counts),
                "ifc_class_counts":
                    dict(
                        source.data.ifc_class_counts
                    ),
            },
        )

        dataset_evidence: list[
            EvidenceContract
        ] = []

        for dataset_name in (
            "elements",
            "properties",
            "quantities",
            "materials",
        ):
            rows = source.data.datasets.get(
                dataset_name,
                (),
            )

            for index, row in enumerate(rows):
                dataset_evidence.append(
                    self._evidence(
                        source,
                        path=(
                            f"datasets."
                            f"{dataset_name}[{index}]"
                        ),
                        value=dict(row),
                    )
                )

        project_id = (
            source.data.inspection.project_name
            or f"source:{source.source.source_ref}"
        )

        if (
            source.source_format
            is ExtractionSourceFormat.IFC
        ):
            model_qa_evidence = (identity,)
            ifc_evidence = tuple(
                dataset_evidence
            )
        else:
            model_qa_evidence = (
                identity,
                *dataset_evidence,
            )
            ifc_evidence = ()

        return ProjectEvidence(
            project_id=project_id,
            model_qa_evidence=tuple(
                model_qa_evidence
            ),
            ifc_evidence=tuple(
                ifc_evidence
            ),
            source_manifest=self._source_manifest(
                source
            ),
        )

    @staticmethod
    def _classify_sources(
        product_code: ProductCode,
        sources: tuple[_ExtractedSource, ...],
    ) -> tuple[
        tuple[_ExtractedSource, ...],
        _ExtractedSource | None,
    ]:
        family_sources = tuple(
            source
            for source in sources
            if source.source_format
            is ExtractionSourceFormat.RFA
        )

        project_sources = tuple(
            source
            for source in sources
            if source.source_format
            in {
                ExtractionSourceFormat.RVT,
                ExtractionSourceFormat.IFC,
            }
        )

        if product_code is ProductCode.FAMILY_AUDIT:
            if (
                len(sources) != 1
                or len(family_sources) != 1
            ):
                raise AppValidationError(
                    "Family Audit requires exactly one RFA source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )

            return family_sources, None

        if product_code is ProductCode.BIM_QA:
            if (
                len(sources) != 1
                or len(project_sources) != 1
            ):
                raise AppValidationError(
                    "BIM QA requires exactly one RVT or IFC source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )

            return (), project_sources[0]

        if product_code is ProductCode.COMBINED_AUDIT:
            if not family_sources:
                raise AppValidationError(
                    "Combined Audit requires at least one RFA family source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )

            if len(project_sources) != 1:
                raise AppValidationError(
                    "Combined Audit requires exactly one RVT or IFC project source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )

            if (
                len(family_sources)
                + len(project_sources)
                != len(sources)
            ):
                raise AppValidationError(
                    "Combined Audit accepts only RFA family sources and one RVT or IFC project source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )

            return (
                family_sources,
                project_sources[0],
            )

        raise AppValidationError(
            "Unsupported audit product.",
            component=_COMPONENT,
            operation="classify_sources",
            field="product_code",
        )

    def prepare(
        self,
        order_id: str,
        product_code: ProductCode | str,
        sources: tuple[AuditSourceRef, ...],
    ) -> PreparedAuditInput:
        target_order = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="prepare",
        )

        if not sources:
            raise AppValidationError(
                "At least one staged audit source is required.",
                component=_COMPONENT,
                operation="prepare",
                field="sources",
            )

        try:
            product = ProductCode.parse(
                product_code
            )
        except DomainError as exc:
            raise AppValidationError(
                "Audit product code is invalid.",
                component=_COMPONENT,
                operation="prepare",
                field="product_code",
                cause=exc,
            ) from exc

        extracted = tuple(
            self._extract_source(source)
            for source in sources
        )

        family_sources, project_source = (
            self._classify_sources(
                product,
                extracted,
            )
        )

        family_payload = (
            self._family_contract(
                family_sources
            )
            if family_sources
            else None
        )

        project_payload = (
            self._project_contract(
                project_source
            )
            if project_source is not None
            else None
        )

        evidence_refs = tuple(
            [
                *(
                    family_payload.evidence_ids()
                    if family_payload is not None
                    else ()
                ),
                *(
                    project_payload.evidence_ids()
                    if project_payload is not None
                    else ()
                ),
            ]
        )

        if not evidence_refs:
            raise AppValidationError(
                "Audit extraction produced no canonical evidence.",
                component=_COMPONENT,
                operation="prepare",
                field="evidence",
            )

        document = {
            "schema_version":
                _MANIFEST_SCHEMA_VERSION,
            "order_id":
                target_order,
            "product_code":
                product.value,
            "family_payload": (
                family_payload.to_dict()
                if family_payload is not None
                else None
            ),
            "project_payload": (
                project_payload.to_dict()
                if project_payload is not None
                else None
            ),
            "evidence_refs":
                list(evidence_refs),
        }

        payload = canonical_app_json(
            document
        ).encode("utf-8")

        digest = hashlib.sha256(
            payload
        ).hexdigest()

        manifest_ref = (
            "audit-input-"
            + hashlib.sha256(
                target_order.encode("utf-8")
            ).hexdigest()[:16]
            + "-"
            + digest
        )

        self._storage.put(
            BytesIO(payload),
            object_id=manifest_ref,
            content_type=_MANIFEST_CONTENT_TYPE,
            hash_algorithm="sha256",
            expected_size_bytes=len(payload),
            expected_hash=digest,
        )

        logger.info(
            {
                "event":
                    "audit_input_prepared",
                "order_id":
                    target_order,
                "product_code":
                    product.value,
                "source_count":
                    len(sources),
                "evidence_count":
                    len(evidence_refs),
                "manifest_ref":
                    manifest_ref,
            }
        )

        return PreparedAuditInput(
            order_id=target_order,
            product_code=product,
            manifest_ref=manifest_ref,
            evidence_refs=evidence_refs,
        )

    def resolve(
        self,
        manifest_ref: str,
        *,
        expected_order_id: str,
        expected_product_code: ProductCode | str,
    ) -> ResolvedAuditInput:
        target_ref = require_app_text(
            manifest_ref,
            field="manifest_ref",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="resolve",
        )

        with closing(
            self._storage.open(target_ref)
        ) as stream:
            payload = stream.read()

        document = decode_app_json_object(
            payload,
            field="audit_input_manifest",
        )

        order_id = require_app_text(
            document.get("order_id"),
            field="order_id",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="resolve",
        )

        if order_id != expected_order_id:
            raise AppIntegrityError(
                "Audit input belongs to another order.",
                component=_COMPONENT,
                operation="resolve",
                field="order_id",
            )

        try:
            product = ProductCode.parse(
                document.get("product_code")
            )
            expected_product = ProductCode.parse(
                expected_product_code
            )
        except DomainError as exc:
            raise AppIntegrityError(
                "Audit input contains an invalid product code.",
                component=_COMPONENT,
                operation="resolve",
                field="product_code",
                cause=exc,
            ) from exc

        if product is not expected_product:
            raise AppIntegrityError(
                "Audit input product does not match the audit job.",
                component=_COMPONENT,
                operation="resolve",
                field="product_code",
            )

        raw_family = document.get(
            "family_payload"
        )
        raw_project = document.get(
            "project_payload"
        )

        family_payload = (
            None
            if raw_family is None
            else FamilyEvidence.from_dict(
                raw_family
            )
        )

        project_payload = (
            None
            if raw_project is None
            else ProjectEvidence.from_dict(
                raw_project
            )
        )

        return ResolvedAuditInput(
            order_id=order_id,
            product_code=product,
            family_payload=family_payload,
            project_payload=project_payload,
        )


__all__ = [
    "AuditSourceRef",
    "PreparedAuditInput",
    "ResolvedAuditInput",
    "AuditInputService",
]
