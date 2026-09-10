"""
Native-Revit conversion adapter boundary for BIMAP.

RVT/RFA are proprietary Autodesk formats.  This module therefore does not
pretend to decode them with an unrelated Python library.  It adapts a real,
deployment-owned Revit conversion backend (for example a controlled Revit /
RevitCoreConsole worker or an Autodesk-hosted processing service) to BIMAP's
provider-neutral ``ModelConverter`` application port.

The backend remains responsible for the actual native Revit operation and must
advertise only source/target pairs it genuinely supports.
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


logger = get_logger("BIMAP Revit Model Converter")
printer = PrettyPrinter()

_COMPONENT = "revit_model_converter"
_ALLOWED_SOURCE_FORMATS = frozenset(
    {
        ModelSourceFormat.parse("RVT"),
        ModelSourceFormat.parse("RFA"),
    }
)


def _format_value(value: ModelSourceFormat | str) -> str:
    return value.value if isinstance(value, ModelSourceFormat) else value


@runtime_checkable
class RevitConversionBackend(Protocol):
    """Deployment-owned native Revit conversion contract."""

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        ...

    def inspect_file(
        self,
        path: Path,
        *,
        source_format: ModelSourceFormat,
    ) -> ModelSourceInspection:
        ...

    def convert_file(
        self,
        path: Path,
        *,
        source_format: ModelSourceFormat,
        target_format: ModelTargetFormat,
        output_stem: str,
    ) -> ConvertedModelArtifact:
        ...


class RevitModelConverter(ModelConverter):
    """Adapt one native Revit backend to BIMAP's ModelConverter contract."""

    __slots__ = ("_backend", "_capabilities", "_by_format")

    def __init__(self, backend: RevitConversionBackend) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing Revit model converter",
            event="revit_model_converter_init_start",
        )

        required_callables = ("inspect_file", "convert_file")
        missing = tuple(
            name
            for name in required_callables
            if not callable(getattr(backend, name, None))
        )
        if not hasattr(backend, "capabilities"):
            missing = ("capabilities", *missing)
        if missing:
            raise AppConfigurationError(
                "Revit conversion backend does not satisfy the required contract.",
                component=_COMPONENT,
                operation="initialize",
                field="backend",
                context={
                    "received_type": type(backend).__name__,
                    "missing_members": missing,
                },
            )

        try:
            raw_capabilities = tuple(backend.capabilities)
        except Exception as exc:
            raise AppConfigurationError(
                "Revit conversion backend capabilities could not be read.",
                component=_COMPONENT,
                operation="initialize",
                field="backend.capabilities",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if not raw_capabilities:
            raise AppConfigurationError(
                "Revit conversion backend advertises no capabilities.",
                component=_COMPONENT,
                operation="initialize",
                field="backend.capabilities",
            )

        by_format: dict[ModelSourceFormat, ModelConversionCapability] = {}
        normalized: list[ModelConversionCapability] = []

        for index, capability in enumerate(raw_capabilities):
            if not isinstance(capability, ModelConversionCapability):
                raise AppConfigurationError(
                    "Revit backend capability must be ModelConversionCapability.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}]",
                    context={"received_type": type(capability).__name__},
                )

            source = ModelSourceFormat.parse(capability.source_format)
            if source not in _ALLOWED_SOURCE_FORMATS:
                raise AppConfigurationError(
                    "Revit backend may advertise RVT/RFA sources only.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}].source_format",
                    context={"source_format": _format_value(source)},
                )
            if source in by_format:
                raise AppConfigurationError(
                    "Revit backend advertises a duplicate source format.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="backend.capabilities",
                    context={"source_format": _format_value(source)},
                )

            expected_extension = f".{_format_value(source)}"
            if expected_extension not in capability.extensions:
                raise AppConfigurationError(
                    "Revit capability must advertise its canonical source extension.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}].extensions",
                    context={
                        "source_format": _format_value(source),
                        "required_extension": expected_extension,
                    },
                )

            by_format[source] = capability
            normalized.append(capability)

        self._backend = backend
        self._by_format = by_format
        self._capabilities = tuple(
            sorted(normalized, key=lambda item: _format_value(item.source_format))
        )

        logger.info(
            {
                "event": "revit_model_converter_initialized",
                "backend_type": type(backend).__name__,
                "source_formats": tuple(
                    _format_value(item.source_format) for item in self._capabilities
                ),
            }
        )

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        return self._capabilities

    def _require_source(
        self,
        source_format: ModelSourceFormat | str,
        *,
        operation: str,
    ) -> tuple[ModelSourceFormat, ModelConversionCapability]:
        source = ModelSourceFormat.parse(source_format)
        capability = self._by_format.get(source)
        if capability is None:
            raise UnsupportedAppInputError(
                "Configured Revit backend does not support this source format.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={
                    "source_format": _format_value(source),
                    "supported": tuple(
                        item.value
                        for item in sorted(
                            self._by_format,
                            key=_format_value,
                        )
                    ),
                },
            )
        return source, capability

    @staticmethod
    def _validate_inspection(
        inspection: ModelSourceInspection,
        *,
        expected_source: ModelSourceFormat,
        operation: str,
    ) -> ModelSourceInspection:
        if not isinstance(inspection, ModelSourceInspection):
            raise AppIntegrityError(
                "Revit backend returned an invalid inspection result.",
                component=_COMPONENT,
                operation=operation,
                field="inspection",
                context={"received_type": type(inspection).__name__},
            )
        if inspection.source_format is not expected_source:
            raise AppIntegrityError(
                "Revit backend inspection source format does not match the request.",
                component=_COMPONENT,
                operation=operation,
                field="inspection.source_format",
                context={
                    "expected": _format_value(expected_source),
                    "received": _format_value(inspection.source_format),
                },
            )
        return inspection

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
    ) -> ModelSourceInspection:
        source, _ = self._require_source(source_format, operation="inspect")

        with tempfile.TemporaryDirectory(prefix="bimap-revit-inspect-") as directory_name:
            source_path = Path(directory_name) / f"source.{_format_value(source)}"
            _materialize_stream(
                stream,
                source_path,
                component=_COMPONENT,
                operation="materialize_source",
            )
            try:
                inspection = self._backend.inspect_file(
                    source_path,
                    source_format=source,
                )
            except AppError:
                raise
            except Exception as exc:
                raise AppIntegrityError(
                    "Native Revit inspection failed outside the BIMAP application-error contract.",
                    component=_COMPONENT,
                    operation="inspect",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc

        return self._validate_inspection(
            inspection,
            expected_source=source,
            operation="inspect",
        )

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
        stem = _normalize_output_stem(
            output_stem,
            component=_COMPONENT,
            operation="convert",
        )
        if target not in capability.target_formats:
            raise UnsupportedAppInputError(
                "Configured Revit backend does not support the requested target format.",
                component=_COMPONENT,
                operation="convert",
                field="target_format",
                context={
                    "source_format": _format_value(source),
                    "target_format": target.value,
                    "supported": tuple(item.value for item in capability.target_formats), # type: ignore
                },
            )

        with tempfile.TemporaryDirectory(prefix="bimap-revit-convert-") as directory_name:
            source_path = Path(directory_name) / f"source.{_format_value(source)}"
            _materialize_stream(
                stream,
                source_path,
                component=_COMPONENT,
                operation="materialize_source",
            )
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
                    "Native Revit conversion failed outside the BIMAP application-error contract.",
                    component=_COMPONENT,
                    operation="convert",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc

        if not isinstance(artifact, ConvertedModelArtifact):
            raise AppIntegrityError(
                "Revit backend returned an invalid conversion artifact.",
                component=_COMPONENT,
                operation="convert",
                field="artifact",
                context={"received_type": type(artifact).__name__},
            )

        logger.info(
            {
                "event": "revit_model_conversion_completed",
                "source_format": source.value,
                "target_format": target.value,
                "size_bytes": artifact.size_bytes,
            }
        )
        return artifact


__all__ = ["RevitConversionBackend", "RevitModelConverter"]


if __name__ == "__main__":
    print("\n=== Running Revit Converter Self-Test ===\n")
    printer.status("TEST", "Revit converter module initialized", "info")
    assert issubclass(RevitModelConverter, ModelConverter)
    printer.status("PASS", "Revit adapter contract", "success")
    print("\n=== Test ran successfully ===\n")
