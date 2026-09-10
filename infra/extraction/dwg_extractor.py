"""
DXF and deployment-assisted DWG data extraction for BIMAP.

``ezdxf`` is used for DXF parsing.  DWG is not parsed by pretending it is DXF;
a deployment must provide a real ``DwgToDxfBackend``.  When no such backend is
configured, the adapter advertises DXF only and BIMAP must not present DWG as a
supported upload format.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable

import ezdxf  # type: ignore
from ezdxf import bbox  # type: ignore

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP DWG/DXF Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "dwg_dxf_data_extractor"
_COPY_CHUNK_BYTES = 1024 * 1024
_SUPPORTED_DATASETS = (
    ExtractionDataset.ELEMENTS,
    ExtractionDataset.PROPERTIES,
    ExtractionDataset.QUANTITIES,
    ExtractionDataset.MATERIALS,
)


@runtime_checkable
class DwgToDxfBackend(Protocol):
    """Deployment-owned DWG -> DXF conversion dependency."""

    def convert_to_dxf(
        self,
        source_path: Path,
        destination_path: Path,
    ) -> None:
        ...


def _safe_attr(entity: Any, name: str) -> Any:
    namespace = getattr(entity, "dxf", None)
    if namespace is None:
        return None

    try:
        if not namespace.hasattr(name):
            return None
        value = getattr(namespace, name)
    except Exception:
        return None

    if value is None or isinstance(
        value,
        (bool, int, float, str),
    ):
        return value

    if hasattr(value, "x") and hasattr(value, "y"):
        result = [
            float(value.x),
            float(value.y),
        ]
        if hasattr(value, "z"):
            result.append(float(value.z))
        return result

    return str(value)


def _entity_id(entity: Any) -> str | None:
    value = _safe_attr(entity, "handle")
    return None if value is None else str(value)


def _entity_type(entity: Any) -> str:
    try:
        value = entity.dxftype()
    except Exception:
        return type(entity).__name__
    return str(value or type(entity).__name__)


def _bounds(entity: Any) -> dict[str, Any] | None:
    try:
        box = bbox.extents([entity], fast=True)
    except Exception:
        return None

    if not getattr(box, "has_data", False):
        return None

    try:
        minimum = [
            float(box.extmin.x),
            float(box.extmin.y),
            float(box.extmin.z),
        ]
        maximum = [
            float(box.extmax.x),
            float(box.extmax.y),
            float(box.extmax.z),
        ]
        size = [
            maximum[index] - minimum[index]
            for index in range(3)
        ]
    except Exception:
        return None

    return {
        "minimum": minimum,
        "maximum": maximum,
        "size": size,
    }


def _entity_properties(entity: Any) -> dict[str, Any]:
    namespace = getattr(entity, "dxf", None)
    if namespace is None:
        return {}

    try:
        values = namespace.all_existing_dxf_attribs()
    except Exception:
        return {}

    normalized: dict[str, Any] = {}
    for key, value in sorted(
        values.items(),
        key=lambda item: str(item[0]),
    ):
        if value is None or isinstance(
            value,
            (bool, int, float, str),
        ):
            normalized[str(key)] = value
        elif hasattr(value, "x") and hasattr(value, "y"):
            point = [
                float(value.x),
                float(value.y),
            ]
            if hasattr(value, "z"):
                point.append(float(value.z))
            normalized[str(key)] = point
        else:
            normalized[str(key)] = str(value)
    return normalized


class DwgDxfDataExtractor(DataExtractor):
    """
    Extract DXF directly and DWG through an explicitly configured converter.

    DWG is advertised only when ``dwg_backend`` is present.
    """

    __slots__ = (
        "_dwg_backend",
        "_capabilities",
    )

    def __init__(
        self,
        *,
        dwg_backend: DwgToDxfBackend | None = None,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing DWG/DXF data extractor",
            event="dwg_dxf_data_extractor_init_start",
            context={"dwg_backend_configured": dwg_backend is not None},
        )

        if dwg_backend is not None:
            convert = getattr(
                dwg_backend,
                "convert_to_dxf",
                None,
            )
            if not callable(convert):
                raise AppConfigurationError(
                    "dwg_backend must provide convert_to_dxf(source_path, destination_path).",
                    component=_COMPONENT,
                    operation="initialize",
                    field="dwg_backend",
                    context={
                        "received_type": type(dwg_backend).__name__,
                    },
                )

        capabilities = [
            DataExtractionCapability(
                source_format=ExtractionSourceFormat.parse("DXF"),
                extensions=(".dxf",),
                datasets=_SUPPORTED_DATASETS,
            )
        ]
        if dwg_backend is not None:
            capabilities.append(
                DataExtractionCapability(
                    source_format=ExtractionSourceFormat.parse("DWG"),
                    extensions=(".dwg",),
                    datasets=_SUPPORTED_DATASETS,
                )
            )

        self._dwg_backend = dwg_backend
        self._capabilities = tuple(capabilities)

        logger.info(
            {
                "event": "dwg_dxf_data_extractor_initialized",
                "dwg_enabled": dwg_backend is not None,
            }
        )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._capabilities

    @staticmethod
    def _materialize(
        stream: BinaryIO,
        destination: Path,
    ) -> None:
        source = require_binary_stream(
            stream,
            field="source",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="materialize_source",
        )

        try:
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise UnsupportedAppInputError(
                "CAD source stream must be seekable.",
                component=_COMPONENT,
                operation="materialize_source",
                field="source",
                cause=exc,
            ) from exc

        with destination.open("wb") as target:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(
                    chunk,
                    (bytes, bytearray, memoryview),
                ):
                    raise UnsupportedAppInputError(
                        "CAD source stream yielded non-binary data.",
                        component=_COMPONENT,
                        operation="materialize_source",
                        field="source",
                    )
                target.write(bytes(chunk))

        if destination.stat().st_size <= 0:
            raise AppValidationError(
                "CAD source model cannot be empty.",
                component=_COMPONENT,
                operation="materialize_source",
                field="source",
            )

    def _require_source(
        self,
        source_format: ExtractionSourceFormat | str,
        *,
        operation: str,
    ) -> ExtractionSourceFormat:
        source = ExtractionSourceFormat.parse(
            source_format
        )

        if source is ExtractionSourceFormat.parse("DXF"):
            return source

        if source is ExtractionSourceFormat.parse("DWG"):
            if self._dwg_backend is None:
                raise UnsupportedAppInputError(
                    "DWG extraction is not configured on this deployment.",
                    component=_COMPONENT,
                    operation=operation,
                    field="source_format",
                )
            return source

        raise UnsupportedAppInputError(
            "DWG/DXF extractor accepts DWG or DXF sources only.",
            component=_COMPONENT,
            operation=operation,
            field="source_format",
            context={"source_format": source.value},
        )

    def _prepare_dxf(
        self,
        *,
        source: ExtractionSourceFormat,
        source_path: Path,
        directory: Path,
    ) -> Path:
        if source is ExtractionSourceFormat.parse("DXF"):
            return source_path

        if self._dwg_backend is None:
            raise AppIntegrityError(
                "DWG backend unexpectedly missing after capability validation.",
                component=_COMPONENT,
                operation="prepare_dxf",
                field="dwg_backend",
            )

        dxf_path = directory / "converted.dxf"
        try:
            self._dwg_backend.convert_to_dxf(
                source_path,
                dxf_path,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Configured DWG-to-DXF backend failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="convert_dwg",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if (
            not dxf_path.is_file()
            or dxf_path.stat().st_size <= 0
        ):
            raise AppIntegrityError(
                "DWG-to-DXF backend did not produce a non-empty DXF file.",
                component=_COMPONENT,
                operation="convert_dwg",
                field="artifact",
            )
        return dxf_path

    @staticmethod
    def _open_document(path: Path) -> Any:
        try:
            # ``readfile`` is available at runtime but is omitted from ezdxf's
            # public type exports.
            return getattr(ezdxf, "readfile")(str(path))
        except Exception as exc:
            raise AppValidationError(
                "The uploaded CAD source could not be parsed as DXF.",
                component=_COMPONENT,
                operation="open_dxf",
                field="source",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

    @staticmethod
    def _inspection(
        document: Any,
        *,
        source: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        try:
            entities = tuple(document.modelspace())
        except Exception as exc:
            raise AppIntegrityError(
                "DXF document did not expose a readable modelspace.",
                component=_COMPONENT,
                operation="inspect",
                cause=exc,
            ) from exc

        if not entities:
            raise AppValidationError(
                "CAD model contains no modelspace entities to extract.",
                component=_COMPONENT,
                operation="inspect",
                field="source",
            )

        schema = str(
            getattr(document, "dxfversion", "")
            or source.value.upper()
        )
        return DataSourceInspection(
            source_format=source,
            schema=schema,
            product_count=len(entities),
            project_name=None,
        )

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        source = self._require_source(
            source_format,
            operation="inspect",
        )

        with tempfile.TemporaryDirectory(
            prefix="bimap-cad-extract-"
        ) as directory_name:
            directory = Path(directory_name)
            source_path = (
                directory
                / f"source.{source.value}"
            )
            self._materialize(stream, source_path)
            dxf_path = self._prepare_dxf(
                source=source,
                source_path=source_path,
                directory=directory,
            )
            document = self._open_document(dxf_path)
            return self._inspection(
                document,
                source=source,
            )

    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        source = self._require_source(
            source_format,
            operation="extract",
        )
        selected = normalize_datasets(list(datasets))

        unsupported = tuple(
            item.value
            for item in selected
            if item not in _SUPPORTED_DATASETS
        )
        if unsupported:
            raise UnsupportedAppInputError(
                "Requested dataset is unsupported for DWG/DXF extraction.",
                component=_COMPONENT,
                operation="extract",
                field="datasets",
                context={"unsupported": unsupported},
            )

        with tempfile.TemporaryDirectory(
            prefix="bimap-cad-extract-"
        ) as directory_name:
            directory = Path(directory_name)
            source_path = (
                directory
                / f"source.{source.value}"
            )
            self._materialize(stream, source_path)
            dxf_path = self._prepare_dxf(
                source=source,
                source_path=source_path,
                directory=directory,
            )
            document = self._open_document(dxf_path)
            inspection = self._inspection(
                document,
                source=source,
            )
            entities = tuple(document.modelspace())

            class_counts = Counter(
                _entity_type(entity)
                for entity in entities
            )
            extracted: dict[
                str,
                tuple[dict[str, Any], ...],
            ] = {}
            counts: dict[str, int] = {}

            if ExtractionDataset.ELEMENTS in selected:
                rows = tuple(
                    {
                        "source_id": _entity_id(entity),
                        "source_class": _entity_type(entity),
                        "name": (
                            _safe_attr(entity, "name")
                            or _safe_attr(entity, "text")
                        ),
                        "layer": _safe_attr(entity, "layer"),
                        "owner": _safe_attr(entity, "owner"),
                    }
                    for entity in entities
                )
                extracted[
                    ExtractionDataset.ELEMENTS.value
                ] = rows
                counts[
                    ExtractionDataset.ELEMENTS.value
                ] = len(rows)

            if ExtractionDataset.PROPERTIES in selected:
                property_rows: list[
                    dict[str, Any]
                ] = []
                for entity in entities:
                    identity = {
                        "element_id": _entity_id(entity),
                        "source_class": _entity_type(entity),
                    }
                    for name, value in _entity_properties(
                        entity
                    ).items():
                        property_rows.append(
                            {
                                **identity,
                                "set_name": "DXF",
                                "name": name,
                                "value": value,
                            }
                        )
                rows = tuple(property_rows)
                extracted[
                    ExtractionDataset.PROPERTIES.value
                ] = rows
                counts[
                    ExtractionDataset.PROPERTIES.value
                ] = len(rows)

            if ExtractionDataset.QUANTITIES in selected:
                quantity_rows: list[
                    dict[str, Any]
                ] = []
                for entity in entities:
                    bounds = _bounds(entity)
                    if bounds is None:
                        continue
                    quantity_rows.append(
                        {
                            "element_id": _entity_id(entity),
                            "source_class": _entity_type(entity),
                            "set_name": "GeometryBounds",
                            "name": "bounding_box",
                            "value": bounds,
                        }
                    )
                rows = tuple(quantity_rows)
                extracted[
                    ExtractionDataset.QUANTITIES.value
                ] = rows
                counts[
                    ExtractionDataset.QUANTITIES.value
                ] = len(rows)

            if ExtractionDataset.MATERIALS in selected:
                material_rows: list[
                    dict[str, Any]
                ] = []
                for entity in entities:
                    record = {
                        "element_id": _entity_id(entity),
                        "source_class": _entity_type(entity),
                        "material_handle": _safe_attr(
                            entity,
                            "material_handle",
                        ),
                        "color": _safe_attr(
                            entity,
                            "color",
                        ),
                        "true_color": _safe_attr(
                            entity,
                            "true_color",
                        ),
                        "transparency": _safe_attr(
                            entity,
                            "transparency",
                        ),
                    }
                    if any(
                        record[key] is not None
                        for key in (
                            "material_handle",
                            "color",
                            "true_color",
                            "transparency",
                        )
                    ):
                        material_rows.append(record)

                rows = tuple(material_rows)
                extracted[
                    ExtractionDataset.MATERIALS.value
                ] = rows
                counts[
                    ExtractionDataset.MATERIALS.value
                ] = len(rows)

            units_code = getattr(
                document,
                "units",
                None,
            )
            units = (
                (
                    {
                        "unit_type": "DXF_INSUNITS",
                        "code": (
                            int(units_code)
                            if isinstance(
                                units_code,
                                int,
                            )
                            else str(units_code)
                        ),
                    },
                )
                if units_code is not None
                else ()
            )

            logger.info(
                {
                    "event": "dwg_dxf_data_extraction_completed",
                    "source_format": source.value,
                    "schema": inspection.schema,
                    "entity_count": inspection.product_count,
                    "selected_datasets": tuple(
                        item.value
                        for item in selected
                    ),
                    "row_counts": dict(counts),
                }
            )

            return ExtractedModelData(
                inspection=inspection,
                project={},
                units=units,
                datasets=extracted,
                counts=counts,
                # Compatibility field in the current application contract.
                ifc_class_counts=dict(
                    sorted(class_counts.items())
                ),
            )


__all__ = [
    "DwgToDxfBackend",
    "DwgDxfDataExtractor",
]


if __name__ == "__main__":
    print("\n=== Running DWG/DXF Data Extractor Self-Test ===\n")
    printer.status("TEST", "DWG/DXF extractor initialized", "info")
    extractor = DwgDxfDataExtractor()
    assert tuple(
        capability.source_format
        for capability in extractor.capabilities
    ) == (ExtractionSourceFormat.parse("DXF"),)
    printer.status("PASS", "DXF-only fail-closed capability", "success")
    print("\n=== Test ran successfully ===\n")
