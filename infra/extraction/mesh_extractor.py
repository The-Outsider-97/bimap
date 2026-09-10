"""
Trimesh-backed generic 3D geometry extractor for BIMAP.

The adapter is intentionally geometry-oriented.  It does not invent IFC/Revit
semantics for mesh formats.  It extracts only information actually represented
by the source geometry/material scene.
"""

from __future__ import annotations

import math
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO

import trimesh  # type: ignore

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Mesh Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "mesh_data_extractor"
_COPY_CHUNK_BYTES = 1024 * 1024
_SUPPORTED_DATASETS = (
    ExtractionDataset.ELEMENTS,
    ExtractionDataset.PROPERTIES,
    ExtractionDataset.QUANTITIES,
    ExtractionDataset.MATERIALS,
)
_SUPPORTED_FORMATS = (
    ExtractionSourceFormat.parse("OBJ"),
    ExtractionSourceFormat.parse("GLB"),
    ExtractionSourceFormat.parse("STL"),
    ExtractionSourceFormat.parse("PLY"),
)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(
        value,
        (bool, int, str),
    ):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _json_value(tolist())

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_value(item())
        except Exception:
            pass

    return str(value)


def _mesh_records(
    scene: trimesh.Scene,
) -> tuple[tuple[str, Any], ...]:
    return tuple(
        sorted(
            scene.geometry.items(),
            key=lambda item: str(item[0]),
        )
    )


def _mesh_material(
    geometry: Any,
) -> dict[str, Any] | None:
    visual = getattr(geometry, "visual", None)
    material = getattr(visual, "material", None)
    if material is None:
        return None

    record = {
        "material_class": type(material).__name__,
        "name": getattr(material, "name", None),
        "base_color_factor": _json_value(
            getattr(
                material,
                "baseColorFactor",
                None,
            )
        ),
        "metallic_factor": _json_value(
            getattr(
                material,
                "metallicFactor",
                None,
            )
        ),
        "roughness_factor": _json_value(
            getattr(
                material,
                "roughnessFactor",
                None,
            )
        ),
    }
    return {
        key: value
        for key, value in record.items()
        if value is not None
    }


