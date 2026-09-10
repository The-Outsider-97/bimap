"""
DWG/DXF conversion adapter boundary for BIMAP.

Reliable DWG conversion requires a real CAD-capable backend.  This module does
not claim that a lightweight Python parser can losslessly convert arbitrary 3D
DWG content.  Instead it adapts a deployment-owned DWG/DXF conversion backend
to BIMAP's provider-neutral ``ModelConverter`` port.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from ...app.ports.model_conversion import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from . import _materialize_stream, _normalize_output_stem
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP DWG/DXF Model Converter")
printer = PrettyPrinter()
_COMPONENT = "dwg_dxf_model_converter"
_ALLOWED_SOURCE_FORMATS = frozenset({ModelSourceFormat.parse("DWG"), ModelSourceFormat.parse("DXF")})


@runtime_checkable
class DwgDxfConversionBackend(Protocol):
    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        ...

    def inspect_file(self, path: Path, *, source_format: ModelSourceFormat) -> ModelSourceInspection:
        ...

    def convert_file(
        self,
        path: Path,
        *,
        source_format: ModelSourceFormat,
        target_format: ModelTargetFormat,
        output_stem: str,
    ) -> ConvertedModelArtifact: ...


class DwgDxfModelConverter(ModelConverter):
    """Adapt one real CAD backend to BIMAP's ModelConverter contract."""

    __slots__ = ("_backend", "_capabilities", "_by_format")

    def __init__(self, backend: DwgDxfConversionBackend) -> None:
        announce_app_action(
            printer, logger,
            component=_COMPONENT,
            action="Initializing DWG/DXF model converter",
            event="dwg_dxf_model_converter_init_start",
        )
        missing = tuple(
            name for name in ("inspect_file", "convert_file")
            if not callable(getattr(backend, name, None))
        )
        if not hasattr(backend, "capabilities"):
            missing = ("capabilities", *missing)
        if missing:
            raise AppConfigurationError(
                "DWG/DXF conversion backend does not satisfy the required contract.",
                component=_COMPONENT,
                operation="initialize",
                field="backend",
                context={"received_type": type(backend).__name__, "missing_members": missing},
            )

        try:
            raw = tuple(backend.capabilities)
        except Exception as exc:
            raise AppConfigurationError(
                "DWG/DXF backend capabilities could not be read.",
                component=_COMPONENT,
                operation="initialize",
                field="backend.capabilities",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        if not raw:
            raise AppConfigurationError(
                "DWG/DXF conversion backend advertises no capabilities.",
                component=_COMPONENT,
                operation="initialize",
                field="backend.capabilities",
            )

        by_format: dict[ModelSourceFormat, ModelConversionCapability] = {}
        normalized: list[ModelConversionCapability] = []
        for index, capability in enumerate(raw):
            if not isinstance(capability, ModelConversionCapability):
                raise AppConfigurationError(
                    "DWG/DXF backend capability must be ModelConversionCapability.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}]",
                    context={"received_type": type(capability).__name__},
                )
            source = ModelSourceFormat.parse(capability.source_format)
            if source not in _ALLOWED_SOURCE_FORMATS:
                raise AppConfigurationError(
                    "DWG/DXF backend may advertise DWG/DXF sources only.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}].source_format",
                    context={"source_format": source.value},
                )
            if source in by_format:
                raise AppConfigurationError(
                    "DWG/DXF backend advertises a duplicate source format.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="backend.capabilities",
                    context={"source_format": source.value},
                )
            expected_extension = f".{source.value}"
            if expected_extension not in capability.extensions:
                raise AppConfigurationError(
                    "CAD capability must advertise its canonical source extension.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}].extensions",
                    context={"required_extension": expected_extension},
                )
            by_format[source] = capability
            normalized.append(capability)

        self._backend = backend
        self._by_format = by_format
        self._capabilities = tuple(sorted(normalized, key=lambda item: ModelSourceFormat.parse(item.source_format).value))

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        return self._capabilities

    def _require_source(self, value: ModelSourceFormat | str, *, operation: str):
        source = ModelSourceFormat.parse(value)
        capability = self._by_format.get(source)
        if capability is None:
            raise UnsupportedAppInputError(
                "Configured CAD backend does not support this source format.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={"source_format": source.value},
            )
        return source, capability

    def inspect(self, stream: BinaryIO, *, source_format: ModelSourceFormat) -> ModelSourceInspection:
        source, _ = self._require_source(source_format, operation="inspect")
        with tempfile.TemporaryDirectory(prefix="bimap-cad-inspect-") as directory_name:
            source_path = Path(directory_name) / f"source.{source.value}"
            _materialize_stream(stream, source_path, component=_COMPONENT, operation="materialize_source")
            try:
                inspection = self._backend.inspect_file(source_path, source_format=source)
            except AppError:
                raise
            except Exception as exc:
                raise AppIntegrityError(
                    "CAD inspection failed outside the BIMAP application-error contract.",
                    component=_COMPONENT,
                    operation="inspect",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc
        if not isinstance(inspection, ModelSourceInspection):
            raise AppIntegrityError(
                "CAD backend returned an invalid inspection result.",
                component=_COMPONENT,
                operation="inspect",
                field="inspection",
                context={"received_type": type(inspection).__name__},
            )
        if inspection.source_format is not source:
            raise AppIntegrityError(
                "CAD backend inspection source format does not match the request.",
                component=_COMPONENT,
                operation="inspect",
                field="inspection.source_format",
            )
        return inspection

    def convert(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
        target_format: ModelTargetFormat,
        output_stem: str,
    ) -> ConvertedModelArtifact:
        source, capability = self._require_source(source_format, operation="convert")
        target = ModelTargetFormat.parse(target_format)
        stem = _normalize_output_stem(output_stem, component=_COMPONENT, operation="convert")
        if target not in capability.target_formats:
            raise UnsupportedAppInputError(
                "Configured CAD backend does not support the requested target format.",
                component=_COMPONENT,
                operation="convert",
                field="target_format",
                context={"source_format": source.value, "target_format": target.value},
            )
        with tempfile.TemporaryDirectory(prefix="bimap-cad-convert-") as directory_name:
            source_path = Path(directory_name) / f"source.{source.value}"
            _materialize_stream(stream, source_path, component=_COMPONENT, operation="materialize_source")
            try:
                artifact = self._backend.convert_file(
                    source_path,
                    source_format=source,
                    target_format=target,
                    output_stem=stem,
                )
            except AppError:
                raise
            except Exception as exc:
                raise AppIntegrityError(
                    "CAD conversion failed outside the BIMAP application-error contract.",
                    component=_COMPONENT,
                    operation="convert",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc
        if not isinstance(artifact, ConvertedModelArtifact):
            raise AppIntegrityError(
                "CAD backend returned an invalid conversion artifact.",
                component=_COMPONENT,
                operation="convert",
                field="artifact",
                context={"received_type": type(artifact).__name__},
            )
        logger.info({
            "event": "dwg_dxf_model_conversion_completed",
            "source_format": source.value,
            "target_format": target.value,
            "size_bytes": artifact.size_bytes,
        })
        return artifact


__all__ = ["DwgDxfConversionBackend", "DwgDxfModelConverter"]


if __name__ == "__main__":
    print("\n=== Running DWG/DXF Converter Self-Test ===\n")
    printer.status("TEST", "DWG/DXF converter module initialized", "info")
    assert issubclass(DwgDxfModelConverter, ModelConverter)
    printer.status("PASS", "DWG/DXF adapter contract", "success")
    print("\n=== Test ran successfully ===\n")
