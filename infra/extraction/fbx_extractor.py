"""
Blender-backed FBX data extraction for BIMAP.

FBX is normalized to a temporary GLB using Blender's native FBX importer and
glTF exporter, then the existing generic Trimesh extractor is reused.  This
avoids a duplicate geometry-extraction implementation and deliberately limits
claims to geometry/material data that survive this normalization step.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from .mesh_extractor import TrimeshDataExtractor
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP FBX Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "fbx_data_extractor"
_COPY_CHUNK_BYTES = 1024 * 1024
_SUPPORTED_DATASETS = (
    ExtractionDataset.ELEMENTS,
    ExtractionDataset.PROPERTIES,
    ExtractionDataset.QUANTITIES,
    ExtractionDataset.MATERIALS,
)


class BlenderFbxDataExtractor(DataExtractor):
    """
    Extract FBX geometry through Blender -> GLB -> Trimesh normalization.

    The executable is a deployment dependency and is validated at startup.
    """

    __slots__ = (
        "_blender",
        "_timeout_seconds",
        "_mesh_extractor",
    )

    _CAPABILITY = DataExtractionCapability(
        source_format=ExtractionSourceFormat.parse("FBX"),
        extensions=(".fbx",),
        datasets=_SUPPORTED_DATASETS,
    )

    def __init__(
        self,
        blender_executable: str = "blender",
        *,
        timeout_seconds: int = 300,
        mesh_extractor: TrimeshDataExtractor | None = None,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing FBX data extractor",
            event="fbx_data_extractor_init_start",
        )

        executable = require_app_text(
            blender_executable,
            field="blender_executable",
            error_type=AppConfigurationError,
            component=_COMPONENT,
            operation="initialize",
            max_length=4096,
        )
        resolved = shutil.which(executable)
        if resolved is None:
            path = Path(executable).expanduser()
            if path.is_file():
                resolved = str(path.resolve())

        if resolved is None:
            raise AppConfigurationError(
                "Blender executable could not be resolved.",
                component=_COMPONENT,
                operation="initialize",
                field="blender_executable",
            )

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise AppConfigurationError(
                "timeout_seconds must be a positive integer.",
                component=_COMPONENT,
                operation="initialize",
                field="timeout_seconds",
            )

        if (
            mesh_extractor is not None
            and not isinstance(
                mesh_extractor,
                TrimeshDataExtractor,
            )
        ):
            raise AppConfigurationError(
                "mesh_extractor must be TrimeshDataExtractor or None.",
                component=_COMPONENT,
                operation="initialize",
                field="mesh_extractor",
                context={
                    "received_type": type(mesh_extractor).__name__,
                },
            )

        self._blender = resolved
        self._timeout_seconds = timeout_seconds
        self._mesh_extractor = (
            mesh_extractor
            if mesh_extractor is not None
            else TrimeshDataExtractor()
        )

        logger.info(
            {
                "event": "fbx_data_extractor_initialized",
                "timeout_seconds": timeout_seconds,
            }
        )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return (self._CAPABILITY,)

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
                "FBX source stream must be seekable.",
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
                        "FBX source stream yielded non-binary data.",
                        component=_COMPONENT,
                        operation="materialize_source",
                        field="source",
                    )
                target.write(bytes(chunk))

        if destination.stat().st_size <= 0:
            raise AppValidationError(
                "FBX source model cannot be empty.",
                component=_COMPONENT,
                operation="materialize_source",
                field="source",
            )

    @staticmethod
    def _require_source(
        source_format: ExtractionSourceFormat | str,
        *,
        operation: str,
    ) -> ExtractionSourceFormat:
        source = ExtractionSourceFormat.parse(
            source_format
        )
        if source is not ExtractionSourceFormat.parse("FBX"):
            raise UnsupportedAppInputError(
                "FBX extractor accepts FBX sources only.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={"source_format": source.value},
            )
        return source

    def _convert_to_glb(
        self,
        source_path: Path,
        *,
        directory: Path,
    ) -> Path:
        glb_path = directory / "normalized.glb"
        script_path = directory / "convert_fbx.py"

        source_literal = json.dumps(
            str(source_path.resolve())
        )
        output_literal = json.dumps(
            str(glb_path.resolve())
        )

        script_path.write_text(
            "\n".join(
                (
                    "import bpy",
                    "bpy.ops.wm.read_factory_settings(use_empty=True)",
                    f"source_path = {source_literal}",
                    f"output_path = {output_literal}",
                    "result = bpy.ops.import_scene.fbx(filepath=source_path)",
                    "if 'FINISHED' not in result:",
                    "    raise RuntimeError('Blender FBX import did not finish successfully.')",
                    "result = bpy.ops.export_scene.gltf(",
                    "    filepath=output_path,",
                    "    export_format='GLB',",
                    ")",
                    "if 'FINISHED' not in result:",
                    "    raise RuntimeError('Blender GLB export did not finish successfully.')",
                )
            ),
            encoding="utf-8",
        )

        try:
            completed = subprocess.run(
                [
                    self._blender,
                    "--background",
                    "--factory-startup",
                    "--python",
                    str(script_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppIntegrityError(
                "Blender FBX normalization exceeded the configured timeout.",
                component=_COMPONENT,
                operation="normalize_fbx",
                context={
                    "timeout_seconds": self._timeout_seconds,
                },
                cause=exc,
            ) from exc
        except OSError as exc:
            raise AppConfigurationError(
                "Blender executable could not be started.",
                component=_COMPONENT,
                operation="normalize_fbx",
                field="blender_executable",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if completed.returncode != 0:
            raise AppValidationError(
                "Blender could not import the uploaded FBX source.",
                component=_COMPONENT,
                operation="normalize_fbx",
                field="source",
                context={
                    "return_code": completed.returncode,
                },
            )

        if (
            not glb_path.is_file()
            or glb_path.stat().st_size <= 0
        ):
            raise AppIntegrityError(
                "Blender did not produce a non-empty GLB normalization artifact.",
                component=_COMPONENT,
                operation="normalize_fbx",
                field="artifact",
            )

        return glb_path

    @staticmethod
    def _fbx_inspection(
        mesh_inspection: DataSourceInspection,
    ) -> DataSourceInspection:
        return DataSourceInspection(
            source_format=ExtractionSourceFormat.parse("FBX"),
            # Current port requires a non-empty schema.  "FBX" is an explicit
            # format identifier, not a fabricated file-version claim.
            schema="FBX",
            product_count=mesh_inspection.product_count,
            project_name=mesh_inspection.project_name,
        )

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        self._require_source(
            source_format,
            operation="inspect",
        )

        with tempfile.TemporaryDirectory(
            prefix="bimap-fbx-extract-"
        ) as directory_name:
            directory = Path(directory_name)
            source_path = directory / "source.fbx"
            self._materialize(stream, source_path)
            glb_path = self._convert_to_glb(
                source_path,
                directory=directory,
            )

            with glb_path.open("rb") as normalized:
                mesh_inspection = (
                    self._mesh_extractor.inspect(
                        normalized,
                        source_format=ExtractionSourceFormat.parse("GLB"),
                    )
                )

        return self._fbx_inspection(
            mesh_inspection
        )

    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        self._require_source(
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
                "Requested dataset is unsupported for FBX extraction.",
                component=_COMPONENT,
                operation="extract",
                field="datasets",
                context={"unsupported": unsupported},
            )

        with tempfile.TemporaryDirectory(
            prefix="bimap-fbx-extract-"
        ) as directory_name:
            directory = Path(directory_name)
            source_path = directory / "source.fbx"
            self._materialize(stream, source_path)
            glb_path = self._convert_to_glb(
                source_path,
                directory=directory,
            )

            with glb_path.open("rb") as normalized:
                mesh_result = (
                    self._mesh_extractor.extract(
                        normalized,
                        source_format=ExtractionSourceFormat.parse("GLB"),
                        datasets=selected,
                    )
                )

        inspection = self._fbx_inspection(
            mesh_result.inspection
        )

        logger.info(
            {
                "event": "fbx_data_extraction_completed",
                "entity_count": inspection.product_count,
                "selected_datasets": tuple(
                    item.value
                    for item in selected
                ),
            }
        )

        return ExtractedModelData(
            inspection=inspection,
            project=mesh_result.project,
            units=mesh_result.units,
            datasets=mesh_result.datasets,
            counts=mesh_result.counts,
            # Compatibility field in the current application contract.
            ifc_class_counts=mesh_result.ifc_class_counts,
        )


__all__ = ["BlenderFbxDataExtractor"]


if __name__ == "__main__":
    print("\n=== Running FBX Data Extractor Self-Test ===\n")
    printer.status("TEST", "FBX extractor module loaded", "info")
    assert (
        BlenderFbxDataExtractor._CAPABILITY.source_format
        is ExtractionSourceFormat.parse("FBX")
    )
    printer.status("PASS", "FBX capability contract", "success")
    print("\n=== Test ran successfully ===\n")
