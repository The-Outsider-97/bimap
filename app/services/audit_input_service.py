"""
Audit-source preparation service for BIMAP.

Already staged/security-admitted model sources are extracted once and converted
into the canonical evidence contracts consumed by ``AuditEngine``.  Revit-only
family semantics are read from ``ExtractedModelData.extensions["revit_family"]``
without adding Revit-specific members to the provider-neutral extraction enums.

This service does not create orders, stage uploads, consume entitlement, enqueue
jobs, define audit rules, or fabricate organization policy.
"""

from __future__ import annotations

import hashlib
import json
import struct

from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Any, cast

from ..ports.clock import Clock
from ..ports.model_conversion import *
from ..ports.data_extraction import *
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
_MANIFEST_SCHEMA_VERSION = "1.2.0"
_REVIT_FAMILY_EXTENSION = "revit_family"
_GLB_MAGIC = b"glTF"
_GLB_VERSION = 2
_GLB_JSON_CHUNK_TYPE = 0x4E4F534A
_MAX_VIEWER_JSON_BYTES = (
    64
    * 1024
    * 1024
)


@dataclass(frozen=True, slots=True)
class AuditArtifactRef:
    kind: str
    object_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self,) -> None:
        kind = require_app_text(
            self.kind,
            field="kind",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=64,
        )

        object_id = require_app_text(
            self.object_id,
            field="object_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
        )

        filename = require_app_text(
            self.filename,
            field="filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=255,
        )

        if (
            "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
        ):
            raise AppValidationError(
                "Audit artifact filename must be a safe basename.",
                component=_COMPONENT,
                operation="validate_artifact",
                field="filename",
            )

        content_type = require_app_text(
            self.content_type,
            field="content_type",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=128,
        )

        size_bytes = (
            require_non_negative_int(
                self.size_bytes,
                field="size_bytes",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_artifact",
            )
        )

        if size_bytes <= 0:
            raise AppValidationError(
                "Audit artifact cannot be empty.",
                component=_COMPONENT,
                operation="validate_artifact",
                field="size_bytes",
            )

        sha256 = require_app_text(
            self.sha256,
            field="sha256",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=64,
        ).casefold()

        if (
            len(sha256) != 64
            or any(
                character
                not in
                "0123456789abcdef"
                for character
                in sha256
            )
        ):
            raise AppValidationError(
                "Audit artifact SHA-256 is invalid.",
                component=_COMPONENT,
                operation="validate_artifact",
                field="sha256",
            )

        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "object_id", object_id)
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "content_type", content_type)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "sha256", sha256)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "object_id": self.object_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AuditArtifactRef":
        return cls(
            kind=cast(str, value.get("kind")),
            object_id=cast(str, value.get("object_id")),
            filename=cast(str, value.get("filename")),
            content_type=cast(str, value.get("content_type")),
            size_bytes=cast(int, value.get("size_bytes")),
            sha256=cast(str, value.get("sha256")),
        )


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
        if "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise AppValidationError(
                "Audit source filename must be a safe basename.",
                component=_COMPONENT,
                operation="validate_source",
                field="filename",
            )
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "filename", filename)


@dataclass(frozen=True, slots=True)
class AuditViewerModelRef:
    """
    Reference to a staged derived viewer artifact.

    This is intentionally separate from AuditSourceRef because GLB geometry is
    not authoritative audit evidence.
    """

    viewer_ref: str
    filename: str

    def __post_init__(self) -> None:
        viewer_ref = require_app_text(
            self.viewer_ref,
            field="viewer_ref",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_viewer_model",
        )

        filename = require_app_text(
            self.filename,
            field="filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_viewer_model",
            max_length=255,
        )

        if (
            "/" in filename
            or "\\" in filename
            or filename
            in {
                ".",
                "..",
            }
            or PurePath(filename).suffix.casefold()
            != ".glb"
        ):
            raise AppValidationError(
                "Audit viewer model must be a safe .glb basename.",
                component=_COMPONENT,
                operation="validate_viewer_model",
                field="filename",
            )

        object.__setattr__(self, "viewer_ref", viewer_ref)
        object.__setattr__(self, "filename", filename)


