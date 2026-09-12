"""
Concrete multi-format data-extraction infrastructure for BIMAP.

This package contains source-specific adapters behind the provider-neutral
``DataExtractor`` application port.  The application and audit layers should
depend on the port/capability contracts, never on these concrete adapters.

``MultiFormatDataExtractor`` is the deployment-level dispatcher.  It has no
default source format: a caller supplies the already-resolved
``ExtractionSourceFormat`` and the dispatcher forwards the operation to the
single configured adapter that advertises that source format.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import BinaryIO

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Multi-Format Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "multi_format_data_extractor"


class MultiFormatDataExtractor(DataExtractor):
    """
    Dispatch extraction to exactly one adapter per advertised source format.

    The dispatcher deliberately does not infer a fallback/default format.
    Source-format resolution belongs to the application service, which should
    resolve the uploaded filename against ``capabilities``.
    """

    __slots__ = ("_extractors", "_by_format", "_capabilities")

    def __init__(self, *extractors: DataExtractor) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing multi-format data extractor",
            event="multi_format_data_extractor_init_start",
            context={"extractor_count": len(extractors)},
        )

        if not extractors:
            raise AppConfigurationError(
                "At least one data extractor must be configured.",
                component=_COMPONENT,
                operation="initialize",
                field="extractors",
            )

        normalized: list[DataExtractor] = []
        by_format: dict[ExtractionSourceFormat, DataExtractor] = {}
        capabilities: list[DataExtractionCapability] = []

        for index, extractor in enumerate(extractors):
            if not isinstance(extractor, DataExtractor):
                raise AppConfigurationError(
                    "Every configured extractor must implement DataExtractor.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"extractors[{index}]",
                    context={"received_type": type(extractor).__name__},
                )

            adapter_capabilities = tuple(extractor.capabilities)
            if not adapter_capabilities:
                raise AppConfigurationError(
                    "Configured extractor advertises no capabilities.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"extractors[{index}].capabilities",
                    context={"extractor_type": type(extractor).__name__},
                )

            for capability in adapter_capabilities:
                if not isinstance(capability, DataExtractionCapability):
                    raise AppConfigurationError(
                        "Extractor capability must be DataExtractionCapability.",
                        component=_COMPONENT,
                        operation="initialize",
                        field=f"extractors[{index}].capabilities",
                        context={"received_type": type(capability).__name__},
                    )

                source_format = ExtractionSourceFormat.parse(
                    capability.source_format
                )
                owner = by_format.get(source_format)
                if owner is not None and owner is not extractor:
                    raise AppConfigurationError(
                        "Multiple data extractors advertise the same source format.",
                        component=_COMPONENT,
                        operation="initialize",
                        field="extractors",
                        context={
                            "source_format": source_format.value,
                            "first_extractor": type(owner).__name__,
                            "second_extractor": type(extractor).__name__,
                        },
                    )

                by_format[source_format] = extractor
                capabilities.append(capability)

            normalized.append(extractor)

        capabilities.sort(
            key=lambda item: (
                ExtractionSourceFormat.parse(item.source_format).value,
                item.extensions,
            )
        )

        self._extractors = tuple(normalized)
        self._by_format = by_format
        self._capabilities = tuple(capabilities)

        logger.info(
            {
                "event": "multi_format_data_extractor_initialized",
                "extractor_count": len(self._extractors),
                "source_formats": tuple(item.value for item in sorted(self._by_format, key=lambda value: value.value)
                ),
            }
        )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._capabilities

    def _resolve(
        self,
        source_format: ExtractionSourceFormat | str,
        *,
        operation: str,
    ) -> tuple[ExtractionSourceFormat, DataExtractor]:
        source = ExtractionSourceFormat.parse(source_format)
        extractor = self._by_format.get(source)
        if extractor is None:
            raise UnsupportedAppInputError(
                "No configured data extractor supports the selected source format.",
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
        return source, extractor

    def inspect(self, stream: BinaryIO, *, source_format: ExtractionSourceFormat) -> DataSourceInspection:
        source, extractor = self._resolve(source_format, operation="inspect")
        return extractor.inspect(stream, source_format=source)

    def extract(self, stream: BinaryIO, *, source_format: ExtractionSourceFormat,
                datasets: tuple[ExtractionDataset, ...]) -> ExtractedModelData:
        source, extractor = self._resolve(source_format, operation="extract")
        return extractor.extract(stream, source_format=source, datasets=datasets)

    def render_preview(self,stream: BinaryIO, *, source_format: ExtractionSourceFormat) -> bytes | None:
        source, extractor = self._resolve(source_format, operation="render_preview")
        return extractor.render_preview(stream, source_format=source)


from .ifc_extractor import IfcOpenShellDataExtractor
from .revit_extractor import RevitDataExtractor, RevitExtractionBackend
from .dwg_extractor import DwgDxfDataExtractor, DwgToDxfBackend
from .fbx_extractor import BlenderFbxDataExtractor
from .mesh_extractor import TrimeshDataExtractor


__all__ = [
    "MultiFormatDataExtractor",
    "IfcOpenShellDataExtractor",
    "RevitDataExtractor",
    "RevitExtractionBackend",
    "DwgDxfDataExtractor",
    "DwgToDxfBackend",
    "BlenderFbxDataExtractor",
    "TrimeshDataExtractor",
]


if __name__ == "__main__":
    print("\n=== Running BIMAP Extraction Package Self-Test ===\n")
    printer.status("TEST", "Extraction package initialized", "info")
    assert issubclass(MultiFormatDataExtractor, DataExtractor)
    printer.status("PASS", "Extraction dispatcher contract", "success")
    print("\n=== Test ran successfully ===\n")
