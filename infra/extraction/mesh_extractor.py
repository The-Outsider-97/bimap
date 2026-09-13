"""
Trimesh-backed generic 3D geometry extractor for BIMAP.

The adapter is intentionally geometry-oriented.  It does not invent IFC/Revit
semantics for mesh formats.  It extracts only information actually represented
by the source geometry/material scene.
"""

from __future__ import annotations

import math
import tempfile
import numpy as np  # type: ignore
import trimesh  # type: ignore

from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO
from PIL import Image, ImageDraw  # type: ignore

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Mesh Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "mesh_data_extractor"
_COPY_CHUNK_BYTES = 1024 * 1024
_PREVIEW_RESOLUTION = (
    1200,
    700,
)

_PREVIEW_BACKGROUND = (
    250,
    250,
    248,
)

_PREVIEW_FALLBACK_RGB = (
    150,
    156,
    163,
)

# Stable isometric-style viewing direction.
_PREVIEW_VIEW_VECTOR = (
    1.35,
    -1.65,
    1.15,
)

# Directional light used only to make
# the actual mesh topology readable.
_PREVIEW_LIGHT_VECTOR = (
    0.35,
    -0.45,
    0.82,
)
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


def _mesh_material(geometry: Any) -> dict[str, Any] | None:
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

def _unit_vector(
    values: tuple[
        float,
        float,
        float,
    ],
) -> np.ndarray:
    vector = np.asarray(
        values,
        dtype=np.float64,
    )

    length = float(
        np.linalg.norm(
            vector
        )
    )

    if (
        not math.isfinite(length)
        or length <= 0.0
    ):
        raise ValueError(
            "Preview vector must have "
            "a finite non-zero length."
        )

    return vector / length


def _preview_face_colors(
    mesh: trimesh.Trimesh,
    face_count: int,
) -> np.ndarray:
    """
    Return RGB colors for preview faces.

    Existing model face colors are used where
    Trimesh exposes them. Otherwise BIMAP uses
    one neutral presentation color.

    This function never invents model materials.
    """

    fallback = np.tile(
        np.asarray(
            _PREVIEW_FALLBACK_RGB,
            dtype=np.float64,
        ),
        (
            face_count,
            1,
        ),
    )

    visual = getattr(
        mesh,
        "visual",
        None,
    )

    if visual is None:
        return fallback

    try:
        raw = np.asarray(
            visual.face_colors
        )
    except Exception:
        return fallback

    if (
        raw.ndim != 2
        or raw.shape[0]
        != face_count
        or raw.shape[1] < 3
    ):
        return fallback

    rgb = raw[
        :,
        :3,
    ].astype(
        np.float64,
        copy=False,
    )

    if not np.isfinite(
        rgb
    ).all():
        return fallback

    return np.clip(
        rgb,
        0.0,
        255.0,
    )