@dataclass(frozen=True, slots=True)
class PreparedAuditInput:
    order_id: str
    product_code: ProductCode
    manifest_ref: str
    evidence_refs: tuple[str, ...]
    artifacts: tuple[AuditArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedAuditInput:
    order_id: str
    product_code: ProductCode
    family_payload: FamilyEvidence | None
    project_payload: ProjectEvidence | None
    artifacts: tuple[AuditArtifactRef, ...] = ()


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
        "_model_converter",
        "_pdf_renderer",
        "_clock",
    )

    def __init__(self, storage: Storage, extractor: DataExtractor, clock: Clock, model_converter: ModelConverter, pdf_renderer: DataExtractionPDFRenderer) -> None:
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
        if not isinstance(model_converter, ModelConverter):
            raise AppConfigurationError(
                "model_converter must implement ModelConverter.",
                component=_COMPONENT,
                operation="initialize",
                field="model_converter",
            )

        if not isinstance(pdf_renderer, DataExtractionPDFRenderer):
            raise AppConfigurationError(
                "pdf_renderer must implement DataExtractionPDFRenderer.",
                component=_COMPONENT,
                operation="initialize",
                field="pdf_renderer",
            )        

        self._storage = storage
        self._extractor = extractor
        self._model_converter = model_converter
        self._pdf_renderer = pdf_renderer
        self._clock = clock

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._extractor.capabilities

    def _capability_for(self, filename: str) -> DataExtractionCapability:
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
                context={"extension": extension, "match_count": len(matches)},
            )
        return matches[0]

    def _extract_source(self, source: AuditSourceRef) -> _ExtractedSource:
        stored = self._storage.stat(source.source_ref)
        if stored is None:
            raise AppValidationError(
                "Referenced audit source does not exist.",
                component=_COMPONENT,
                operation="extract_source",
                field="source_ref",
                context={"source_ref": source.source_ref},
            )

        capability = self._capability_for(source.filename)
        source_format = ExtractionSourceFormat.parse(capability.source_format)
        datasets = tuple(
            ExtractionDataset.parse(dataset)
            for dataset in capability.datasets
        )

        with closing(self._storage.open(source.source_ref)) as stream:
            extracted = self._extractor.extract(
                stream,
                source_format=source_format,
                datasets=datasets,
            )

        if not isinstance(extracted, ExtractedModelData):
            raise AppIntegrityError(
                "Audit extractor returned an unsupported result.",
                component=_COMPONENT,
                operation="extract_source",
                field="result",
                context={"received_type": type(extracted).__name__},
            )

        return _ExtractedSource(
            source=source,
            stored=stored,
            source_format=source_format,
            data=extracted,
        )

    @staticmethod
    def _element_identifier(value: Any) -> str | None:
        if not isinstance(value, Mapping):
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
            "unique_id",
            "uniqueId",
            "element_id",
            "elementId",
            "revit_id",
            "revitId",
            "id",
        )
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str):
                normalized = candidate.strip()
                if normalized:
                    return normalized
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return str(candidate)
        return None

    def _evidence(
        self,
        source: _ExtractedSource,
        *,
        path: str,
        value: Any,
    ) -> EvidenceContract:
        primitive = to_app_primitive(value, field="audit_evidence_value")
        identity_payload = canonical_app_json(
            {
                "source_ref": source.source.source_ref,
                "path": path,
                "value": primitive,
            }
        ).encode("utf-8")
        evidence_id = (
            "EV-"
            + hashlib.sha256(identity_payload).hexdigest()[:24].upper()
        )
        element = self._element_identifier(primitive)

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
                **({"element": element} if element is not None else {}),
            },
            extracted_value=primitive,
            confidence=None,
        )

    @staticmethod
    def _revit_family_extension(source: _ExtractedSource) -> Mapping[str, Any]:
        if source.source_format is not ExtractionSourceFormat.RFA:
            return {}
        value = source.data.extensions.get(_REVIT_FAMILY_EXTENSION)
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise AppIntegrityError(
                "Revit family extraction extension must be a mapping.",
                component=_COMPONENT,
                operation="read_revit_family_extension",
                field=f"extensions.{_REVIT_FAMILY_EXTENSION}",
                context={"received_type": type(value).__name__},
            )
        return value

    @staticmethod
    def _extension_records(
        extension: Mapping[str, Any],
        name: str,
    ) -> tuple[Mapping[str, Any], ...]:
        value = extension.get(name)
        if value is None:
            return ()
        if isinstance(value, Mapping):
            return (dict(value),)
        if isinstance(value, (str, bytes, bytearray)):
            raise AppIntegrityError(
                "Revit family extension section must contain object records.",
                component=_COMPONENT,
                operation="normalize_revit_family_extension",
                field=f"extensions.{_REVIT_FAMILY_EXTENSION}.{name}",
                context={"received_type": type(value).__name__},
            )
        try:
            raw = tuple(value)
        except TypeError as exc:
            raise AppIntegrityError(
                "Revit family extension section must be iterable.",
                component=_COMPONENT,
                operation="normalize_revit_family_extension",
                field=f"extensions.{_REVIT_FAMILY_EXTENSION}.{name}",
                cause=exc,
            ) from exc

        result: list[Mapping[str, Any]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise AppIntegrityError(
                    "Revit family extension rows must be JSON objects.",
                    component=_COMPONENT,
                    operation="normalize_revit_family_extension",
                    field=(
                        f"extensions.{_REVIT_FAMILY_EXTENSION}."
                        f"{name}[{index}]"
                    ),
                    context={"received_type": type(item).__name__},
                )
            result.append(dict(item))
        return tuple(result)

    @staticmethod
    def _generic_rows(
        source: _ExtractedSource,
        dataset_name: str,
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(source.data.datasets.get(dataset_name, ()))

    def _source_manifest(self, source: _ExtractedSource) -> dict[str, Any]:
        manifest: dict[str, Any] = {
            "source_file_id": source.source.source_ref,
            "original_filename": source.source.filename,
            "source_type": source.source_format.value,
            "source_hash": source.stored.content_hash,
            "hash_algorithm": source.stored.hash_algorithm,
            "size_bytes": source.stored.size_bytes,
            "schema": source.data.inspection.schema,
            "product_count": source.data.inspection.product_count,
            "project_name": source.data.inspection.project_name,
            "counts": dict(source.data.counts),
            "geometry_summary": dict(source.data.geometry_summary),
        }

        extension = self._revit_family_extension(source)
        if extension:
            manifest["revit_family"] = {
                key: to_app_primitive(extension.get(key), field=f"revit_family.{key}")
                for key in (
                    "schema_version",
                    "extractor_version",
                    "revit_engine_version",
                    "source_revit_version",
                    "sections_assessed",
                )
                if extension.get(key) is not None
            }
        return manifest

    def _read_bimap_viewer_metadata(self, viewer: AuditViewerModelRef, stored: StoredObject) -> Mapping[str, Any]:
        """
        Read only BIMAP's GLB root JSON metadata.

        No geometry parser or third-party glTF dependency is required.
        """

        operation = ("read_viewer_metadata")

        with closing(self._storage.open(viewer.viewer_ref)) as stream:
            header = stream.read(20)

            if (
                not isinstance(
                    header,
                    (
                        bytes,
                        bytearray,
                        memoryview,
                    ),
                )
                or len(header)
                != 20
            ):
                raise AppIntegrityError(
                    "Stored viewer artifact has an incomplete GLB header.",
                    component=_COMPONENT,
                    operation=operation,
                    field="viewer_model",
                )

            (
                magic,
                version,
                declared_length,
                json_length,
                json_type,
            ) = struct.unpack(
                "<4sIIII",
                bytes(header),
            )

            if (
                magic
                != _GLB_MAGIC
                or version
                != _GLB_VERSION
            ):
                raise AppIntegrityError(
                    "Stored viewer artifact is not a GLB 2.0 container.",
                    component=_COMPONENT,
                    operation=operation,
                    field="viewer_model",
                )

            if (
                declared_length
                != stored.size_bytes
            ):
                raise AppIntegrityError(
                    "Stored viewer GLB length does not match storage metadata.",
                    component=_COMPONENT,
                    operation=operation,
                    field="viewer_model",
                    context={
                        "declared_length":
                            declared_length,
                        "stored_size_bytes":
                            stored.size_bytes,
                    },
                )

            if (
                json_type
                != _GLB_JSON_CHUNK_TYPE
                or json_length
                <= 0
                or json_length
                > _MAX_VIEWER_JSON_BYTES
                or json_length
                % 4
                != 0
                or 20
                + json_length
                > declared_length
            ):
                raise AppIntegrityError(
                    "Stored viewer GLB has an invalid JSON chunk.",
                    component=_COMPONENT,
                    operation=operation,
                    field="viewer_model",
                    context={
                        "json_length":
                            json_length,
                    },
                )

            json_payload = (
                stream.read(
                    json_length
                )
            )

            if (
                not isinstance(
                    json_payload,
                    (
                        bytes,
                        bytearray,
                        memoryview,
                    ),
                )
                or len(
                    json_payload
                )
                != json_length
            ):
                raise AppIntegrityError(
                    "Stored viewer GLB JSON chunk is truncated.",
                    component=_COMPONENT,
                    operation=operation,
                    field="viewer_model",
                )

        try:
            root = json.loads(
                bytes(
                    json_payload
                )
                .rstrip(
                    b" \t\r\n\x00"
                )
                .decode(
                    "utf-8"
                )
            )

        except (
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise AppIntegrityError(
                "Stored viewer GLB contains invalid UTF-8 JSON.",
                component=_COMPONENT,
                operation=operation,
                field="viewer_model",
                cause=exc,
            ) from exc

        if not isinstance(
            root,
            Mapping,
        ):
            raise AppIntegrityError(
                "Stored viewer GLB root must be a JSON object.",
                component=_COMPONENT,
                operation=operation,
                field="viewer_model",
            )

        extras = root.get(
            "extras"
        )

        if not isinstance(
            extras,
            Mapping,
        ):
            raise AppIntegrityError(
                "BIMAP viewer GLB is missing root extras metadata.",
                component=_COMPONENT,
                operation=operation,
                field="viewer_model.extras",
            )

        if (
            extras.get(
                "bimapSchema"
            )
            != "viewer-model/1.0"
        ):
            raise AppIntegrityError(
                "Viewer GLB does not advertise BIMAP viewer-model/1.0.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras.bimapSchema"
                ),
                context={
                    "received":
                        extras.get(
                            "bimapSchema"
                        ),
                },
            )

        return dict(
            extras
        )


    def _uploaded_viewer_artifact(
        self,
        order_id: str,
        viewer: AuditViewerModelRef,
        source: _ExtractedSource,
    ) -> AuditArtifactRef:
        """
        Bind one locally exported GLB to exactly one authoritative RFA/RVT source.
        """

        operation = (
            "bind_viewer_model"
        )

        if (
            source.source_format
            not in {
                ExtractionSourceFormat.RFA,
                ExtractionSourceFormat.RVT,
            }
        ):
            raise UnsupportedAppInputError(
                "A BIMAP Revit Local Exporter GLB can accompany RFA/RVT sources only.",
                component=_COMPONENT,
                operation=operation,
                field="viewer_model",
                context={
                    "source_format":
                        source
                        .source_format
                        .value,
                },
            )

        stored = self._storage.stat(
            viewer.viewer_ref
        )

        if stored is None:
            raise AppValidationError(
                "Referenced viewer model does not exist.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "viewer_ref"
                ),
            )

        if (
            stored.size_bytes
            <= 0
        ):
            raise AppIntegrityError(
                "Stored viewer model is empty.",
                component=_COMPONENT,
                operation=operation,
                field="viewer_model",
            )

        if (
            stored
            .hash_algorithm
            .casefold()
            != "sha256"
        ):
            raise AppIntegrityError(
                "Viewer model must use SHA-256 integrity metadata.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "hash_algorithm"
                ),
            )

        if (
            source
            .stored
            .hash_algorithm
            .casefold()
            != "sha256"
        ):
            raise AppIntegrityError(
                "Authoritative audit source must use SHA-256 before viewer association.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "source."
                    "hash_algorithm"
                ),
            )

        metadata = (
            self
            ._read_bimap_viewer_metadata(
                viewer,
                stored,
            )
        )

        source_kind = (
            metadata.get(
                "sourceKind"
            )
        )

        if (
            source_kind
            != source
            .source_format
            .value
        ):
            raise AppIntegrityError(
                "Viewer GLB source kind does not match the authoritative audit source.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras.sourceKind"
                ),
                context={
                    "expected":
                        source
                        .source_format
                        .value,
                    "received":
                        source_kind,
                },
            )

        if (
            metadata.get(
                "sourceFingerprintKind"
            )
            != "saved-file-sha256"
        ):
            raise AppIntegrityError(
                "Viewer GLB does not contain the required saved-file SHA-256 provenance.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras."
                    "sourceFingerprintKind"
                ),
            )

        if (
            metadata.get(
                "sourceDocumentModifiedAtExport"
            )
            is not False
        ):
            raise AppIntegrityError(
                "Viewer GLB was not exported from a clean saved Revit document.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras."
                    "sourceDocumentModifiedAtExport"
                ),
            )

        source_sha256 = (
            metadata.get(
                "sourceSha256"
            )
        )

        if not isinstance(
            source_sha256,
            str,
        ):
            raise AppIntegrityError(
                "Viewer GLB is missing its source SHA-256.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras.sourceSha256"
                ),
            )

        normalized_source_sha256 = (
            source_sha256
            .strip()
            .casefold()
        )

        if (
            len(
                normalized_source_sha256
            )
            != 64
            or any(
                character
                not in
                "0123456789abcdef"
                for character
                in normalized_source_sha256
            )
        ):
            raise AppIntegrityError(
                "Viewer GLB source SHA-256 is invalid.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras.sourceSha256"
                ),
            )

        expected_source_sha256 = (
            source
            .stored
            .content_hash
            .casefold()
        )

        if (
            normalized_source_sha256
            != expected_source_sha256
        ):
            raise AppIntegrityError(
                "Viewer GLB was generated from a different RFA/RVT source.",
                component=_COMPONENT,
                operation=operation,
                field=(
                    "viewer_model."
                    "extras.sourceSha256"
                ),
                context={
                    "source_ref":
                        source
                        .source
                        .source_ref,
                },
            )

        artifact = AuditArtifactRef(
            kind="viewer_model",
            object_id=(
                stored.object_id
            ),
            filename=(
                viewer.filename
            ),
            content_type=(
                "model/gltf-binary"
            ),
            size_bytes=(
                stored.size_bytes
            ),
            sha256=(
                stored.content_hash
            ),
        )

        logger.info(
            {
                "event":
                    "audit_uploaded_viewer_bound",
                "order_id":
                    order_id,
                "viewer_ref":
                    viewer.viewer_ref,
                "source_ref":
                    source.source.source_ref,
                "source_format":
                    source.source_format.value,
                "size_bytes":
                    stored.size_bytes,
                "source_fingerprint_verified":
                    True,
            }
        )

        return artifact

    def _family_contract(
        self,
        sources: tuple[_ExtractedSource, ...],
        *,
        organization_rules: tuple[EvidenceContract, ...] = (),
    ) -> FamilyEvidence:
        family_identity: list[EvidenceContract] = []
        type_catalog: list[EvidenceContract] = []
        parameters: list[EvidenceContract] = []
        formulas: list[EvidenceContract] = []
        materials: list[EvidenceContract] = []
        connectors: list[EvidenceContract] = []
        nested_components: list[EvidenceContract] = []
        geometry_metrics: list[EvidenceContract] = []
        documentation: list[EvidenceContract] = []

        for source in sources:
            extension = self._revit_family_extension(source)
            identity_records = self._extension_records(extension, "identity")
            if len(identity_records) > 1:
                raise AppIntegrityError(
                    "Revit family extraction must publish exactly one identity object per RFA source.",
                    component=_COMPONENT,
                    operation="build_family_contract",
                    field=f"extensions.{_REVIT_FAMILY_EXTENSION}.identity",
                    context={"identity_record_count": len(identity_records)},
                )
            family_identity_value: Mapping[str, Any] = (
                identity_records[0] if identity_records else {}
            )

            family_identity.append(
                self._evidence(
                    source,
                    path="source.identity",
                    value={
                        # Baseline rules intentionally continue to consume the
                        # authoritative inspection at this stable location.
                        "inspection": source.data.inspection.to_dict(),
                        "project": dict(source.data.project),
                        "units": [dict(item) for item in source.data.units],
                        "counts": dict(source.data.counts),
                        "ifc_class_counts": dict(source.data.ifc_class_counts),
                        "geometry_summary": dict(source.data.geometry_summary),
                        "family": dict(family_identity_value),
                        "extraction": {
                            key: extension.get(key)
                            for key in (
                                "schema_version",
                                "extractor_version",
                                "revit_engine_version",
                                "source_revit_version",
                                "sections_assessed",
                            )
                            if extension.get(key) is not None
                        },
                    },
                )
            )

            semantic_rows = {
                "type_catalog": (
                    self._extension_records(extension, "type_catalog")
                    or self._generic_rows(source, "elements")
                ),
                "parameters": (
                    self._extension_records(extension, "parameters")
                    or self._generic_rows(source, "properties")
                ),
                "materials": (
                    self._extension_records(extension, "materials")
                    or self._generic_rows(source, "materials")
                ),
                "geometry_metrics": (
                    self._extension_records(extension, "geometry_metrics")
                    or self._generic_rows(source, "quantities")
                ),
            }

            # Keep the stable logical prefixes expected by existing baseline
            # integrity rules while improving the semantic row contents.
            stable_sections = (
                ("type_catalog", "elements", type_catalog),
                ("parameters", "properties", parameters),
                ("materials", "materials", materials),
                ("geometry_metrics", "quantities", geometry_metrics),
            )
            for semantic_name, dataset_name, target in stable_sections:
                for index, row in enumerate(semantic_rows[semantic_name]):
                    target.append(
                        self._evidence(
                            source,
                            path=f"datasets.{dataset_name}[{index}]",
                            value=dict(row),
                        )
                    )

            extension_sections = (
                ("formulas", formulas),
                ("connectors", connectors),
                ("nested_components", nested_components),
                ("documentation", documentation),
            )
            for section_name, target in extension_sections:
                for index, row in enumerate(
                    self._extension_records(extension, section_name)
                ):
                    target.append(
                        self._evidence(
                            source,
                            path=f"revit_family.{section_name}[{index}]",
                            value=dict(row),
                        )
                    )

        return FamilyEvidence(
            family_identity=tuple(family_identity),
            type_catalog=tuple(type_catalog),
            parameters=tuple(parameters),
            formulas=tuple(formulas),
            materials=tuple(materials),
            connectors=tuple(connectors),
            nested_components=tuple(nested_components),
            geometry_metrics=tuple(geometry_metrics),
            documentation=tuple(documentation),
            # Organization/customer policy is a separate authoritative input.
            # It must never be inferred from the source RFA.
            organization_rules=tuple(organization_rules),
            source_manifest={
                "sources": [
                    self._source_manifest(source)
                    for source in sources
                ]
            },
        )

    def _project_contract(self, source: _ExtractedSource) -> ProjectEvidence:
        identity = self._evidence(
            source,
            path="source.identity",
            value={
                "inspection": source.data.inspection.to_dict(),
                "project": dict(source.data.project),
                "units": [dict(item) for item in source.data.units],
                "counts": dict(source.data.counts),
                "ifc_class_counts": dict(source.data.ifc_class_counts),
                "geometry_summary": dict(source.data.geometry_summary),
            },
        )

        dataset_evidence: list[EvidenceContract] = []
        for dataset_name in ("elements", "properties", "quantities", "materials"):
            rows = source.data.datasets.get(dataset_name, ())
            for index, row in enumerate(rows):
                dataset_evidence.append(
                    self._evidence(
                        source,
                        path=f"datasets.{dataset_name}[{index}]",
                        value=dict(row),
                    )
                )

        project_id = (
            source.data.inspection.project_name
            or f"source:{source.source.source_ref}"
        )

        if source.source_format is ExtractionSourceFormat.IFC:
            model_qa_evidence = (identity,)
            ifc_evidence = tuple(dataset_evidence)
        else:
            model_qa_evidence = (identity, *dataset_evidence)
            ifc_evidence = ()

        return ProjectEvidence(
            project_id=project_id,
            model_qa_evidence=tuple(model_qa_evidence),
            ifc_evidence=tuple(ifc_evidence),
            source_manifest=self._source_manifest(source),
        )

    @staticmethod
    def _classify_sources(
        product_code: ProductCode,
        sources: tuple[_ExtractedSource, ...],
    ) -> tuple[tuple[_ExtractedSource, ...], _ExtractedSource | None]:
        family_sources = tuple(
            source
            for source in sources
            if source.source_format is ExtractionSourceFormat.RFA
        )
        project_sources = tuple(
            source
            for source in sources
            if source.source_format
            in {ExtractionSourceFormat.RVT, ExtractionSourceFormat.IFC}
        )

        if product_code is ProductCode.FAMILY_AUDIT:
            if len(sources) != 1 or len(family_sources) != 1:
                raise AppValidationError(
                    "Family Audit requires exactly one RFA source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )
            return family_sources, None

        if product_code is ProductCode.BIM_QA:
            if len(sources) != 1 or len(project_sources) != 1:
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
            if len(family_sources) + len(project_sources) != len(sources):
                raise AppValidationError(
                    "Combined Audit accepts only RFA family sources and one RVT or IFC project source.",
                    component=_COMPONENT,
                    operation="classify_sources",
                    field="sources",
                )
            return family_sources, project_sources[0]

        raise AppValidationError(
            "Unsupported audit product.",
            component=_COMPONENT,
            operation="classify_sources",
            field="product_code",
        )

    def _family_data_artifact(
        self,
        order_id: str,
        source: _ExtractedSource,
    ) -> AuditArtifactRef:
        """
        Render and persist the Family Audit data PDF.

        The artifact reuses BIMAP's existing DataExtractionPDFRenderer
        contract and the canonical extraction-report document shape used by
        DataExtractionService.

        The method does not:
        - consume a second entitlement;
        - run model extraction again;
        - create a second PDF renderer;
        - alter deterministic audit evidence;
        - fabricate user identity or project metadata;
        - make preview generation mandatory.

        The already-extracted ``_ExtractedSource.data`` is authoritative.
        """

        operation = "build_family_data_artifact"

        target_order = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )

        if not isinstance(
            source,
            _ExtractedSource,
        ):
            raise UnsupportedAppInputError(
                "source must be an _ExtractedSource instance.",
                component=_COMPONENT,
                operation=operation,
                field="source",
                context={
                    "received_type":
                        type(source).__name__,
                },
            )

        if (
            source.source_format
            is not ExtractionSourceFormat.RFA
        ):
            raise UnsupportedAppInputError(
                "Family data PDF generation requires an RFA source.",
                component=_COMPONENT,
                operation=operation,
                field="source.source_format",
                context={
                    "source_format":
                        source.source_format.value,
                },
            )

        if not isinstance(
            source.data,
            ExtractedModelData,
        ):
            raise AppIntegrityError(
                "Family data artifact requires canonical ExtractedModelData.",
                component=_COMPONENT,
                operation=operation,
                field="source.data",
                context={
                    "received_type":
                        type(source.data).__name__,
                },
            )

        if not isinstance(
            source.stored,
            StoredObject,
        ):
            raise AppIntegrityError(
                "Family data artifact requires stored-source metadata.",
                component=_COMPONENT,
                operation=operation,
                field="source.stored",
                context={
                    "received_type":
                        type(source.stored).__name__,
                },
            )

        # -------------------------------------------------------------
        # Resolve authoritative source SHA-256.
        #
        # The extraction report labels this specifically as SHA-256.
        # Normally BIMAP storage already uses SHA-256. If another
        # algorithm is configured, calculate SHA-256 from the stored
        # source rather than mislabelling another digest.
        # -------------------------------------------------------------

        if (
            source.stored.hash_algorithm.casefold()
            == "sha256"
        ):
            source_sha256 = (
                source.stored.content_hash.casefold()
            )

        else:
            source_digest = hashlib.sha256()

            try:
                with closing(
                    self._storage.open(
                        source.source.source_ref
                    )
                ) as source_stream:
                    while True:
                        chunk = source_stream.read(
                            1024 * 1024
                        )

                        if not chunk:
                            break

                        if not isinstance(
                            chunk,
                            (
                                bytes,
                                bytearray,
                                memoryview,
                            ),
                        ):
                            raise AppIntegrityError(
                                "Stored audit source yielded non-binary data.",
                                component=_COMPONENT,
                                operation=operation,
                                field="source",
                            )

                        source_digest.update(
                            bytes(chunk)
                        )

            except AppError:
                raise

            except Exception as exc:
                raise AppIntegrityError(
                    "Unable to calculate the Family Audit source SHA-256.",
                    component=_COMPONENT,
                    operation=operation,
                    field="source",
                    context=lower_error_context(
                        exc
                    ),
                    cause=exc,
                ) from exc

            source_sha256 = (
                source_digest.hexdigest()
            )

        # -------------------------------------------------------------
        # Generate optional preview.
        #
        # Preview enrichment is deliberately non-authoritative. A
        # missing preview must not invalidate otherwise valid family
        # extraction/report generation. This matches the standalone
        # DataExtractionService policy.
        # -------------------------------------------------------------

        preview_png: bytes | None = None

        try:
            with closing(
                self._storage.open(
                    source.source.source_ref
                )
            ) as source_stream:
                preview_candidate = (
                    self._extractor.render_preview(
                        source_stream,
                        source_format=(
                            source.source_format
                        ),
                    )
                )

            if isinstance(
                preview_candidate,
                (
                    bytes,
                    bytearray,
                    memoryview,
                ),
            ):
                preview_payload = bytes(
                    preview_candidate
                )

                if preview_payload.startswith(
                    b"\x89PNG\r\n\x1a\n"
                ):
                    preview_png = (
                        preview_payload
                    )

                else:
                    logger.warning(
                        {
                            "event":
                                "audit_family_data_preview_invalid",
                            "order_id":
                                target_order,
                            "source_ref":
                                source.source.source_ref,
                            "source_format":
                                source.source_format.value,
                            "reason":
                                "preview_is_not_png",
                        }
                    )

            elif preview_candidate is not None:
                logger.warning(
                    {
                        "event":
                            "audit_family_data_preview_invalid",
                        "order_id":
                            target_order,
                        "source_ref":
                            source.source.source_ref,
                        "source_format":
                            source.source_format.value,
                        "received_type":
                            type(
                                preview_candidate
                            ).__name__,
                    }
                )

        except Exception as exc:
            logger.warning(
                {
                    "event":
                        "audit_family_data_preview_unavailable",
                    "order_id":
                        target_order,
                    "source_ref":
                        source.source.source_ref,
                    "source_format":
                        source.source_format.value,
                    "error":
                        lower_error_context(
                            exc
                        ),
                }
            )

            preview_png = None

        # -------------------------------------------------------------
        # Timestamp.
        #
        # AuditInputService currently has no authoritative browser/user
        # timezone offset. Therefore UTC is retained explicitly instead
        # of fabricating local time.
        # -------------------------------------------------------------

        generated_at_utc = (
            self._clock.now()
        )

        generated_at = (
            format_app_utc_datetime(
                generated_at_utc,
                field="generated_at",
                component=_COMPONENT,
                operation=operation,
            )
        )

        # -------------------------------------------------------------
        # Canonical extraction-report document.
        #
        # This intentionally matches the structure already consumed by
        # ReportLabDataExtractionPDFRenderer:
        #
        #     extraction
        #     source
        #     model
        #
        # The renderer currently reads these exact sections.
        # -------------------------------------------------------------

        selected_datasets = tuple(
            sorted(
                str(name)
                for name
                in source.data.datasets.keys()
            )
        )

        document: dict[
            str,
            object,
        ] = {
            "extraction": {
                "extraction_id": (
                    "AUDIT-"
                    + hashlib.sha256(
                        (
                            target_order
                            + "\0"
                            + source.source.source_ref
                            + "\0"
                            + source_sha256
                        ).encode(
                            "utf-8"
                        )
                    ).hexdigest()[
                        :24
                    ].upper()
                ),

                "generated_at":
                    generated_at,

                # No user/browser timezone is available at this
                # application boundary. Keep UTC explicit.
                "generated_at_local":
                    generated_at,

                "utc_offset_minutes":
                    0,

                "project_name": (
                    source.data
                    .inspection
                    .project_name
                ),

                "datasets":
                    selected_datasets,

                # AuditInputService currently has no authoritative
                # account/display-name dependency. Do not fabricate
                # one. The existing renderer renders None as "—".
                "requested_by": {
                    "account_id":
                        None,
                    "display_name":
                        None,
                },
            },

            "source": {
                "filename":
                    source.source.filename,

                "content_type":
                    source.stored.content_type,

                "source_format":
                    source.source_format.value,

                "schema": (
                    source.data
                    .inspection
                    .schema
                ),

                "size_bytes":
                    source.stored.size_bytes,

                "sha256":
                    source_sha256,

                "product_count": (
                    source.data
                    .inspection
                    .product_count
                ),
            },

            "model":
                source.data.to_dict(),
        }

        # -------------------------------------------------------------
        # Render PDF through the existing abstract renderer.
        #
        # AuditInputService depends on DataExtractionPDFRenderer, not
        # ReportLabDataExtractionPDFRenderer directly.
        # -------------------------------------------------------------

        try:
            rendered = (
                self._pdf_renderer.render(
                    document=document,
                    preview_png=preview_png,
                )
            )

        except AppError:
            raise

        except Exception as exc:
            raise AppIntegrityError(
                "Family data PDF renderer failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation=operation,
                field="pdf_renderer",
                context=lower_error_context(
                    exc
                ),
                cause=exc,
            ) from exc

        if not isinstance(
            rendered,
            (
                bytes,
                bytearray,
                memoryview,
            ),
        ):
            raise AppIntegrityError(
                "Family data PDF renderer returned a non-binary artifact.",
                component=_COMPONENT,
                operation=operation,
                field="pdf",
                context={
                    "received_type":
                        type(rendered).__name__,
                },
            )

        pdf_payload = bytes(
            rendered
        )

        if not pdf_payload:
            raise AppIntegrityError(
                "Family data PDF renderer returned an empty artifact.",
                component=_COMPONENT,
                operation=operation,
                field="pdf",
            )

        if not pdf_payload.startswith(
            b"%PDF"
        ):
            raise AppIntegrityError(
                "Family data renderer output is not a PDF document.",
                component=_COMPONENT,
                operation=operation,
                field="pdf",
            )

        pdf_sha256 = hashlib.sha256(
            pdf_payload
        ).hexdigest()

        # -------------------------------------------------------------
        # Safe deterministic output filename.
        # -------------------------------------------------------------

        raw_stem = PurePath(
            source.source.filename
        ).stem.strip()

        safe_stem = "".join(
            character
            if (
                character.isalnum()
                or character
                in {
                    ".",
                    "_",
                    "-",
                }
            )
            else "-"
            for character
            in raw_stem
        )

        safe_stem = (
            safe_stem
            .strip(
                "._-"
            )[:120]
        )

        if not safe_stem:
            safe_stem = (
                "revit-family"
            )

        filename = (
            f"{safe_stem}"
            "-family-data.pdf"
        )

        # -------------------------------------------------------------
        # Deterministic storage identity.
        #
        # order + kind + content digest makes retrying the same
        # generated artifact naturally idempotent while keeping storage
        # identity independent from the user-visible filename.
        # -------------------------------------------------------------

        order_key = hashlib.sha256(target_order.encode("utf-8")).hexdigest()[:16]

        object_id = (
            "audit-artifact-"
            f"{order_key}-"
            "family-data-"
            f"{pdf_sha256}"
        )

        try:
            stored_pdf = (
                self._storage.put(
                    BytesIO(pdf_payload),
                    object_id=object_id,
                    content_type="application/pdf",
                    hash_algorithm="sha256",
                    expected_size_bytes=(len(pdf_payload)),
                    expected_hash=pdf_sha256,
                )
            )

        except AppError:
            raise

        except Exception as exc:
            raise AppIntegrityError(
                "Unable to persist the Family Audit data PDF.",
                component=_COMPONENT,
                operation=operation,
                field="storage",
                context={
                    "order_id": target_order,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        # -------------------------------------------------------------
        # Defensive integrity verification.
        # -------------------------------------------------------------

        if (
            stored_pdf.hash_algorithm
            != "sha256"
        ):
            raise AppIntegrityError(
                "Stored Family Audit data PDF used an unexpected hash algorithm.",
                component=_COMPONENT,
                operation=operation,
                field="stored_pdf.hash_algorithm",
                context={
                    "expected": "sha256",
                    "received": stored_pdf.hash_algorithm,
                },
            )

        if (
            stored_pdf.content_hash
            != pdf_sha256
        ):
            raise AppIntegrityError(
                "Stored Family Audit data PDF hash does not match the rendered artifact.",
                component=_COMPONENT,
                operation=operation,
                field="stored_pdf.content_hash",
            )

        if (
            stored_pdf.size_bytes
            != len(pdf_payload)
        ):
            raise AppIntegrityError(
                "Stored Family Audit data PDF size does not match the rendered artifact.",
                component=_COMPONENT,
                operation=operation,
                field="stored_pdf.size_bytes",
                context={
                    "expected_size_bytes": len(pdf_payload),
                    "actual_size_bytes": stored_pdf.size_bytes,
                },
            )

        artifact = AuditArtifactRef(
            kind="family_data_pdf",
            object_id=stored_pdf.object_id,
            filename=filename,
            content_type="application/pdf",
            size_bytes=stored_pdf.size_bytes,
            sha256=stored_pdf.content_hash,
        )

        logger.info(
            {
                "event": "audit_family_data_artifact_created",
                "order_id": target_order,
                "source_ref": source.source.source_ref,
                "source_format": source.source_format.value,
                "filename": artifact.filename,
                "object_id": artifact.object_id,
                "size_bytes": artifact.size_bytes,
                "has_preview": preview_png is not None,
            }
        )

        return artifact

    def prepare(
        self,
        order_id: str,
        product_code: ProductCode | str,
        sources: tuple[AuditSourceRef, ...],
        *,
        viewer_model: AuditViewerModelRef | None = None,
        organization_rules: tuple[EvidenceContract, ...] = (),
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

        if isinstance(sources, (str, bytes, bytearray, Mapping)):
            raise UnsupportedAppInputError(
                "sources must be a sequence of AuditSourceRef values.",
                component=_COMPONENT,
                operation="prepare",
                field="sources",
            )
        for index, source in enumerate(sources):
            if not isinstance(source, AuditSourceRef):
                raise UnsupportedAppInputError(
                    "sources contains an unsupported value.",
                    component=_COMPONENT,
                    operation="prepare",
                    field=f"sources[{index}]",
                    context={"received_type": type(source).__name__},
                )

        if (
            viewer_model is not None
            and not isinstance(
                viewer_model,
                AuditViewerModelRef,
            )
        ):
            raise UnsupportedAppInputError(
                "viewer_model must be an AuditViewerModelRef or None.",
                component=_COMPONENT,
                operation="prepare",
                field="viewer_model",
                context={
                    "received_type":
                        type(
                            viewer_model
                        ).__name__,
                },
            )

        try:
            product = ProductCode.parse(product_code)
        except DomainError as exc:
            raise AppValidationError(
                "Audit product code is invalid.",
                component=_COMPONENT,
                operation="prepare",
                field="product_code",
                cause=exc,
            ) from exc

        extracted = tuple(self._extract_source(source) for source in sources)
        family_sources, project_source = self._classify_sources(product, extracted)
        artifacts: list[AuditArtifactRef] = []

        viewer_source = (
            project_source
            if project_source
            is not None
            else (
                family_sources[0]
                if len(
                    family_sources
                )
                == 1
                else None
            )
        )

        if viewer_model is not None:
            if viewer_source is None:
                raise AppIntegrityError(
                    "Audit product has no authoritative source for the supplied viewer model.",
                    component=_COMPONENT,
                    operation="prepare",
                    field="viewer_model",
                )

            artifacts.append(
                self._uploaded_viewer_artifact(
                    target_order,
                    viewer_model,
                    viewer_source,
                )
            )

        elif viewer_source is not None:
            generated_viewer = (
                self._viewer_artifact(
                    target_order,
                    viewer_source,
                )
            )

            if (
                generated_viewer
                is not None
            ):
                artifacts.append(
                    generated_viewer
                )

        if (
            product
            is ProductCode.FAMILY_AUDIT
            and len(family_sources) == 1
        ):
            family_source = (family_sources[0])

            artifacts.append(self._family_data_artifact(target_order, family_source))

        if isinstance(organization_rules, (str, bytes, bytearray, Mapping)):
            raise UnsupportedAppInputError(
                "organization_rules must be a sequence of EvidenceContract values.",
                component=_COMPONENT,
                operation="prepare",
                field="organization_rules",
            )
        for index, item in enumerate(organization_rules):
            if not isinstance(item, EvidenceContract):
                raise UnsupportedAppInputError(
                    "organization_rules contains an unsupported value.",
                    component=_COMPONENT,
                    operation="prepare",
                    field=f"organization_rules[{index}]",
                    context={"received_type": type(item).__name__},
                )

        family_payload = (
            self._family_contract(
                family_sources,
                organization_rules=tuple(organization_rules),
            )
            if family_sources
            else None
        )
        project_payload = (
            self._project_contract(project_source)
            if project_source is not None
            else None
        )

        evidence_refs = tuple(
            [
                *(family_payload.evidence_ids() if family_payload is not None else ()),
                *(project_payload.evidence_ids() if project_payload is not None else ()),
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
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "order_id": target_order,
            "product_code": product.value,
            "family_payload": (
                family_payload.to_dict()
                if family_payload
                is not None
                else None
            ),

            "project_payload": (
                project_payload.to_dict()
                if project_payload
                is not None
                else None
            ),

            "evidence_refs": list(evidence_refs),
            "artifacts": [
                artifact.to_dict()
                for artifact
                in artifacts
            ],
        }
        payload = canonical_app_json(document).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        manifest_ref = (
            "audit-input-"
            + hashlib.sha256(target_order.encode("utf-8")).hexdigest()[:16]
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
                "event": "audit_input_prepared",
                "order_id": target_order,
                "product_code": product.value,
                "source_count": len(sources),
                "evidence_count": len(evidence_refs),
                "manifest_ref": manifest_ref,
            }
        )
        return PreparedAuditInput(
            order_id=target_order,
            product_code=product,
            manifest_ref=manifest_ref,
            evidence_refs=evidence_refs,
            artifacts=tuple(artifacts),
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

        with closing(self._storage.open(target_ref)) as stream:
            payload = stream.read()

        document = decode_app_json_object(payload, field="audit_input_manifest")
        schema_version = require_app_text(
            document.get("schema_version"),
            field="schema_version",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="resolve",
        )
        if schema_version not in {"1.1.0", _MANIFEST_SCHEMA_VERSION}:
            raise AppIntegrityError(
                "Audit input manifest schema version is unsupported.",
                component=_COMPONENT,
                operation="resolve",
                field="schema_version",
                context={"received": schema_version},
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
            product = ProductCode.parse(document.get("product_code"))
            expected_product = ProductCode.parse(expected_product_code)
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

        raw_family = document.get("family_payload")
        raw_project = document.get("project_payload")
        family_payload = (
            None if raw_family is None else FamilyEvidence.from_dict(raw_family)
        )
        project_payload = (
            None if raw_project is None else ProjectEvidence.from_dict(raw_project)
        )
        raw_artifacts = document.get("artifacts") or []

        if (
            not isinstance(raw_artifacts, list)
        ):
            raise AppIntegrityError(
                "Audit input artifacts must "
                "be an array.",
                component=_COMPONENT,
                operation="resolve",
                field="artifacts",
            )

        artifacts = tuple(AuditArtifactRef.from_dict(item)
            for item in raw_artifacts
            if isinstance(item, Mapping))

        return ResolvedAuditInput(
            order_id=order_id,
            product_code=product,
            family_payload=family_payload,
            project_payload=project_payload,
            artifacts=artifacts,
        )

    def _supports_glb(self, source_format: ModelSourceFormat) -> bool:
        for capability in (
            self
            ._model_converter
            .capabilities
        ):
            if (
                capability.source_format
                is source_format
                and ModelTargetFormat.GLB
                in capability.target_formats
            ):
                return True

        return False


    def _viewer_artifact(self, order_id: str, source: _ExtractedSource) -> AuditArtifactRef | None:
        """
        Optional server-side viewer fallback.

        A locally uploaded verified GLB takes precedence. This method is reached
        only when no local viewer companion was supplied.
        """

        try:
            model_source_format = (ModelSourceFormat.parse(source.source_format.value))

        except Exception:
            return None

        if not self._supports_glb(model_source_format):
            logger.warning(
                {
                    "event": "audit_viewer_artifact_unavailable",
                    "order_id": order_id,
                    "source_format": source.source_format.value,
                    "reason": "glb_converter_not_configured",
                }
            )

            return None

        with closing(
            self._storage.open(
                source
                .source
                .source_ref
            )
        ) as stream:
            converted = (
                self
                ._model_converter
                .convert(
                    stream,
                    source_format=(
                        model_source_format
                    ),
                    target_format=(
                        ModelTargetFormat.GLB
                    ),
                    output_stem=(
                        f"{PurePath(source.source.filename).stem}"
                        "-viewer"
                    ),
                )
            )

        try:
            order_key = (
                hashlib
                .sha256(
                    order_id
                    .encode(
                        "utf-8"
                    )
                )
                .hexdigest()[
                    :16
                ]
            )

            object_id = (
                "audit-artifact-"
                f"{order_key}-"
                "viewer-"
                f"{converted.content_hash}"
            )

            stored = (
                self
                ._storage
                .put(
                    converted.stream,
                    object_id=object_id,
                    content_type=(
                        converted
                        .content_type
                    ),
                    hash_algorithm=(
                        converted
                        .hash_algorithm
                    ),
                    expected_size_bytes=(
                        converted
                        .size_bytes
                    ),
                    expected_hash=(
                        converted
                        .content_hash
                    ),
                )
            )

            return AuditArtifactRef(
                kind="viewer_model",
                object_id=(
                    stored.object_id
                ),
                filename=(
                    converted.filename
                ),
                content_type=(
                    converted
                    .content_type
                ),
                size_bytes=(
                    stored.size_bytes
                ),
                sha256=(
                    stored.content_hash
                ),
            )

        finally:
            converted.close()


__all__ = [
    "AuditSourceRef",
    "AuditViewerModelRef",
    "AuditArtifactRef",
    "PreparedAuditInput",
    "ResolvedAuditInput",
    "AuditInputService",
]
