"""Provider-neutral structured model-data extraction contracts for BIMAP."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
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
    def parse(
        cls,
        value: "ExtractionSourceFormat | str",
    ) -> "ExtractionSourceFormat":
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
                    "allowed": tuple(
                        item.value
                        for item in cls
                    ),
                },
                cause=exc,
            ) from exc


class ExtractionDataset(str, Enum):
    ELEMENTS = "elements"
    PROPERTIES = "properties"
    QUANTITIES = "quantities"
    MATERIALS = "materials"

    @classmethod
    def parse(
        cls,
        value: "ExtractionDataset | str",
    ) -> "ExtractionDataset":
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
                    "allowed": tuple(
                        item.value
                        for item in cls
                    ),
                },
                cause=exc,
            ) from exc


def normalize_datasets(
    values: (
        list[ExtractionDataset | str]
        | tuple[ExtractionDataset | str, ...]
    ),
) -> tuple[ExtractionDataset, ...]:
    if isinstance(
        values,
        (str, bytes, bytearray),
    ):
        raise UnsupportedAppInputError(
            "datasets must be an iterable of dataset values.",
            component=_COMPONENT,
            operation="normalize_datasets",
            field="datasets",
        )

    normalized: list[ExtractionDataset] = []

    for raw in values:
        dataset = ExtractionDataset.parse(
            raw
        )

        if dataset not in normalized:
            normalized.append(
                dataset
            )

    if not normalized:
        raise AppValidationError(
            "At least one extraction dataset is required.",
            component=_COMPONENT,
            operation="normalize_datasets",
            field="datasets",
        )

    return tuple(
        normalized
    )


@dataclass(frozen=True, slots=True)
class DataExtractionCapability:
    source_format: ExtractionSourceFormat | str
    extensions: tuple[str, ...]
    datasets: tuple[ExtractionDataset | str, ...]
    package_content_type: str = "application/zip"
    package_extension: str = ".zip"
    included_artifacts: tuple[str, ...] = (
        "pdf",
        "json",
    )

    def __post_init__(self) -> None:
        source_format = (
            ExtractionSourceFormat.parse(
                self.source_format
            )
        )

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
                extensions.append(
                    value
                )

        if not extensions:
            raise AppValidationError(
                "A data-extraction capability requires "
                "at least one source extension.",
                component=_COMPONENT,
                operation="validate_capability",
                field="extensions",
            )

        datasets = normalize_datasets(
            list(self.datasets)
        )

        package_content_type = (
            require_app_text(
                self.package_content_type,
                field="package_content_type",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_capability",
                max_length=128,
            )
        )

        package_extension = (
            require_app_text(
                self.package_extension,
                field="package_extension",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_capability",
                max_length=16,
            ).casefold()
        )

        if not package_extension.startswith(
            "."
        ):
            package_extension = (
                f".{package_extension}"
            )

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

        if artifacts != (
            "pdf",
            "json",
        ):
            raise AppValidationError(
                "BIMAP data extraction packages "
                "must contain PDF and JSON artifacts.",
                component=_COMPONENT,
                operation="validate_capability",
                field="included_artifacts",
            )

        object.__setattr__(
            self,
            "source_format",
            source_format,
        )

        object.__setattr__(
            self,
            "extensions",
            tuple(extensions),
        )

        object.__setattr__(
            self,
            "datasets",
            datasets,
        )

        object.__setattr__(
            self,
            "package_content_type",
            package_content_type,
        )

        object.__setattr__(
            self,
            "package_extension",
            package_extension,
        )

        object.__setattr__(
            self,
            "included_artifacts",
            artifacts,
        )

    def to_dict(
        self,
    ) -> dict[str, object]:
        return {
            "source_format":
                self.source_format.value,
            "extensions":
                self.extensions,
            "datasets":
                tuple(
                    item.value
                    for item in self.datasets
                ),
            "package_content_type":
                self.package_content_type,
            "package_extension":
                self.package_extension,
            "included_artifacts":
                self.included_artifacts,
        }


@dataclass(frozen=True, slots=True)
class DataSourceInspection:
    source_format: ExtractionSourceFormat | str
    schema: str
    product_count: int
    project_name: str | None = None

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "source_format",
            ExtractionSourceFormat.parse(
                self.source_format
            ),
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

    def to_dict(
        self,
    ) -> dict[str, object]:
        return {
            "source_format":
                self.source_format.value,
            "schema":
                self.schema,
            "product_count":
                self.product_count,
            "project_name":
                self.project_name,
        }


@dataclass(frozen=True, slots=True)
class ExtractedModelData:
    inspection: DataSourceInspection
    project: Mapping[str, Any]
    units: tuple[Mapping[str, Any], ...]
    datasets: Mapping[
        str,
        tuple[Mapping[str, Any], ...],
    ]
    counts: Mapping[str, int]
    ifc_class_counts: Mapping[str, int]

    def __post_init__(
        self,
    ) -> None:
        if not isinstance(
            self.inspection,
            DataSourceInspection,
        ):
            raise AppIntegrityError(
                "inspection must be DataSourceInspection.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="inspection",
            )

        project = to_app_primitive(
            dict(self.project),
            field="project",
        )

        units = to_app_primitive(
            list(self.units),
            field="units",
        )

        datasets = to_app_primitive(
            {
                key: list(value)
                for key, value
                in self.datasets.items()
            },
            field="datasets",
        )

        counts = to_app_primitive(
            dict(self.counts),
            field="counts",
        )

        class_counts = to_app_primitive(
            dict(
                self.ifc_class_counts
            ),
            field="ifc_class_counts",
        )

        if not isinstance(
            project,
            dict,
        ):
            raise AppIntegrityError(
                "project did not normalize "
                "to a JSON object.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="project",
            )

        if not isinstance(
            units,
            list,
        ):
            raise AppIntegrityError(
                "units did not normalize "
                "to a JSON array.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="units",
            )

        if not isinstance(
            datasets,
            dict,
        ):
            raise AppIntegrityError(
                "datasets did not normalize "
                "to a JSON object.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="datasets",
            )

        normalized_datasets: dict[
            str,
            tuple[
                Mapping[str, Any],
                ...,
            ],
        ] = {}

        for key, rows in datasets.items():
            if not isinstance(
                rows,
                list,
            ):
                raise AppIntegrityError(
                    "Extraction dataset must "
                    "be a JSON array.",
                    component=_COMPONENT,
                    operation="validate_extracted_data",
                    field=f"datasets.{key}",
                )

            normalized_rows: list[
                Mapping[str, Any]
            ] = []

            for row in rows:
                if not isinstance(
                    row,
                    dict,
                ):
                    raise AppIntegrityError(
                        "Extraction dataset rows "
                        "must be JSON objects.",
                        component=_COMPONENT,
                        operation="validate_extracted_data",
                        field=f"datasets.{key}",
                    )

                normalized_rows.append(
                    MappingProxyType(
                        dict(row)
                    )
                )

            normalized_datasets[
                str(key)
            ] = tuple(
                normalized_rows
            )

        normalized_counts: dict[
            str,
            int,
        ] = {}

        if not isinstance(
            counts,
            dict,
        ):
            raise AppIntegrityError(
                "counts did not normalize "
                "to a JSON object.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="counts",
            )

        for key, value in counts.items():
            normalized_counts[
                str(key)
            ] = require_non_negative_int(
                value,
                field=f"counts.{key}",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_extracted_data",
            )

        normalized_class_counts: dict[
            str,
            int,
        ] = {}

        if not isinstance(
            class_counts,
            dict,
        ):
            raise AppIntegrityError(
                "ifc_class_counts did not "
                "normalize to a JSON object.",
                component=_COMPONENT,
                operation="validate_extracted_data",
                field="ifc_class_counts",
            )

        for (
            key,
            value,
        ) in class_counts.items():
            normalized_class_counts[
                str(key)
            ] = require_non_negative_int(
                value,
                field=(
                    f"ifc_class_counts.{key}"
                ),
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_extracted_data",
            )

        object.__setattr__(
            self,
            "project",
            MappingProxyType(
                dict(project)
            ),
        )

        object.__setattr__(
            self,
            "units",
            tuple(
                MappingProxyType(
                    dict(item)
                )
                for item in units
                if isinstance(
                    item,
                    dict,
                )
            ),
        )

        object.__setattr__(
            self,
            "datasets",
            MappingProxyType(
                normalized_datasets
            ),
        )

        object.__setattr__(
            self,
            "counts",
            MappingProxyType(
                normalized_counts
            ),
        )

        object.__setattr__(
            self,
            "ifc_class_counts",
            MappingProxyType(
                normalized_class_counts
            ),
        )

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return {
            "inspection":
                self.inspection.to_dict(),
            "project":
                dict(self.project),
            "units":
                [
                    dict(item)
                    for item
                    in self.units
                ],
            "counts":
                dict(self.counts),
            "ifc_class_counts":
                dict(
                    self.ifc_class_counts
                ),
            "datasets": {
                key: [
                    dict(row)
                    for row in rows
                ]
                for key, rows
                in self.datasets.items()
            },
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

    def __post_init__(
        self,
    ) -> None:
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

        for field_name in (
            "filename",
            "json_filename",
            "pdf_filename",
        ):
            value = require_app_text(
                getattr(
                    self,
                    field_name,
                ),
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
                or any(
                    ord(character) < 32
                    or ord(character) == 127
                    for character in value
                )
            ):
                raise AppValidationError(
                    "Extraction artifact filename "
                    "is not a safe basename.",
                    component=_COMPONENT,
                    operation="validate_package",
                    field=field_name,
                )

            object.__setattr__(
                self,
                field_name,
                value,
            )

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

        for field_name in (
            "size_bytes",
            "json_size_bytes",
            "pdf_size_bytes",
        ):
            value = (
                require_non_negative_int(
                    getattr(
                        self,
                        field_name,
                    ),
                    field=field_name,
                    error_type=AppValidationError,
                    component=_COMPONENT,
                    operation="validate_package",
                )
            )

            if value == 0:
                raise AppValidationError(
                    "Extraction package artifacts "
                    "cannot be empty.",
                    component=_COMPONENT,
                    operation="validate_package",
                    field=field_name,
                )

            object.__setattr__(
                self,
                field_name,
                value,
            )

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

        if (
            algorithm == "sha256"
            and (
                len(digest) != 64
                or any(
                    character
                    not in
                    "0123456789abcdef"
                    for character
                    in digest
                )
            )
        ):
            raise AppValidationError(
                "SHA-256 package hash must be "
                "a 64-character hexadecimal digest.",
                component=_COMPONENT,
                operation="validate_package",
                field="content_hash",
            )

        object.__setattr__(
            self,
            "hash_algorithm",
            algorithm,
        )

        object.__setattr__(
            self,
            "content_hash",
            digest,
        )

    def read_bytes(
        self,
    ) -> bytes:
        self.stream.seek(0)

        payload = (
            self.stream.read()
        )

        self.stream.seek(0)

        return bytes(
            payload
        )

    def close(
        self,
    ) -> None:
        self.stream.close()


class DataExtractor(ABC):
    @property
    @abstractmethod
    def capabilities(
        self,
    ) -> tuple[
        DataExtractionCapability,
        ...,
    ]:
        raise NotImplementedError

    @abstractmethod
    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format:
            ExtractionSourceFormat,
    ) -> DataSourceInspection:
        raise NotImplementedError

    @abstractmethod
    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format:
            ExtractionSourceFormat,
        datasets:
            tuple[
                ExtractionDataset,
                ...,
            ],
    ) -> ExtractedModelData:
        raise NotImplementedError


class DataExtractionPDFRenderer(ABC):
    @abstractmethod
    def render(
        self,
        *,
        document: Mapping[
            str,
            Any,
        ],
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
