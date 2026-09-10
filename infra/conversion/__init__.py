"""
Concrete multi-format model-conversion infrastructure for BIMAP.

The application layer owns the provider-neutral ``ModelConverter`` contract.
This package contains source-specific adapters plus one deterministic dispatcher.
No adapter is selected by default: the application service resolves the source
format from the uploaded filename against the capabilities exposed here.

Optional/provider-specific modules are imported lazily so a deployment that
uses only IFC conversion is not forced to import Trimesh, Blender integration,
or proprietary Revit/DWG backends during package import.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import tempfile
import zipfile

from pathlib import Path
from typing import BinaryIO, cast

from ...app.ports.model_conversion import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Multi-Format Model Converter")
printer = PrettyPrinter()

_COMPONENT = "multi_format_model_converter"
_COPY_CHUNK_BYTES = 1024 * 1024
_SAFE_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


def _materialize_stream(stream: BinaryIO, destination: Path, *, component: str, operation: str) -> None:
    """Copy one validated binary source stream to an adapter-owned file."""
    source = require_binary_stream(
        stream,
        field="source",
        error_type=UnsupportedAppInputError,
        component=component,
        operation=operation,
    )
    try:
        source.seek(0)
    except (AttributeError, OSError) as exc:
        raise UnsupportedAppInputError(
            "Model source stream must be seekable.",
            component=component,
            operation=operation,
            field="source",
            cause=exc,
        ) from exc

    size_bytes = 0
    with destination.open("wb") as target:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise UnsupportedAppInputError(
                    "Model source stream yielded non-binary data.",
                    component=component,
                    operation=operation,
                    field="source",
                )
            payload = bytes(chunk)
            target.write(payload)
            size_bytes += len(payload)

    if size_bytes <= 0:
        raise AppValidationError(
            "Model source cannot be empty.",
            component=component,
            operation=operation,
            field="source",
        )


def _normalize_output_stem(value: str, *, component: str, operation: str) -> str:
    """Validate the already-sanitized application output stem defensively."""
    stem = require_app_text(
        value,
        field="output_stem",
        error_type=AppValidationError,
        component=component,
        operation=operation,
        max_length=120,
    )
    if not _SAFE_STEM.fullmatch(stem):
        raise AppValidationError(
            "output_stem must be a safe 1-120 character basename stem.",
            component=component,
            operation=operation,
            field="output_stem",
        )
    return stem


def _detach_artifact(
    path: Path,
    *,
    filename: str,
    content_type: str,
    component: str,
    operation: str,
) -> ConvertedModelArtifact:
    """Detach a generated file from temporary storage into the port artifact."""
    if not path.is_file() or path.stat().st_size <= 0:
        raise AppIntegrityError(
            "Model conversion did not produce a non-empty output artifact.",
            component=component,
            operation=operation,
            field="artifact",
        )

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
            stream=cast(BinaryIO, stream),
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            content_hash=digest.hexdigest(),
        )
    except Exception:
        stream.close()
        raise


def _zip_generated_files(paths: tuple[Path, ...], destination: Path, *, component: str, operation: str) -> None:
    """Create one deterministic ZIP package from generated sidecar files."""
    unique: dict[str, Path] = {}
    for path in paths:
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        name = path.name
        if name in unique and unique[name] != path:
            raise AppIntegrityError(
                "Generated conversion files contain duplicate basenames.",
                component=component,
                operation=operation,
                field="artifact",
                context={"filename": name},
            )
        unique[name] = path

    if not unique:
        raise AppIntegrityError(
            "No generated files are available for conversion packaging.",
            component=component,
            operation=operation,
            field="artifact",
        )

    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for name in sorted(unique):
            archive.write(unique[name], arcname=name)

    if not destination.is_file() or destination.stat().st_size <= 0:
        raise AppIntegrityError(
            "Generated conversion package is empty.",
            component=component,
            operation=operation,
            field="artifact",
        )


class MultiFormatModelConverter(ModelConverter):
    """
    Dispatch conversion to exactly one configured adapter per source format.

    There is deliberately no IFC fallback.  ``source_format`` must already have
    been resolved from the uploaded source against this dispatcher's advertised
    capabilities by the application service.
    """

    __slots__ = ("_converters", "_by_format", "_capabilities")

    def __init__(self, *converters: ModelConverter) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing multi-format model converter",
            event="multi_format_model_converter_init_start",
            context={"converter_count": len(converters)},
        )
        if not converters:
            raise AppConfigurationError(
                "At least one model converter must be configured.",
                component=_COMPONENT,
                operation="initialize",
                field="converters",
            )

        normalized: list[ModelConverter] = []
        by_format: dict[ModelSourceFormat, ModelConverter] = {}
        capabilities: list[ModelConversionCapability] = []

        for index, converter in enumerate(converters):
            if not isinstance(converter, ModelConverter):
                raise AppConfigurationError(
                    "Every configured converter must implement ModelConverter.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"converters[{index}]",
                    context={"received_type": type(converter).__name__},
                )

            adapter_capabilities = tuple(converter.capabilities)
            if not adapter_capabilities:
                raise AppConfigurationError(
                    "Configured converter advertises no capabilities.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"converters[{index}].capabilities",
                    context={"converter_type": type(converter).__name__},
                )

            for capability_index, capability in enumerate(adapter_capabilities):
                if not isinstance(capability, ModelConversionCapability):
                    raise AppConfigurationError(
                        "Converter capability must be ModelConversionCapability.",
                        component=_COMPONENT,
                        operation="initialize",
                        field=(
                            f"converters[{index}].capabilities[{capability_index}]"
                        ),
                        context={"received_type": type(capability).__name__},
                    )

                source = ModelSourceFormat.parse(capability.source_format)
                owner = by_format.get(source)
                if owner is not None and owner is not converter:
                    raise AppConfigurationError(
                        "Multiple model converters advertise the same source format.",
                        component=_COMPONENT,
                        operation="initialize",
                        field="converters",
                        context={
                            "source_format": source.value,
                            "first_converter": type(owner).__name__,
                            "second_converter": type(converter).__name__,
                        },
                    )
                by_format[source] = converter
                capabilities.append(capability)

            normalized.append(converter)

        capabilities.sort(
            key=lambda item: (
                ModelSourceFormat.parse(item.source_format).value,
                item.extensions,
                tuple(
                    ModelTargetFormat.parse(target).value
                    for target in item.target_formats
                ),
            )
        )
        self._converters = tuple(normalized)
        self._by_format = by_format
        self._capabilities = tuple(capabilities)

        logger.info(
            {
                "event": "multi_format_model_converter_initialized",
                "converter_count": len(self._converters),
                "source_formats": tuple(
                    source.value
                    for source in sorted(
                        self._by_format,
                        key=lambda item: item.value,
                    )
                ),
            }
        )

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        return self._capabilities

    def _resolve(
        self,
        source_format: ModelSourceFormat | str,
        *,
        operation: str,
    ) -> tuple[ModelSourceFormat, ModelConverter]:
        source = ModelSourceFormat.parse(source_format)
        converter = self._by_format.get(source)
        if converter is None:
            raise UnsupportedAppInputError(
                "No configured model converter supports the selected source format.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={
                    "source_format": source.value,
                    "supported": tuple(
                        item.value
                        for item in sorted(
                            self._by_format,
                            key=lambda value: value.value,
                        )
                    ),
                },
            )
        return source, converter

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
    ) -> ModelSourceInspection:
        source, converter = self._resolve(source_format, operation="inspect")
        return converter.inspect(stream, source_format=source)

    def convert(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
        target_format: ModelTargetFormat,
        output_stem: str,
    ) -> ConvertedModelArtifact:
        source, converter = self._resolve(source_format, operation="convert")
        target = ModelTargetFormat.parse(target_format)
        capability = next(
            (
                item
                for item in converter.capabilities
                if ModelSourceFormat.parse(item.source_format) is source
            ),
            None,
        )
        if capability is None or target not in capability.target_formats:
            raise UnsupportedAppInputError(
                "Requested source/target conversion pair is unsupported.",
                component=_COMPONENT,
                operation="convert",
                field="target_format",
                context={
                    "source_format": source.value,
                    "target_format": target.value,
                },
            )
        return converter.convert(
            stream,
            source_format=source,
            target_format=target,
            output_stem=output_stem,
        )


# Lazy imports preserve optional/provider-specific dependency boundaries.
_LAZY_EXPORTS = {
    "IfcOpenShellModelConverter": ("ifc_converter", "IfcOpenShellModelConverter"),
    "RevitModelConverter": ("revit_converter", "RevitModelConverter"),
    "RevitConversionBackend": ("revit_converter", "RevitConversionBackend"),
    "DwgDxfModelConverter": ("dwg_converter", "DwgDxfModelConverter"),
    "DwgDxfConversionBackend": ("dwg_converter", "DwgDxfConversionBackend"),
    "BlenderFbxModelConverter": ("fbx_converter", "BlenderFbxModelConverter"),
    "TrimeshModelConverter": ("mesh_converter", "TrimeshModelConverter"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    module = importlib.import_module(f"{__name__}.{module_name}")
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


__all__ = [
    "MultiFormatModelConverter",
    "IfcOpenShellModelConverter",
    "RevitModelConverter",
    "RevitConversionBackend",
    "DwgDxfModelConverter",
    "DwgDxfConversionBackend",
    "BlenderFbxModelConverter",
    "TrimeshModelConverter",
]


if __name__ == "__main__":
    print("\n=== Running BIMAP Conversion Package Self-Test ===\n")
    printer.status("TEST", "Conversion package initialized", "info")
    assert issubclass(MultiFormatModelConverter, ModelConverter)
    printer.status("PASS", "Conversion dispatcher contract", "success")
    print("\n=== Test ran successfully ===\n")
