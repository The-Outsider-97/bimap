"""
Portable Revit-family metadata extractor for BIMAP local development.

Purpose
-------
This adapter makes simple .rfa Family Audit execution possible when the
deployment has no native Autodesk Revit worker configured.

It reads only the documented/observable PartAtom metadata stored in the RFA
compound document.  It deliberately does NOT claim to provide native Revit
state such as:

- FamilyParameter.Formula;
- shared-parameter GUID/scope metadata;
- connectors;
- nested-family topology;
- native solid geometry;
- tessellated GLB geometry.

Those sections remain not-assessed so deterministic rules resolve to UNKNOWN
rather than fabricating evidence.

A native Revit backend remains authoritative whenever configured.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, BinaryIO, Final
from xml.etree import ElementTree as ET

from ...app.ports.data_extraction import (
    DataExtractionCapability,
    DataExtractor,
    DataSourceInspection,
    ExtractedModelData,
    ExtractionDataset,
    ExtractionSourceFormat,
)
from ...app.utils.app_errors import (
    AppConfigurationError,
    AppIntegrityError,
    AppValidationError,
    UnsupportedAppInputError,
)
from ...app.utils.app_helpers import require_binary_stream
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP PartAtom RFA Data Extractor")
printer = PrettyPrinter()

_COMPONENT: Final[str] = "partatom_rfa_data_extractor"
_PARTATOM_STREAM: Final[str] = "PartAtom"
_MAX_SOURCE_BYTES: Final[int] = 256 * 1024 * 1024

_ATOM_NS: Final[str] = "http://www.w3.org/2005/Atom"
_PART_NS: Final[str] = "urn:schemas-autodesk-com:partatom"

_CAPABILITY = DataExtractionCapability(
    source_format=ExtractionSourceFormat.RFA,
    extensions=(".rfa",),
    # Only type-catalog evidence is advertised generically. Parameter values
    # are retained inside type rows because PartAtom cannot prove parameter
    # scope/shared/formula metadata required by the canonical parameters section.
    datasets=(ExtractionDataset.ELEMENTS,),
)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _child(element: ET.Element, local_name: str) -> ET.Element | None:
    for candidate in element:
        if _local_name(candidate.tag) == local_name:
            return candidate
    return None


def _children(element: ET.Element, local_name: str) -> tuple[ET.Element, ...]:
    return tuple(
        candidate
        for candidate in element
        if _local_name(candidate.tag) == local_name
    )


def _read_stream(stream: BinaryIO) -> bytes:
    source = require_binary_stream(
        stream,
        field="source",
        error_type=UnsupportedAppInputError,
        component=_COMPONENT,
        operation="read_source",
    )
    try:
        source.seek(0)
    except (AttributeError, OSError) as exc:
        raise UnsupportedAppInputError(
            "RFA source stream must be seekable.",
            component=_COMPONENT,
            operation="read_source",
            field="source",
            cause=exc,
        ) from exc

    payload = source.read(_MAX_SOURCE_BYTES + 1)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise UnsupportedAppInputError(
            "RFA source stream yielded non-binary data.",
            component=_COMPONENT,
            operation="read_source",
            field="source",
        )

    data = bytes(payload)
    if not data:
        raise AppValidationError(
            "RFA source cannot be empty.",
            component=_COMPONENT,
            operation="read_source",
            field="source",
        )
    if len(data) > _MAX_SOURCE_BYTES:
        raise AppValidationError(
            "RFA source exceeds the PartAtom fallback size limit.",
            component=_COMPONENT,
            operation="read_source",
            field="source",
            context={
                "max_source_bytes": _MAX_SOURCE_BYTES,
                "received_at_least_bytes": len(data),
            },
        )
    return data


def _ole_module():
    try:
        import olefile  # type: ignore
    except ImportError as exc:
        raise AppConfigurationError(
            "Portable RFA inspection requires the 'olefile' package.",
            component=_COMPONENT,
            operation="load_dependency",
            field="olefile",
            cause=exc,
        ) from exc
    return olefile


def _partatom_xml(payload: bytes) -> bytes:
    olefile = _ole_module()
    try:
        compound = olefile.OleFileIO(BytesIO(payload))
    except Exception as exc:
        raise AppValidationError(
            "RFA source is not a readable Revit compound document.",
            component=_COMPONENT,
            operation="open_rfa",
            field="source",
            context={"error_type": type(exc).__name__},
            cause=exc,
        ) from exc

    try:
        if not compound.exists(_PARTATOM_STREAM):
            raise AppValidationError(
                "RFA source does not contain a PartAtom metadata stream.",
                component=_COMPONENT,
                operation="read_partatom",
                field="source",
            )

        try:
            raw = compound.openstream(_PARTATOM_STREAM).read()
        except Exception as exc:
            raise AppIntegrityError(
                "RFA PartAtom stream could not be read.",
                component=_COMPONENT,
                operation="read_partatom",
                field="PartAtom",
                cause=exc,
            ) from exc
    finally:
        compound.close()

    if not raw:
        raise AppIntegrityError(
            "RFA PartAtom stream is empty.",
            component=_COMPONENT,
            operation="read_partatom",
            field="PartAtom",
        )
    return bytes(raw)


def _parse_partatom(payload: bytes) -> dict[str, Any]:
    raw = _partatom_xml(payload)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise AppIntegrityError(
            "RFA PartAtom metadata is not valid XML.",
            component=_COMPONENT,
            operation="parse_partatom",
            field="PartAtom",
            cause=exc,
        ) from exc

    title = _text(_child(root, "title"))
    updated = _text(_child(root, "updated"))

    grouping_category: str | None = None
    omniclass_number: str | None = None

    for category in _children(root, "category"):
        term = _text(_child(category, "term"))
        scheme = (category.attrib.get("scheme") or "").strip().casefold()
        if not term:
            continue
        if scheme == "adsk:revit:grouping":
            grouping_category = term
        elif scheme == "std:oc1":
            omniclass_number = term

    design_file: ET.Element | None = None
    for link in _children(root, "link"):
        if (link.attrib.get("type") or "").strip().casefold() == "application/rfa":
            design_file = _child(link, "design-file")
            if design_file is not None:
                break

    source_revit_version = None
    source_filename = None
    product = None
    design_updated = None
    if design_file is not None:
        source_filename = _text(_child(design_file, "title"))
        product = _text(_child(design_file, "product"))
        source_revit_version = _text(_child(design_file, "product-version"))
        design_updated = _text(_child(design_file, "updated"))

    family = _child(root, "family")
    variation_count: int | None = None
    types: list[dict[str, Any]] = []
    unit_names: set[str] = set()

    if family is not None:
        raw_count = _text(_child(family, "variationCount"))
        if raw_count:
            try:
                variation_count = int(raw_count)
            except ValueError:
                variation_count = None

        for part_index, part in enumerate(_children(family, "part")):
            type_name = _text(_child(part, "title"))
            values: list[dict[str, Any]] = []

            for field in part:
                field_name = _local_name(field.tag)
                if field_name == "title":
                    continue

                value = _text(field)
                if value is None:
                    continue

                display_name = (
                    field.attrib.get("displayName")
                    or field.attrib.get("display-name")
                    or field_name
                )
                units = (field.attrib.get("units") or "").strip() or None
                if units:
                    unit_names.add(units)

                values.append(
                    {
                        "name": display_name,
                        "xml_name": field_name,
                        "value": value,
                        **({"units": units} if units else {}),
                    }
                )

            # Keep unnamed types visible instead of silently fabricating names.
            row: dict[str, Any] = {
                "partatom_index": part_index,
                "type_name": type_name,
                "name": type_name,
                "parameter_values": values,
            }
            if grouping_category:
                row["category"] = grouping_category
            types.append(row)

    identity = {
        "family_name": title,
        "category": grouping_category,
        "source_filename": source_filename,
        "source_revit_version": source_revit_version,
        "omniclass_number": omniclass_number,
        "variation_count": (
            variation_count
            if variation_count is not None
            else len(types)
        ),
    }

    # Remove only absent optional values. Do not infer unsupported metadata.
    identity = {
        key: value
        for key, value in identity.items()
        if value is not None
    }

    documentation = [
        {
            "kind": "partatom",
            "product": product,
            "updated": design_updated or updated,
            "source": "RFA PartAtom metadata",
        }
    ]

    return {
        "family_name": title,
        "category": grouping_category,
        "source_revit_version": source_revit_version,
        "omniclass_number": omniclass_number,
        "variation_count": (
            variation_count
            if variation_count is not None
            else len(types)
        ),
        "types": types,
        "identity": identity,
        "documentation": documentation,
        "units": sorted(unit_names),
    }


class PartAtomRfaDataExtractor(DataExtractor):
    """Conservative RFA metadata extractor used only when no native worker exists."""

    __slots__ = ()

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return (_CAPABILITY,)

    @staticmethod
    def _require_rfa(
        source_format: ExtractionSourceFormat | str,
        *,
        operation: str,
    ) -> ExtractionSourceFormat:
        source = ExtractionSourceFormat.parse(source_format)
        if source is not ExtractionSourceFormat.RFA:
            raise UnsupportedAppInputError(
                "PartAtom fallback supports RFA sources only.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={"received": source.value},
            )
        return source

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        self._require_rfa(source_format, operation="inspect")
        parsed = _parse_partatom(_read_stream(stream))
        version = parsed.get("source_revit_version")
        schema = (
            f"Revit {version} PartAtom"
            if isinstance(version, str) and version.strip()
            else "Revit RFA PartAtom"
        )
        return DataSourceInspection(
            source_format=ExtractionSourceFormat.RFA,
            schema=schema,
            product_count=max(int(parsed.get("variation_count") or 0), 1),
            project_name=parsed.get("family_name"),
        )

    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        self._require_rfa(source_format, operation="extract")

        selected = tuple(ExtractionDataset.parse(item) for item in datasets)
        unsupported = tuple(
            item.value
            for item in selected
            if item is not ExtractionDataset.ELEMENTS
        )
        if unsupported:
            raise UnsupportedAppInputError(
                "PartAtom fallback can provide family type-catalog evidence only.",
                component=_COMPONENT,
                operation="extract",
                field="datasets",
                context={"unsupported": unsupported},
            )

        parsed = _parse_partatom(_read_stream(stream))
        types = tuple(dict(row) for row in parsed["types"])
        version = parsed.get("source_revit_version")
        schema = (
            f"Revit {version} PartAtom"
            if isinstance(version, str) and version.strip()
            else "Revit RFA PartAtom"
        )

        inspection = DataSourceInspection(
            source_format=ExtractionSourceFormat.RFA,
            schema=schema,
            product_count=max(int(parsed.get("variation_count") or 0), 1),
            project_name=parsed.get("family_name"),
        )

        unit_rows = tuple(
            {"symbol": unit, "source": "PartAtom"}
            for unit in parsed["units"]
        )

        extension = {
            "schema_version": "1.0.0",
            "extractor_version": "partatom-fallback-1.0.0",
            "source_revit_version": parsed.get("source_revit_version"),
            # Critical: only sections actually established by PartAtom are
            # marked assessed. All native-only sections remain not-assessed.
            "sections_assessed": [
                "identity",
                "type_catalog",
                "documentation",
            ],
            "identity": dict(parsed["identity"]),
            "type_catalog": [dict(row) for row in types],
            "parameters": [],
            "formulas": [],
            "materials": [],
            "connectors": [],
            "nested_components": [],
            "geometry_metrics": [],
            "documentation": [
                dict(row)
                for row in parsed["documentation"]
            ],
        }

        return ExtractedModelData(
            inspection=inspection,
            project={
                "family_name": parsed.get("family_name"),
                "category": parsed.get("category"),
                "omniclass_number": parsed.get("omniclass_number"),
                "source_revit_version": parsed.get("source_revit_version"),
                "extraction_mode": "partatom_metadata_fallback",
            },
            units=unit_rows,
            datasets={"elements": types},
            counts={
                "elements": len(types),
                "properties": 0,
                "quantities": 0,
                "materials": 0,
            },
            ifc_class_counts={},
            geometry_summary={},
            extensions={"revit_family": extension},
        )

    def render_preview(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> bytes | None:
        # No raster or geometry claim is made by the portable fallback.
        del stream
        self._require_rfa(source_format, operation="render_preview")
        return None


__all__ = ["PartAtomRfaDataExtractor"]
