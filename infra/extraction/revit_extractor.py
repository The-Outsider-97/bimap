"""
Native-Revit extraction adapter boundary for BIMAP.

RVT/RFA are proprietary Autodesk formats.  This module therefore does not
pretend to parse them with an unrelated Python library.  Instead it adapts a
deployment-supplied native Revit extraction backend (for example a controlled
Revit/RevitCoreConsole worker or an Autodesk-hosted processing service) to the
provider-neutral BIMAP ``DataExtractor`` port.

Only a backend that actually advertises RVT/RFA capabilities is exposed to the
application layer.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Revit Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "revit_data_extractor"
_COPY_CHUNK_BYTES = 1024 * 1024


@runtime_checkable
class RevitExtractionBackend(Protocol):
    """
    Deployment-owned native Revit extraction backend.

    The backend receives a materialized local source path because native Revit
    processors generally operate on files rather than arbitrary Python streams.
    """

    @property
    def capabilities(
        self,
    ) -> tuple[DataExtractionCapability, ...]:
        ...

    def inspect_file(
        self,
        path: Path,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        ...

    def extract_file(
        self,
        path: Path,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        ...


class RevitDataExtractor(DataExtractor):
    """Adapt one native Revit backend to BIMAP's DataExtractor contract."""

    __slots__ = ("_backend", "_capabilities", "_supported_formats")

    def __init__(
        self,
        backend: RevitExtractionBackend,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing Revit data extractor",
            event="revit_data_extractor_init_start",
        )

        required_members = (
            "capabilities",
            "inspect_file",
            "extract_file",
        )
        missing = tuple(
            member
            for member in required_members
            if not hasattr(backend, member)
        )
        if missing:
            raise AppConfigurationError(
                "Revit extraction backend does not satisfy the required contract.",
                component=_COMPONENT,
                operation="initialize",
                field="backend",
                context={
                    "received_type": type(backend).__name__,
                    "missing_members": missing,
                },
            )

        raw_capabilities = tuple(backend.capabilities)
        if not raw_capabilities:
            raise AppConfigurationError(
                "Revit extraction backend advertises no capabilities.",
                component=_COMPONENT,
                operation="initialize",
                field="backend.capabilities",
            )

        allowed_formats = {
            ExtractionSourceFormat.parse("RVT"),
            ExtractionSourceFormat.parse("RFA"),
        }
        capabilities: list[DataExtractionCapability] = []
        supported_formats: set[ExtractionSourceFormat] = set()

        for index, capability in enumerate(raw_capabilities):
            if not isinstance(
                capability,
                DataExtractionCapability,
            ):
                raise AppConfigurationError(
                    "Revit backend capability must be DataExtractionCapability.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}]",
                    context={
                        "received_type": type(capability).__name__,
                    },
                )

            source = ExtractionSourceFormat.parse(
                capability.source_format
            )
            if source not in allowed_formats:
                raise AppConfigurationError(
                    "Revit backend may advertise RVT/RFA sources only.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"backend.capabilities[{index}].source_format",
                    context={"source_format": source.value},
                )

            if source in supported_formats:
                raise AppConfigurationError(
                    "Revit backend advertises a duplicate source format.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="backend.capabilities",
                    context={"source_format": source.value},
                )

            supported_formats.add(source)
            capabilities.append(capability)

        self._backend = backend
        self._capabilities = tuple(
            sorted(
                capabilities,
                key=lambda item: ExtractionSourceFormat.parse(
                    item.source_format
                ).value,
            )
        )
        self._supported_formats = frozenset(
            supported_formats
        )

        logger.info(
            {
                "event": "revit_data_extractor_initialized",
                "backend_type": type(backend).__name__,
                "source_formats": tuple(
                    item.value
                    for item in sorted(
                        self._supported_formats,
                        key=lambda value: value.value,
                    )
                ),
            }
        )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._capabilities

    def _require_supported(
        self,
        source_format: ExtractionSourceFormat | str,
        *,
        operation: str,
    ) -> ExtractionSourceFormat:
        source = ExtractionSourceFormat.parse(
            source_format
        )
        if source not in self._supported_formats:
            raise UnsupportedAppInputError(
                "Configured Revit backend does not support this source format.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={
                    "source_format": source.value,
                    "supported": tuple(
                        item.value
                        for item in sorted(
                            self._supported_formats,
                            key=lambda value: value.value,
                        )
                    ),
                },
            )
        return source

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
                "Revit source stream must be seekable.",
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
                        "Revit source stream yielded non-binary data.",
                        component=_COMPONENT,
                        operation="materialize_source",
                        field="source",
                    )
                target.write(bytes(chunk))

        if destination.stat().st_size <= 0:
            raise AppValidationError(
                "Revit source model cannot be empty.",
                component=_COMPONENT,
                operation="materialize_source",
                field="source",
            )

    def _capability_for(
        self,
        source: ExtractionSourceFormat,
    ) -> DataExtractionCapability:
        capability = next(
            (
                item
                for item in self._capabilities
                if item.source_format is source
            ),
            None,
        )
        if capability is None:
            raise AppIntegrityError(
                "Revit extractor capability map is inconsistent.",
                component=_COMPONENT,
                operation="resolve_capability",
                field="source_format",
                context={"source_format": source.value},
            )
        return capability

    @staticmethod
    def _validate_inspection(inspection: DataSourceInspection, *, source: ExtractionSourceFormat, operation: str) -> DataSourceInspection:
        if not isinstance(inspection, DataSourceInspection):
            raise AppIntegrityError(
                "Revit backend returned an invalid inspection result.",
                component=_COMPONENT,
                operation=operation,
                field="inspection",
                context={
                    "received_type": type(inspection).__name__,
                },
            )
        if inspection.source_format is not source:
            raise AppIntegrityError(
                "Revit backend returned an inspection for the wrong source format.",
                component=_COMPONENT,
                operation=operation,
                field="inspection.source_format",
                context={
                    "expected": source.value,
                    "received": getattr(inspection.source_format, "value", inspection.source_format),
                },
            )
        return inspection

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        source = self._require_supported(
            source_format,
            operation="inspect",
        )

        with tempfile.TemporaryDirectory(
            prefix="bimap-revit-extract-"
        ) as directory_name:
            suffix = f".{source.value}"
            source_path = (
                Path(directory_name)
                / f"source{suffix}"
            )
            self._materialize(stream, source_path)

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
            source=source,
            operation="inspect",
        )

    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        source = self._require_supported(
            source_format,
            operation="extract",
        )
        selected = normalize_datasets(list(datasets))
        capability = self._capability_for(source)

        unsupported = tuple(
            item.value
            for item in selected
            if item not in capability.datasets
        )
        if unsupported:
            raise UnsupportedAppInputError(
                "Requested dataset is not supported by the configured Revit backend.",
                component=_COMPONENT,
                operation="extract",
                field="datasets",
                context={"unsupported": unsupported},
            )

        with tempfile.TemporaryDirectory(
            prefix="bimap-revit-extract-"
        ) as directory_name:
            source_path = (
                Path(directory_name)
                / f"source.{source.value}"
            )
            self._materialize(stream, source_path)

            try:
                result = self._backend.extract_file(
                    source_path,
                    source_format=source,
                    datasets=selected,
                )
            except AppError:
                raise
            except Exception as exc:
                raise AppIntegrityError(
                    "Native Revit extraction failed outside the BIMAP application-error contract.",
                    component=_COMPONENT,
                    operation="extract",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc

        if not isinstance(result, ExtractedModelData):
            raise AppIntegrityError(
                "Revit backend returned an invalid extraction result.",
                component=_COMPONENT,
                operation="extract",
                field="result",
                context={
                    "received_type": type(result).__name__,
                },
            )

        self._validate_inspection(
            result.inspection,
            source=source,
            operation="extract",
        )
        return result


__all__ = [
    "RevitExtractionBackend",
    "RevitDataExtractor",
]


if __name__ == "__main__":
    print("\n=== Running Revit Data Extractor Self-Test ===\n")
    printer.status("TEST", "Revit adapter contract loaded", "info")
    assert hasattr(RevitExtractionBackend, "inspect_file")
    assert hasattr(RevitExtractionBackend, "extract_file")
    printer.status("PASS", "Revit backend protocol", "success")
    print("\n=== Test ran successfully ===\n")