def _render_scene_preview_png(
    scene: trimesh.Scene,
) -> bytes | None:
    """
    Render a deterministic software preview.

    No OpenGL window, GPU, pyglet, display
    server or browser is required.

    Scene transforms are applied through
    Scene.to_geometry() before projection.
    """

    geometry = (
        scene.to_geometry()
    )

    if not isinstance(
        geometry,
        trimesh.Trimesh,
    ):
        return None

    vertices = np.asarray(
        geometry.vertices,
        dtype=np.float64,
    )

    faces = np.asarray(
        geometry.faces,
        dtype=np.int64,
    )

    if (
        vertices.ndim != 2
        or vertices.shape[1] != 3
        or len(vertices) == 0
        or faces.ndim != 2
        or faces.shape[1] != 3
        or len(faces) == 0
    ):
        return None

    #
    # Do not attempt to hide invalid model
    # geometry by silently cleaning it here.
    #
    if not np.isfinite(
        vertices
    ).all():
        return None

    if (
        faces.min() < 0
        or faces.max()
        >= len(vertices)
    ):
        return None

    bounds_min = (
        vertices.min(
            axis=0
        )
    )

    bounds_max = (
        vertices.max(
            axis=0
        )
    )

    center = (
        bounds_min
        + bounds_max
    ) * 0.5

    centered = (
        vertices
        - center
    )

    #
    # Camera basis.
    #
    view = _unit_vector(
        _PREVIEW_VIEW_VECTOR
    )

    world_up = np.asarray(
        (
            0.0,
            0.0,
            1.0,
        ),
        dtype=np.float64,
    )

    if abs(
        float(
            np.dot(
                view,
                world_up,
            )
        )
    ) > 0.95:
        world_up = np.asarray(
            (
                0.0,
                1.0,
                0.0,
            ),
            dtype=np.float64,
        )

    right = np.cross(
        view,
        world_up,
    )

    right_length = float(
        np.linalg.norm(
            right
        )
    )

    if (
        not math.isfinite(
            right_length
        )
        or right_length <= 0.0
    ):
        return None

    right /= right_length

    up = np.cross(
        right,
        view,
    )

    up_length = float(
        np.linalg.norm(
            up
        )
    )

    if (
        not math.isfinite(
            up_length
        )
        or up_length <= 0.0
    ):
        return None

    up /= up_length

    #
    # Orthographic projection.
    #
    projected_x = (
        centered
        @ right
    )

    projected_y = (
        centered
        @ up
    )

    depth = (
        centered
        @ view
    )

    width, height = (
        _PREVIEW_RESOLUTION
    )

    margin = max(
        24,
        int(
            min(
                width,
                height,
            )
            * 0.06
        ),
    )

    span_x = float(
        projected_x.max()
        - projected_x.min()
    )

    span_y = float(
        projected_y.max()
        - projected_y.min()
    )

    if (
        not math.isfinite(
            span_x
        )
        or not math.isfinite(
            span_y
        )
    ):
        return None

    span_x = max(
        span_x,
        1e-12,
    )

    span_y = max(
        span_y,
        1e-12,
    )

    scale = min(
        (
            width
            - (2 * margin)
        )
        / span_x,
        (
            height
            - (2 * margin)
        )
        / span_y,
    )

    mid_x = float(
        projected_x.min()
        + projected_x.max()
    ) * 0.5

    mid_y = float(
        projected_y.min()
        + projected_y.max()
    ) * 0.5

    pixel_x = (
        (
            projected_x
            - mid_x
        )
        * scale
        + (width * 0.5)
    )

    pixel_y = (
        (height * 0.5)
        - (
            projected_y
            - mid_y
        )
        * scale
    )

    screen = np.column_stack(
        (
            pixel_x,
            pixel_y,
        )
    )

    #
    # Calculate face normals directly from the
    # uploaded geometry.
    #
    triangle_vertices = (
        centered[
            faces
        ]
    )

    normals = np.cross(
        (
            triangle_vertices[
                :,
                1,
            ]
            - triangle_vertices[
                :,
                0,
            ]
        ),
        (
            triangle_vertices[
                :,
                2,
            ]
            - triangle_vertices[
                :,
                0,
            ]
        ),
    )

    normal_lengths = (
        np.linalg.norm(
            normals,
            axis=1,
        )
    )

    valid_normals = (
        normal_lengths
        > 1e-15
    )

    normals[
        valid_normals
    ] /= normal_lengths[
        valid_normals,
        None,
    ]

    normals[
        ~valid_normals
    ] = 0.0

    #
    # Flat directional shading.
    #
    # abs() is intentional because imported STL
    # files can contain inconsistent winding.
    # Winding is not repaired or changed.
    #
    light = _unit_vector(
        _PREVIEW_LIGHT_VECTOR
    )

    intensity = (
        0.38
        + (
            0.62
            * np.abs(
                normals
                @ light
            )
        )
    )

    intensity = np.clip(
        intensity,
        0.25,
        1.0,
    )

    base_colors = (
        _preview_face_colors(
            geometry,
            len(faces),
        )
    )

    shaded = np.clip(
        (
            base_colors
            * intensity[
                :,
                None,
            ]
        ),
        0.0,
        255.0,
    ).astype(
        np.uint8
    )

    #
    # Painter ordering:
    # distant triangles first,
    # nearer triangles last.
    #
    face_depth = (
        depth[
            faces
        ].mean(
            axis=1
        )
    )

    render_order = (
        np.argsort(
            face_depth,
            kind="stable",
        )
    )

    image = Image.new(
        "RGB",
        _PREVIEW_RESOLUTION,
        _PREVIEW_BACKGROUND,
    )

    draw = ImageDraw.Draw(image)

    for face_index in (render_order):
        points = [(float(screen[vertex_index, 0,]), float(screen[vertex_index, 1]))
            for vertex_index
            in faces[face_index]
        ]

        color = tuple(int(value)
            for value
            in shaded[face_index]
        )

        draw.polygon(points, fill=color)

    output = BytesIO()
    image.save(output, format="PNG")
    payload = (output.getvalue())

    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return None

    return payload

