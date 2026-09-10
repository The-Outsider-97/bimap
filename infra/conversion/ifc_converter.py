"""IfcOpenShell-backed IFC model converter for BIMAP."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

import ifcopenshell  # type: ignore
import ifcopenshell.geom  # type: ignore

from ...app.ports.model_conversion import (
    ConvertedModelArtifact,
    ModelConversionCapability,
    ModelConverter,
    ModelSourceFormat,
    ModelSourceInspection,
    ModelTargetFormat,
)
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from . import (
    _detach_artifact,
    _materialize_stream,
    _normalize_output_stem,
    _zip_generated_files,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP IfcOpenShell Model Converter")
printer = PrettyPrinter()

_COMPONENT = "ifcopenshell_model_converter"


class IfcOpenShellModelConverter(ModelConverter):
    """Convert IFC geometry to GLB or an OBJ package using IfcOpenShell."""

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
    def _open_ifc(path: Path):
        try:
            return ifcopenshell.open(str(path))
        except Exception as exc:
            raise UnsupportedAppInputError(
                "The uploaded file is not a readable IFC model.",
                component=_COMPONENT,
                operation="open_ifc",
                field="source",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

    @staticmethod
    def _close_ifc(model: object) -> None:
        close = getattr(model, "close", None)
        if callable(close):
            close()

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
        source = ModelSourceFormat.parse(source_format)
        if source is not ModelSourceFormat.IFC:
            raise UnsupportedAppInputError(
                "IfcOpenShell converter accepts IFC sources only.",
                component=_COMPONENT,
                operation="inspect",
                field="source_format",
                context={"received": source.value},
            )

        with tempfile.TemporaryDirectory(prefix="bimap-ifc-inspect-") as directory_name:
            source_path = Path(directory_name) / "source.ifc"
            _materialize_stream(
                stream,
                source_path,
                component=_COMPONENT,
                operation="materialize_source",
            )
            model = self._open_ifc(source_path)
            try:
                schema = str(getattr(model, "schema", "") or "").strip()
                product_count = len(model.by_type("IfcProduct"))
            finally:
                self._close_ifc(model)

        if not schema:
            raise AppIntegrityError(
                "Parsed IFC model did not expose a schema identifier.",
                component=_COMPONENT,
                operation="inspect",
                field="schema",
            )
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
                context={"target_format": target_format.value},
            )

        try:
            serialiser.setFile(model)
            serialiser.setUnitNameAndMagnitude("METER", 1.0)
            serialiser.writeHeader()

            iterator = ifcopenshell.geom.iterator(
                geometry_settings,
                model,
                self._num_threads,
            )
            geometry_count = 0
            if iterator.initialize():
                while True:
                    serialiser.write(iterator.get())
                    geometry_count += 1
                    if not iterator.next():
                        break
        finally:
            serialiser.finalize()

        if geometry_count <= 0:
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
            generated = tuple(
                sorted(
                    (
                        path
                        for path in directory.iterdir()
                        if path.is_file()
                        and path != result_path
                        and path.name != "source.ifc"
                    ),
                    key=lambda path: path.name,
                )
            )
            _zip_generated_files(
                generated,
                result_path,
                component=_COMPONENT,
                operation="package_obj",
            )

        if not result_path.is_file() or result_path.stat().st_size <= 0:
            raise AppIntegrityError(
                "IfcOpenShell conversion did not produce a non-empty artifact.",
                component=_COMPONENT,
                operation="serialize",
                field="artifact",
            )

        return result_path, output_filename, content_type

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
        stem = _normalize_output_stem(
            output_stem,
            component=_COMPONENT,
            operation="convert",
        )

        if source is not ModelSourceFormat.IFC:
            raise UnsupportedAppInputError(
                "IfcOpenShell converter accepts IFC sources only.",
                component=_COMPONENT,
                operation="convert",
                field="source_format",
                context={"received": source.value},
            )
        if target not in self._CAPABILITIES[0].target_formats:
            raise UnsupportedAppInputError(
                "IfcOpenShell target format is unsupported.",
                component=_COMPONENT,
                operation="convert",
                field="target_format",
                context={"target_format": target.value},
            )

        with tempfile.TemporaryDirectory(prefix="bimap-ifc-convert-") as directory_name:
            directory = Path(directory_name)
            source_path = directory / "source.ifc"
            _materialize_stream(
                stream,
                source_path,
                component=_COMPONENT,
                operation="materialize_source",
            )
            model = self._open_ifc(source_path)
            try:
                result_path, filename, content_type = self._serialize(
                    model,
                    target_format=target,
                    directory=directory,
                    output_stem=stem,
                )
                artifact = _detach_artifact(
                    result_path,
                    filename=filename,
                    content_type=content_type,
                    component=_COMPONENT,
                    operation="detach_artifact",
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
                self._close_ifc(model)

        logger.info(
            {
                "event": "ifc_converter_convert_completed",
                "target_format": target.value,
                "size_bytes": artifact.size_bytes,
            }
        )
        return artifact


__all__ = ["IfcOpenShellModelConverter"]


if __name__ == "__main__":
    print("\n=== Running IFC Converter Self-Test ===\n")
    printer.status("TEST", "IFC converter module initialized", "info")
    converter = IfcOpenShellModelConverter(num_threads=1)
    assert converter.capabilities[0].source_format is ModelSourceFormat.IFC
    printer.status("PASS", "IFC converter capability contract", "success")
    print("\n=== Test ran successfully ===\n")
