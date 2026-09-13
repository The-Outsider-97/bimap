"""Provider-neutral structured model-data extraction contracts for BIMAP."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, BinaryIO

from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Data Extraction Port")
printer = PrettyPrinter()

_COMPONENT = "data_extraction"


class ExtractionSourceFormat(str, Enum):
    IFC = "ifc"
    RVT = "rvt"
    RFA = "rfa"
    DWG = "dwg"
    DXF = "dxf"
    FBX = "fbx"
    OBJ = "obj"
    GLB = "glb"
    STL = "stl"
    PLY = "ply"

    @classmethod
    def parse(cls, value: "ExtractionSourceFormat | str") -> "ExtractionSourceFormat":
        if isinstance(value, cls):
            return value

        normalized = require_app_text(
            value,
            field="source_format",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="parse_source_format",
            max_length=32,
        ).casefold()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise UnsupportedAppInputError(
                "Unsupported data-extraction source format.",
                component=_COMPONENT,
                operation="parse_source_format",
                field="source_format",
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc


class ExtractionDataset(str, Enum):
    """Provider-neutral extraction datasets shared by all model adapters."""

    ELEMENTS = "elements"
    PROPERTIES = "properties"
    QUANTITIES = "quantities"
    MATERIALS = "materials"

    @classmethod
    def parse(cls, value: "ExtractionDataset | str") -> "ExtractionDataset":
        if isinstance(value, cls):
            return value

        normalized = require_app_text(
            value,
            field="dataset",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="parse_dataset",
            max_length=64,
        ).casefold()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise UnsupportedAppInputError(
                "Unsupported data-extraction dataset.",
                component=_COMPONENT,
                operation="parse_dataset",
                field="dataset",
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc


def normalize_datasets(
    values: Sequence[ExtractionDataset | str],
) -> tuple[ExtractionDataset, ...]:
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedAppInputError(
            "datasets must be an iterable of dataset values.",
            component=_COMPONENT,
            operation="normalize_datasets",
            field="datasets",
        )

    normalized: list[ExtractionDataset] = []
    for raw in values:
        dataset = ExtractionDataset.parse(raw)
        if dataset not in normalized:
            normalized.append(dataset)

    if not normalized:
        raise AppValidationError(
            "At least one extraction dataset is required.",
            component=_COMPONENT,
            operation="normalize_datasets",
            field="datasets",
        )

    return tuple(normalized)


def _primitive_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AppIntegrityError(
            f"{field_name} must be a mapping.",
            component=_COMPONENT,
            operation="validate_extracted_data",
            field=field_name,
            context={"received_type": type(value).__name__},
        )

    primitive = to_app_primitive(dict(value), field=field_name)
    if not isinstance(primitive, dict):
        raise AppIntegrityError(
            f"{field_name} did not normalize to a JSON object.",
            component=_COMPONENT,
            operation="validate_extracted_data",
            field=field_name,
        )
    return primitive


def _primitive_mapping_rows(
    values: Any,
    *,
    field_name: str,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise AppIntegrityError(
            f"{field_name} must be an iterable of mappings.",
            component=_COMPONENT,
            operation="validate_extracted_data",
            field=field_name,
            context={"received_type": type(values).__name__},
        )

    try:
        raw_rows = tuple(values)
    except TypeError as exc:
        raise AppIntegrityError(
            f"{field_name} must be iterable.",
            component=_COMPONENT,
            operation="validate_extracted_data",
            field=field_name,
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    rows: list[Mapping[str, Any]] = []
    for index, raw in enumerate(raw_rows):
        primitive = _primitive_mapping(raw, field_name=f"{field_name}[{index}]")
        rows.append(MappingProxyType(dict(primitive)))
    return tuple(rows)


def _non_negative_count_mapping(
    value: Any,
    *,
    field_name: str,
) -> Mapping[str, int]:
    primitive = _primitive_mapping(value, field_name=field_name)
    result: dict[str, int] = {}

    for raw_key, raw_count in primitive.items():
        key = require_app_text(
            raw_key,
            field=f"{field_name}.key",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_extracted_data",
            max_length=256,
        )
        count = require_non_negative_int(
            raw_count,
            field=f"{field_name}.{key}",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_extracted_data",
        )
        result[key] = count

    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class DataExtractionCapability:
    source_format: ExtractionSourceFormat | str
    extensions: tuple[str, ...]
    datasets: tuple[ExtractionDataset | str, ...]
    package_content_type: str = "application/zip"
    package_extension: str = ".zip"
    included_artifacts: tuple[str, ...] = ("pdf", "json")

    def __post_init__(self) -> None:
        source_format = ExtractionSourceFormat.parse(self.source_format)

        extensions: list[str] = []
        for raw in self.extensions:
            value = require_app_text(
                raw,
                field="extension",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_capability",
                max_length=32,
            ).casefold()
            if not value.startswith("."):
                value = f".{value}"
            if value not in extensions:
                extensions.append(value)

        if not extensions:
            raise AppValidationError(
                "A data-extraction capability requires at least one source extension.",
                component=_COMPONENT,
                operation="validate_capability",
                field="extensions",
            )

        datasets = normalize_datasets(tuple(self.datasets))
        package_content_type = require_app_text(
            self.package_content_type,
            field="package_content_type",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_capability",
            max_length=128,
        )
        package_extension = require_app_text(
            self.package_extension,
            field="package_extension",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_capability",
            max_length=16,
        ).casefold()
        if not package_extension.startswith("."):
            package_extension = f".{package_extension}"

        artifacts = tuple(
            require_app_text(
                item,
                field="included_artifact",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_capability",
                max_length=32,
            ).casefold()
            for item in self.included_artifacts
        )
        if artifacts != ("pdf", "json"):
            raise AppValidationError(
                "BIMAP data extraction packages must contain PDF and JSON artifacts.",
                component=_COMPONENT,
                operation="validate_capability",
                field="included_artifacts",
            )

        object.__setattr__(self, "source_format", source_format)
        object.__setattr__(self, "extensions", tuple(extensions))
        object.__setattr__(self, "datasets", datasets)
        object.__setattr__(self, "package_content_type", package_content_type)
        object.__setattr__(self, "package_extension", package_extension)
        object.__setattr__(self, "included_artifacts", artifacts)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_format": self.source_format.value,
            "extensions": self.extensions,
            "datasets": tuple(item.value for item in self.datasets),
            "package_content_type": self.package_content_type,
            "package_extension": self.package_extension,
            "included_artifacts": self.included_artifacts,
        }


@dataclass(frozen=True, slots=True)
class DataSourceInspection:
    source_format: ExtractionSourceFormat | str
    schema: str
    product_count: int
    project_name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_format",
            ExtractionSourceFormat.parse(self.source_format),
        )
        object.__setattr__(
            self,
            "schema",
            require_app_text(
                self.schema,
                field="schema",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_inspection",
                max_length=128,
            ),
        )
        object.__setattr__(
            self,
            "product_count",
            require_non_negative_int(
                self.product_count,
                field="product_count",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_inspection",
            ),
        )
        object.__setattr__(
            self,
            "project_name",
            optional_app_text(
                self.project_name,
                field="project_name",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_inspection",
                max_length=512,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_format": self.source_format.value,
            "schema": self.schema,
            "product_count": self.product_count,
            "project_name": self.project_name,
        }


@dataclass(frozen=True, slots=True)
class ExtractedModelData:
    """
    Canonical provider-neutral extraction result.

    ``extensions`` is the only source-specific escape hatch.  It preserves
    deterministic adapter data that does not belong in the four cross-format
    datasets without polluting ``ExtractionDataset`` with Revit-only concepts.
    For Revit Family Audit the native worker stores its canonical family payload
    beneath ``extensions["revit_family"]``.
    """

    inspection: DataSourceInspection
    project: Mapping[str, Any]
    units: tuple[Mapping[str, Any], ...]
    datasets: Mapping[str, tuple[Mapping[str, Any], ...]]
    counts: Mapping[str, int]
    ifc_class_counts: Mapping[str, int]
    geometry_summary: Mapping[str, Any] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.inspection, DataSourceInspection):
            raise AppIntegrityError(
                "inspection must be DataSourceInspection.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="inspection",
                context={"received_type": type(self.inspection).__name__},
            )

        project = MappingProxyType(
            _primitive_mapping(self.project, field_name="project")
        )
        units = _primitive_mapping_rows(self.units, field_name="units")

        if not isinstance(self.datasets, Mapping):
            raise AppIntegrityError(
                "datasets must be a mapping.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="datasets",
                context={"received_type": type(self.datasets).__name__},
            )

        normalized_datasets: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for raw_name, raw_rows in self.datasets.items():
            name = require_app_text(
                raw_name,
                field="datasets.name",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_extracted_data",
                max_length=128,
            ).casefold()
            if name in normalized_datasets:
                raise AppIntegrityError(
                    "datasets contains duplicate normalized names.",
                    component=_COMPONENT,
                    operation="validate_extracted_data",
                    field="datasets",
                    context={"dataset": name},
                )
            normalized_datasets[name] = _primitive_mapping_rows(
                raw_rows,
                field_name=f"datasets.{name}",
            )

        counts = _non_negative_count_mapping(self.counts, field_name="counts")
        class_counts = _non_negative_count_mapping(
            self.ifc_class_counts,
            field_name="ifc_class_counts",
        )
        geometry_summary = MappingProxyType(
            _primitive_mapping(
                self.geometry_summary,
                field_name="geometry_summary",
            )
        )
        extensions = MappingProxyType(
            _primitive_mapping(self.extensions, field_name="extensions")
        )

        object.__setattr__(self, "project", project)
        object.__setattr__(self, "units", units)
        object.__setattr__(
            self,
            "datasets",
            MappingProxyType(normalized_datasets),
        )
        object.__setattr__(self, "counts", counts)
        object.__setattr__(self, "ifc_class_counts", class_counts)
        object.__setattr__(self, "geometry_summary", geometry_summary)
        object.__setattr__(self, "extensions", extensions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspection": self.inspection.to_dict(),
            "project": dict(self.project),
            "units": [dict(item) for item in self.units],
            "counts": dict(self.counts),
            "ifc_class_counts": dict(self.ifc_class_counts),
            "datasets": {
                key: [dict(row) for row in rows]
                for key, rows in self.datasets.items()
            },
            "geometry_summary": dict(self.geometry_summary),
            "extensions": to_app_primitive(
                dict(self.extensions),
                field="extensions",
            ),
        }


@dataclass(frozen=True, slots=True)
class DataExtractionPackage:
    stream: BinaryIO
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    json_filename: str
    pdf_filename: str
    json_size_bytes: int
    pdf_size_bytes: int
    hash_algorithm: str = "sha256"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stream",
            require_binary_stream(
                self.stream,
                field="stream",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_package",
            ),
        )

        for field_name in ("filename", "json_filename", "pdf_filename"):
            value = require_app_text(
                getattr(self, field_name),
                field=field_name,
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_package",
                max_length=255,
            )
            if (
                "/" in value
                or "\\" in value
                or value in {".", ".."}
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise AppValidationError(
                    "Extraction artifact filename is not a safe basename.",
                    component=_COMPONENT,
                    operation="validate_package",
                    field=field_name,
                )
            object.__setattr__(self, field_name, value)

        object.__setattr__(
            self,
            "content_type",
            require_app_text(
                self.content_type,
                field="content_type",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_package",
                max_length=128,
            ),
        )

        for field_name in ("size_bytes", "json_size_bytes", "pdf_size_bytes"):
            value = require_non_negative_int(
                getattr(self, field_name),
                field=field_name,
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_package",
            )
            if value == 0:
                raise AppValidationError(
                    "Extraction package artifacts cannot be empty.",
                    component=_COMPONENT,
                    operation="validate_package",
                    field=field_name,
                )
            object.__setattr__(self, field_name, value)

        algorithm = require_app_text(
            self.hash_algorithm,
            field="hash_algorithm",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_package",
            max_length=32,
        ).casefold()
        digest = require_app_text(
            self.content_hash,
            field="content_hash",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_package",
            max_length=256,
        ).casefold()

        if algorithm == "sha256" and (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise AppValidationError(
                "SHA-256 package hash must be a 64-character hexadecimal digest.",
                component=_COMPONENT,
                operation="validate_package",
                field="content_hash",
            )

        object.__setattr__(self, "hash_algorithm", algorithm)
        object.__setattr__(self, "content_hash", digest)

    def read_bytes(self) -> bytes:
        self.stream.seek(0)
        payload = self.stream.read()
        self.stream.seek(0)
        return bytes(payload)

    def close(self) -> None:
        self.stream.close()


class DataExtractor(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        raise NotImplementedError

    @abstractmethod
    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        raise NotImplementedError

    @abstractmethod
    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        raise NotImplementedError

    def render_preview(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> bytes | None:
        """Return a best-effort PNG preview or ``None`` when unavailable."""
        del stream, source_format
        return None


class DataExtractionPDFRenderer(ABC):
    @abstractmethod
    def render(
        self,
        *,
        document: Mapping[str, Any],
        preview_png: bytes | None = None,
    ) -> bytes:
        raise NotImplementedError


__all__ = [
    "ExtractionSourceFormat",
    "ExtractionDataset",
    "normalize_datasets",
    "DataExtractionCapability",
    "DataSourceInspection",
    "ExtractedModelData",
    "DataExtractionPackage",
    "DataExtractor",
    "DataExtractionPDFRenderer",
]