def _geometry_summary(scene: trimesh.Scene, geometry_records: tuple[tuple[str, Any], ...]) -> dict[str, Any]:
    total_vertices = 0
    total_polygons = 0
    total_edges = 0
    watertight_geometry_count = 0
    volumes: list[float] = []
    volume_complete = bool(geometry_records)

    for _, geometry in geometry_records:
        vertices = getattr(geometry, "vertices", ())
        faces = getattr(geometry, "faces", ())
        unique_edges = getattr(geometry, "edges_unique", ())
        total_vertices += int(len(vertices))
        total_polygons += int(len(faces))
        total_edges += int(len(unique_edges))

        if not bool(getattr(geometry, "is_watertight", False)):
            volume_complete = False
            continue

        watertight_geometry_count += 1
        raw_volume = getattr(geometry, "volume", None)

        if raw_volume is None:
            volume_complete = False
            continue

        try:
            volume = float(raw_volume)
        except (TypeError, ValueError):
            volume_complete = False
            continue

        if not math.isfinite(volume):
            volume_complete = False
            continue

        # Negative signed volume can result from face winding.
        # Physical volume is non-negative.
        volumes.append(abs(volume))

    scene_units = getattr(scene, "units", None)

    linear_unit = (
        str(scene_units)
        if scene_units
        else None
    )

    return {
        "geometry_count": len(geometry_records),
        "total_vertices": total_vertices,

        # Trimesh represents mesh faces as triangles.
        "total_polygons": total_polygons,
        "polygon_interpretation": "triangulated_faces",

        # edges_unique is deliberately used instead of raw edges.
        # Raw mesh edges contain duplicates for shared faces.
        "total_edges": total_edges,
        "edge_interpretation": "unique_topological_edges_per_geometry",
        "watertight_geometry_count": watertight_geometry_count,
        "volume_complete": volume_complete,
        "volume": (
            sum(volumes)
            if volume_complete
            else None
        ),

        "linear_unit": linear_unit,
        "volume_unit": (
            f"{linear_unit}³"
            if linear_unit
            else None
        ),
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
    def _materialize(stream: BinaryIO, destination: Path) -> None:
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

    def render_preview(
        self,
        stream: BinaryIO,
        *,
        source_format:
            ExtractionSourceFormat,
    ) -> bytes | None:
        source = self._require_source(
            source_format,
            operation="render_preview",
        )

        with tempfile.TemporaryDirectory(
            prefix="bimap-mesh-preview-"
        ) as directory_name:
            path = (
                Path(directory_name)
                / f"source.{source.value}"
            )

            self._materialize(
                stream,
                path,
            )

            scene = self._open_scene(
                path,
                source=source,
            )

            try:
                payload = (
                    _render_scene_preview_png(
                        scene
                    )
                )
            except Exception as exc:
                logger.warning(
                    {
                        "event":
                            "mesh_preview_unavailable",
                        "source_format":
                            source.value,
                        "renderer":
                            "software",
                        "error":
                            lower_error_context(
                                exc
                            ),
                    }
                )

                return None

            if payload is None:
                logger.warning(
                    {
                        "event":
                            "mesh_preview_unavailable",
                        "source_format":
                            source.value,
                        "renderer":
                            "software",
                        "reason":
                            (
                                "source geometry "
                                "cannot be projected "
                                "into a triangular "
                                "mesh preview"
                            ),
                    }
                )

                return None

            logger.info(
                {
                    "event":
                        "mesh_preview_rendered",
                    "source_format":
                        source.value,
                    "renderer":
                        "software",
                    "width":
                        _PREVIEW_RESOLUTION[
                            0
                        ],
                    "height":
                        _PREVIEW_RESOLUTION[
                            1
                        ],
                    "size_bytes":
                        len(payload),
                }
            )

            return payload

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
    def _open_scene(path: Path, *, source: ExtractionSourceFormat) -> trimesh.Scene:
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
    def _inspection(scene: trimesh.Scene,*, source: ExtractionSourceFormat) -> DataSourceInspection:
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

    def inspect(self, stream: BinaryIO, *, source_format: ExtractionSourceFormat) -> DataSourceInspection:
        source = self._require_source(source_format, operation="inspect")
        with tempfile.TemporaryDirectory(
            prefix="bimap-mesh-extract-"
        ) as directory_name:
            path = (
                Path(directory_name)
                / f"source.{source.value}"
            )
            self._materialize(stream, path)
            scene = self._open_scene(path, source=source)
            return self._inspection(scene, source=source)

    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        source = self._require_source(source_format, operation="extract")
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
            scene = self._open_scene(path, source=source)
            inspection = self._inspection(scene, source=source)
            geometry_records = _mesh_records(scene)

            class_counts = Counter(
                type(geometry).__name__
                for _, geometry in geometry_records
            )
            extracted: dict[str, tuple[dict[str, Any], ...]] = {}
            counts: dict[str, int] = {}

            if ExtractionDataset.ELEMENTS in selected:
                rows = tuple(
                    {
                        "source_id": str(name),
                        "source_class": type(geometry).__name__,
                        "name": str(name),
                        "vertex_count": int(len(getattr(geometry, "vertices", ()))),
                        "face_count": int(len(getattr(geometry, "faces", ()))),
                        "edge_count": int(len(getattr(geometry, "edges_unique", ()))),
                    }
                    for name, geometry in geometry_records
                )
                extracted[ExtractionDataset.ELEMENTS.value] = rows
                counts[ExtractionDataset.ELEMENTS.value] = len(rows)

            if ExtractionDataset.PROPERTIES in selected:
                property_rows: list[dict[str, Any]] = []
                for name, geometry in geometry_records:
                    properties = {
                        "is_watertight": getattr(geometry, "is_watertight", None),
                        "is_winding_consistent": getattr(geometry, "is_winding_consistent", None),
                        "euler_number": getattr(geometry, "euler_number", None),
                    }
                    metadata = getattr(geometry, "metadata", None)
                    if isinstance(metadata, dict):
                        properties["metadata"] = _json_value(metadata)

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
                extracted[ExtractionDataset.PROPERTIES.value] = rows
                counts[ExtractionDataset.PROPERTIES.value] = len(rows)

            if ExtractionDataset.QUANTITIES in selected:
                quantity_rows: list[dict[str, Any]] = []
                for name, geometry in geometry_records:
                    bounds = _json_value(getattr(geometry, "bounds", None))
                    extents = _json_value(getattr(geometry, "extents", None))
                    area = getattr(geometry, "area", None)
                    watertight = bool(getattr(geometry, "is_watertight", False))
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
                        volume = getattr(geometry, "volume", None)
                        if isinstance( volume, (int, float)) and math.isfinite(float(volume)):
                            candidates["volume"] = float(volume)

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
                extracted[ExtractionDataset.QUANTITIES.value] = rows
                counts[ExtractionDataset.QUANTITIES.value] = len(rows)

            if ExtractionDataset.MATERIALS in selected:
                material_rows: list[dict[str, Any]] = []
                for name, geometry in geometry_records:
                    material = _mesh_material(geometry)
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
                extracted[ExtractionDataset.MATERIALS.value] = rows
                counts[ExtractionDataset.MATERIALS.value] = len(rows)

            units_value = getattr(scene, "units", None)
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

            metadata = getattr(scene, "metadata", None)
            project = (
                {
                    "metadata": _json_value(metadata)
                }
                if isinstance(metadata, dict)
                and metadata
                else {}
            )

            geometry_summary = (_geometry_summary(scene, geometry_records))

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
                ifc_class_counts=dict(sorted(class_counts.items())),
                geometry_summary=geometry_summary,
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
