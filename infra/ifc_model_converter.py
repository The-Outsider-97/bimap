"""IfcOpenShell-backed IFC model converter for BIMAP."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

import ifcopenshell  # type: ignore
import ifcopenshell.geom  # type: ignore

from ..app.ports.model_conversion import *
from ..app.utils.app_errors import *
from ..app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP IfcOpenShell Model Converter")
printer = PrettyPrinter()

_COMPONENT = "ifcopenshell_model_converter"
_COPY_CHUNK_BYTES = 1024 * 1024


class IfcOpenShellModelConverter(ModelConverter):
    """Convert IFC geometry to GLB or an OBJ/MTL ZIP package."""

    __slots__ = ("_num_threads",)

    _CAPABILITIES = (
        ModelConversionCapability(
            source_format=ModelSourceFormat.IFC,
            extensions=(".ifc",),
            target_formats=(ModelTargetFormat.GLB, ModelTargetFormat.OBJ),
        ),
    )

    def __init__(self, *, num_threads: int = 1) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing IfcOpenShell model converter",
            event="ifc_converter_init_start",
        )
        if isinstance(num_threads, bool) or not isinstance(num_threads, int) or num_threads <= 0:
            raise AppConfigurationError(
                "num_threads must be a positive integer.",
                component=_COMPONENT,
                operation="initialize",
                field="num_threads",
                context={"received_type": type(num_threads).__name__},
            )
        self._num_threads = num_threads
        logger.info(
            {
                "event": "ifc_converter_initialized",
                "num_threads": num_threads,
                "ifcopenshell_version": getattr(ifcopenshell, "version", "unknown"),
            }
        )

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        return self._CAPABILITIES

    @staticmethod
    def _copy_stream(stream: BinaryIO, destination: Path) -> None:
        source = require_binary_stream(
            stream,
            field="stream",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="materialize_source",
        )
        try:
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise UnsupportedAppInputError(
                "Model source stream must be seekable.",
                component=_COMPONENT,
                operation="materialize_source",
                field="stream",
                cause=exc,
            ) from exc

        with destination.open("wb") as target:
            shutil.copyfileobj(source, target, length=_COPY_CHUNK_BYTES)

    @staticmethod
    def _open_ifc(path: Path):
        try:
            return ifcopenshell.open(str(path))
        except Exception as exc:
            raise UnsupportedAppInputError(
                "The uploaded file is not a readable IFC model.",
                component=_COMPONENT,
                operation="open_ifc",
                field="source",
                context={"error_type": type(exc).__name__},
                cause=exc,
            ) from exc

    @staticmethod
    def _geometry_settings() -> object:
        settings = ifcopenshell.geom.settings()
        settings.set(
            "dimensionality",
            ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS,
        )
        settings.set("apply-default-materials", True)
        return settings

    @staticmethod
    def _serializer_settings() -> object:
        settings = ifcopenshell.geom.serializer_settings()
        settings.set("use-element-guids", True)
        return settings

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
    ) -> ModelSourceInspection:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Inspecting IFC source model",
            event="ifc_converter_inspect_start",
        )
        if ModelSourceFormat.parse(source_format) is not ModelSourceFormat.IFC:
            raise UnsupportedAppInputError(
                "IfcOpenShell converter only accepts IFC sources.",
                component=_COMPONENT,
                operation="inspect",
                field="source_format",
            )

        with tempfile.TemporaryDirectory(prefix="bimap-ifc-inspect-") as directory:
            source_path = Path(directory) / "source.ifc"
            self._copy_stream(stream, source_path)
            model = self._open_ifc(source_path)
            try:
                schema = str(model.schema)
                product_count = len(model.by_type("IfcProduct"))
            finally:
                close = getattr(model, "close", None)
                if callable(close):
                    close()

        if product_count <= 0:
            raise UnsupportedAppInputError(
                "IFC source contains no IfcProduct entities to convert.",
                component=_COMPONENT,
                operation="inspect",
                field="source",
            )

        return ModelSourceInspection(
            source_format=ModelSourceFormat.IFC,
            schema=schema,
            product_count=product_count,
        )

    def _serialize(
        self,
        model: object,
        *,
        target_format: ModelTargetFormat,
        directory: Path,
        output_stem: str,
    ) -> tuple[Path, str, str]:
        geometry_settings = self._geometry_settings()
        serializer_settings = self._serializer_settings()

        if target_format is ModelTargetFormat.GLB:
            primary_path = directory / f"{output_stem}.glb"
            serialiser = ifcopenshell.geom.serializers.gltf(
                str(primary_path),
                geometry_settings,
                serializer_settings,
            )
            result_path = primary_path
            output_filename = primary_path.name
            content_type = "model/gltf-binary"
        elif target_format is ModelTargetFormat.OBJ:
            obj_path = directory / f"{output_stem}.obj"
            mtl_path = directory / f"{output_stem}.mtl"
            serialiser = ifcopenshell.geom.serializers.obj(
                str(obj_path),
                str(mtl_path),
                geometry_settings,
                serializer_settings,
            )
            result_path = directory / f"{output_stem}-obj.zip"
            output_filename = result_path.name
            content_type = "application/zip"
        else:
            raise UnsupportedAppInputError(
                "IfcOpenShell target format is unsupported.",
                component=_COMPONENT,
                operation="serialize",
                field="target_format",
            )

        serialiser.setFile(model)
        serialiser.setUnitNameAndMagnitude("METER", 1.0)
        serialiser.writeHeader()

        iterator = ifcopenshell.geom.iterator(
            geometry_settings,
            model,
            self._num_threads,
        )
        geometry_count = 0
        try:
            if iterator.initialize():
                while True:
                    serialiser.write(iterator.get())
                    geometry_count += 1
                    if not iterator.next():
                        break
        finally:
            serialiser.finalize()

        if geometry_count == 0:
            raise UnsupportedAppInputError(
                "IFC source contains no convertible 3D geometry.",
                component=_COMPONENT,
                operation="serialize",
                field="source",
            )

        if target_format is ModelTargetFormat.OBJ:
            obj_path = directory / f"{output_stem}.obj"
            mtl_path = directory / f"{output_stem}.mtl"
            if not obj_path.is_file() or not mtl_path.is_file():
                raise AppIntegrityError(
                    "IfcOpenShell did not produce the expected OBJ package members.",
                    component=_COMPONENT,
                    operation="serialize",
                    field="artifact",
                )
            with zipfile.ZipFile(
                result_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                generated = sorted(
                    path
                    for path in directory.iterdir()
                    if path.is_file()
                    and path != result_path
                    and path.name != "source.ifc"
                )
                for generated_path in generated:
                    archive.write(
                        generated_path,
                        arcname=generated_path.name,
                    )

        if not result_path.is_file() or result_path.stat().st_size <= 0:
            raise AppIntegrityError(
                "Model conversion did not produce a non-empty output artifact.",
                component=_COMPONENT,
                operation="serialize",
                field="artifact",
            )

        return result_path, output_filename, content_type

    @staticmethod
    def _detach_artifact(
        path: Path,
        *,
        filename: str,
        content_type: str,
    ) -> ConvertedModelArtifact:
        stream = tempfile.TemporaryFile(mode="w+b")
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with path.open("rb") as source:
                while True:
                    chunk = source.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    stream.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
            stream.seek(0)
            return ConvertedModelArtifact(
                stream=stream,
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                content_hash=digest.hexdigest(),
            )
        except Exception:
            stream.close()
            raise

    def convert(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
        target_format: ModelTargetFormat,
        output_stem: str,
    ) -> ConvertedModelArtifact:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Converting IFC source model",
            event="ifc_converter_convert_start",
            context={"target_format": str(target_format)},
        )
        source = ModelSourceFormat.parse(source_format)
        target = ModelTargetFormat.parse(target_format)
        if source is not ModelSourceFormat.IFC:
            raise UnsupportedAppInputError(
                "IfcOpenShell converter only accepts IFC sources.",
                component=_COMPONENT,
                operation="convert",
                field="source_format",
            )
        if target not in (ModelTargetFormat.GLB, ModelTargetFormat.OBJ):
            raise UnsupportedAppInputError(
                "IfcOpenShell target format is unsupported.",
                component=_COMPONENT,
                operation="convert",
                field="target_format",
            )
        if not output_stem or not output_stem.strip():
            raise AppValidationError(
                "output_stem cannot be empty.",
                component=_COMPONENT,
                operation="convert",
                field="output_stem",
            )

        with tempfile.TemporaryDirectory(prefix="bimap-ifc-convert-") as directory_name:
            directory = Path(directory_name)
            source_path = directory / "source.ifc"
            self._copy_stream(stream, source_path)
            model = self._open_ifc(source_path)
            try:
                result_path, filename, content_type = self._serialize(
                    model,
                    target_format=target,
                    directory=directory,
                    output_stem=output_stem,
                )
                artifact = self._detach_artifact(
                    result_path,
                    filename=filename,
                    content_type=content_type,
                )
            except AppError:
                raise
            except Exception as exc:
                raise AppIntegrityError(
                    "IfcOpenShell conversion failed outside the BIMAP application-error contract.",
                    component=_COMPONENT,
                    operation="convert",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc
            finally:
                close = getattr(model, "close", None)
                if callable(close):
                    close()

        logger.info(
            {
                "event": "ifc_converter_convert_completed",
                "target_format": target.value,
                "size_bytes": artifact.size_bytes,
            }
        )
        return artifact


__all__ = ["IfcOpenShellModelConverter"]