class TrimeshDataExtractor(DataExtractor):
    """Extract generic mesh/scene data from OBJ, GLB, STL and PLY."""

    __slots__ = ("_capabilities", "_by_format")

    def __init__(self) -> None:
        capabilities = tuple(
            DataExtractionCapability(
                source_format=source_format,
                extensions=(f".{source_format.value}",),
                datasets=_SUPPORTED_DATASETS,
            )
            for source_format in _SUPPORTED_FORMATS
        )
        self._capabilities = capabilities
        self._by_format = {
            capability.source_format: capability
            for capability in capabilities
        }

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
                "Mesh source stream must be seekable.",
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
                        "Mesh source stream yielded non-binary data.",
                        component=_COMPONENT,
                        operation="materialize_source",
                        field="source",
                    )
                target.write(bytes(chunk))

        if destination.stat().st_size <= 0:
            raise AppValidationError(
                "Mesh source cannot be empty.",
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
        if source not in self._by_format:
            raise UnsupportedAppInputError(
                "Generic mesh extractor does not support this source format.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={
                    "source_format": source.value,
                    "supported": tuple(
                        item.value
                        for item in _SUPPORTED_FORMATS
                    ),
                },
            )
        return source

    @staticmethod
    def _open_scene(
        path: Path,
        *,
        source: ExtractionSourceFormat,
    ) -> trimesh.Scene:
        try:
            loaded = trimesh.load(
                str(path),
                file_type=source.value,
                force="scene",
                process=False,
            )
        except Exception as exc:
            raise AppValidationError(
                "The uploaded mesh source could not be parsed.",
                component=_COMPONENT,
                operation="open_mesh",
                field="source",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if isinstance(loaded, trimesh.Trimesh):
            scene = trimesh.Scene(loaded)
        elif isinstance(loaded, trimesh.Scene):
            scene = loaded
        else:
            raise AppValidationError(
                "Mesh source did not produce a supported 3D scene.",
                component=_COMPONENT,
                operation="open_mesh",
                field="source",
                context={
                    "received_type": type(loaded).__name__,
                },
            )

        if not scene.geometry:
            raise AppValidationError(
                "Mesh source contains no extractable geometry.",
                component=_COMPONENT,
                operation="open_mesh",
                field="source",
            )
        return scene

    @staticmethod
    def _inspection(
        scene: trimesh.Scene,
        *,
        source: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        geometry = _mesh_records(scene)
        return DataSourceInspection(
            source_format=source,
            # Current application contract requires a non-empty ``schema``.
            # For schema-less mesh formats this is the source-format identifier,
            # not a fabricated schema version.
            schema=source.value.upper(),
            product_count=len(geometry),
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
            prefix="bimap-mesh-extract-"
        ) as directory_name:
            path = (
                Path(directory_name)
                / f"source.{source.value}"
            )
            self._materialize(stream, path)
            scene = self._open_scene(
                path,
                source=source,
            )
            return self._inspection(
                scene,
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
                "Requested dataset is unsupported for generic mesh extraction.",
                component=_COMPONENT,
                operation="extract",
                field="datasets",
                context={"unsupported": unsupported},
            )

        with tempfile.TemporaryDirectory(
            prefix="bimap-mesh-extract-"
        ) as directory_name:
            path = (
                Path(directory_name)
                / f"source.{source.value}"
            )
            self._materialize(stream, path)
            scene = self._open_scene(
                path,
                source=source,
            )
            inspection = self._inspection(
                scene,
                source=source,
            )
            geometry_records = _mesh_records(scene)

            class_counts = Counter(
                type(geometry).__name__
                for _, geometry in geometry_records
            )
            extracted: dict[
                str,
                tuple[dict[str, Any], ...],
            ] = {}
            counts: dict[str, int] = {}

            if ExtractionDataset.ELEMENTS in selected:
                rows = tuple(
                    {
                        "source_id": str(name),
                        "source_class": type(
                            geometry
                        ).__name__,
                        "name": str(name),
                        "vertex_count": int(
                            len(
                                getattr(
                                    geometry,
                                    "vertices",
                                    (),
                                )
                            )
                        ),
                        "face_count": int(
                            len(
                                getattr(
                                    geometry,
                                    "faces",
                                    (),
                                )
                            )
                        ),
                    }
                    for name, geometry in geometry_records
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
                for name, geometry in geometry_records:
                    properties = {
                        "is_watertight": getattr(
                            geometry,
                            "is_watertight",
                            None,
                        ),
                        "is_winding_consistent": getattr(
                            geometry,
                            "is_winding_consistent",
                            None,
                        ),
                        "euler_number": getattr(
                            geometry,
                            "euler_number",
                            None,
                        ),
                    }
                    metadata = getattr(
                        geometry,
                        "metadata",
                        None,
                    )
                    if isinstance(metadata, dict):
                        properties["metadata"] = _json_value(
                            metadata
                        )

                    for property_name, value in properties.items():
                        if value is None:
                            continue
                        property_rows.append(
                            {
                                "element_id": str(name),
                                "source_class": type(
                                    geometry
                                ).__name__,
                                "set_name": "Mesh",
                                "name": property_name,
                                "value": _json_value(value),
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
                for name, geometry in geometry_records:
                    bounds = _json_value(
                        getattr(
                            geometry,
                            "bounds",
                            None,
                        )
                    )
                    extents = _json_value(
                        getattr(
                            geometry,
                            "extents",
                            None,
                        )
                    )
                    area = getattr(
                        geometry,
                        "area",
                        None,
                    )
                    watertight = bool(
                        getattr(
                            geometry,
                            "is_watertight",
                            False,
                        )
                    )

                    candidates = {
                        "bounds": bounds,
                        "extents": extents,
                        "surface_area": (
                            float(area)
                            if isinstance(
                                area,
                                (int, float),
                            )
                            and math.isfinite(
                                float(area)
                            )
                            else None
                        ),
                    }

                    if watertight:
                        volume = getattr(
                            geometry,
                            "volume",
                            None,
                        )
                        if isinstance(
                            volume,
                            (int, float),
                        ) and math.isfinite(
                            float(volume)
                        ):
                            candidates["volume"] = float(
                                volume
                            )

                    for quantity_name, value in candidates.items():
                        if value is None:
                            continue
                        quantity_rows.append(
                            {
                                "element_id": str(name),
                                "source_class": type(
                                    geometry
                                ).__name__,
                                "set_name": "Geometry",
                                "name": quantity_name,
                                "value": _json_value(value),
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
                for name, geometry in geometry_records:
                    material = _mesh_material(
                        geometry
                    )
                    if material is None:
                        continue
                    material_rows.append(
                        {
                            "element_id": str(name),
                            "source_class": type(
                                geometry
                            ).__name__,
                            **material,
                        }
                    )

                rows = tuple(material_rows)
                extracted[
                    ExtractionDataset.MATERIALS.value
                ] = rows
                counts[
                    ExtractionDataset.MATERIALS.value
                ] = len(rows)

            units_value = getattr(
                scene,
                "units",
                None,
            )
            units = (
                (
                    {
                        "unit_type": "scene_units",
                        "name": str(units_value),
                    },
                )
                if units_value
                else ()
            )

            metadata = getattr(
                scene,
                "metadata",
                None,
            )
            project = (
                {
                    "metadata": _json_value(metadata)
                }
                if isinstance(metadata, dict)
                and metadata
                else {}
            )

            logger.info(
                {
                    "event": "mesh_data_extraction_completed",
                    "source_format": source.value,
                    "geometry_count": inspection.product_count,
                    "selected_datasets": tuple(
                        item.value
                        for item in selected
                    ),
                    "row_counts": dict(counts),
                }
            )

            return ExtractedModelData(
                inspection=inspection,
                project=project,
                units=units,
                datasets=extracted,
                counts=counts,
                # Compatibility field in the current application contract.
                ifc_class_counts=dict(
                    sorted(class_counts.items())
                ),
            )


__all__ = ["TrimeshDataExtractor"]


if __name__ == "__main__":
    print("\n=== Running Mesh Data Extractor Self-Test ===\n")
    printer.status("TEST", "Mesh extractor initialized", "info")
    extractor = TrimeshDataExtractor()
    assert {
        capability.source_format
        for capability in extractor.capabilities
    } == set(_SUPPORTED_FORMATS)
    printer.status("PASS", "Mesh capability contract", "success")
    print("\n=== Test ran successfully ===\n")
