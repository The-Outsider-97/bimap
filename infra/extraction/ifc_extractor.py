"""
IfcOpenShell-backed IFC structured data extractor for BIMAP.

The adapter owns IFC-specific parsing only.  It emits the provider-neutral
``DataSourceInspection``/``ExtractedModelData`` contracts consumed by the
application layer.
"""

from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO

import ifcopenshell  # type: ignore
import ifcopenshell.util.element as ifc_element  # type: ignore

from ...app.ports.data_extraction import *
from ...app.utils.app_errors import *
from ...app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP IFC Data Extractor")
printer = PrettyPrinter()

_COMPONENT = "ifc_data_extractor"
_COPY_CHUNK_BYTES = 1024 * 1024

_SUPPORTED_DATASETS = (
    ExtractionDataset.ELEMENTS,
    ExtractionDataset.PROPERTIES,
    ExtractionDataset.QUANTITIES,
    ExtractionDataset.MATERIALS,
)


def _entity_id(entity: Any) -> int | None:
    try:
        value = entity.id()
    except Exception:
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _entity_class(entity: Any) -> str | None:
    try:
        value = entity.is_a()
    except Exception:
        return None
    return str(value) if value else None


def _entity_name(entity: Any) -> str | None:
    for attribute in ("Name", "LayerSetName", "ProfileSetName"):
        value = getattr(entity, attribute, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]

    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }

    wrapped = getattr(value, "wrappedValue", None)
    if wrapped is not None and wrapped is not value:
        return _json_value(wrapped)

    entity_class = _entity_class(value)
    if entity_class is not None:
        return {
            "source_class": entity_class,
            "source_id": _entity_id(value),
            "name": _entity_name(value),
        }

    return str(value)


def _project_record(model: Any) -> dict[str, Any]:
    projects = tuple(model.by_type("IfcProject"))
    if not projects:
        return {}

    project = projects[0]
    return {
        "source_id": _entity_id(project),
        "global_id": getattr(project, "GlobalId", None),
        "name": getattr(project, "Name", None),
        "description": getattr(project, "Description", None),
        "phase": getattr(project, "Phase", None),
    }


def _units(model: Any) -> tuple[dict[str, Any], ...]:
    projects = tuple(model.by_type("IfcProject"))
    if not projects:
        return ()

    assignment = getattr(projects[0], "UnitsInContext", None)
    raw_units = getattr(assignment, "Units", None)
    if not raw_units:
        return ()

    result: list[dict[str, Any]] = []
    for unit in raw_units:
        record: dict[str, Any] = {
            "source_class": _entity_class(unit),
            "unit_type": getattr(unit, "UnitType", None),
            "name": getattr(unit, "Name", None),
            "prefix": getattr(unit, "Prefix", None),
        }

        conversion_factor = getattr(unit, "ConversionFactor", None)
        if conversion_factor is not None:
            value_component = getattr(
                conversion_factor,
                "ValueComponent",
                None,
            )
            unit_component = getattr(
                conversion_factor,
                "UnitComponent",
                None,
            )
            record["conversion_factor"] = {
                "value": _json_value(value_component),
                "unit": (
                    _entity_name(unit_component)
                    or _entity_class(unit_component)
                ),
            }

        result.append(_json_value(record))

    return tuple(result)


def _element_identity(element: Any) -> dict[str, Any]:
    type_entity = ifc_element.get_type(element)
    container = ifc_element.get_container(element)

    return {
        "source_id": _entity_id(element),
        "global_id": getattr(element, "GlobalId", None),
        "source_class": _entity_class(element),
        "name": getattr(element, "Name", None),
        "description": getattr(element, "Description", None),
        "object_type": getattr(element, "ObjectType", None),
        "tag": getattr(element, "Tag", None),
        "predefined_type": ifc_element.get_predefined_type(element),
        "type": (
            None
            if type_entity is None
            else {
                "source_id": _entity_id(type_entity),
                "global_id": getattr(type_entity, "GlobalId", None),
                "source_class": _entity_class(type_entity),
                "name": _entity_name(type_entity),
            }
        ),
        "container": (
            None
            if container is None
            else {
                "source_id": _entity_id(container),
                "global_id": getattr(container, "GlobalId", None),
                "source_class": _entity_class(container),
                "name": _entity_name(container),
            }
        ),
    }


def _flatten_psets(
    element: Any,
    *,
    quantities: bool,
) -> list[dict[str, Any]]:
    psets = ifc_element.get_psets(
        element,
        psets_only=not quantities,
        qtos_only=quantities,
        should_inherit=True,
        verbose=False,
    )

    rows: list[dict[str, Any]] = []
    identity = {
        "element_id": _entity_id(element),
        "element_global_id": getattr(element, "GlobalId", None),
        "source_class": _entity_class(element),
        "element_name": getattr(element, "Name", None),
    }

    for set_name in sorted(psets):
        values = psets[set_name]
        if not isinstance(values, dict):
            continue

        set_id = values.get("id")
        property_names = sorted(
            key
            for key in values
            if key != "id"
        )
        for property_name in property_names:
            rows.append(
                {
                    **identity,
                    "set_name": str(set_name),
                    "set_id": _json_value(set_id),
                    "name": str(property_name),
                    "value": _json_value(values[property_name]),
                }
            )

    return rows


def _material_rows(element: Any) -> list[dict[str, Any]]:
    identity = {
        "element_id": _entity_id(element),
        "element_global_id": getattr(element, "GlobalId", None),
        "source_class": _entity_class(element),
        "element_name": getattr(element, "Name", None),
    }

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str | None, str | None]] = set()

    for material in ifc_element.get_materials(
        element,
        should_inherit=True,
    ):
        material_id = _entity_id(material)
        material_class = _entity_class(material)
        material_name = _entity_name(material)
        key = (
            material_id,
            material_class,
            material_name,
        )

        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                **identity,
                "material_id": material_id,
                "material_class": material_class,
                "material_name": material_name,
                "category": getattr(material, "Category", None),
                "description": getattr(
                    material,
                    "Description",
                    None,
                ),
            }
        )

    return rows


class IfcOpenShellDataExtractor(DataExtractor):
    """Extract IFC metadata without converting IFC geometry."""

    _CAPABILITY = DataExtractionCapability(
        source_format=ExtractionSourceFormat.IFC,
        extensions=(".ifc",),
        datasets=_SUPPORTED_DATASETS,
        package_content_type="application/zip",
        package_extension=".zip",
        included_artifacts=("pdf", "json"),
    )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return (self._CAPABILITY,)

    @staticmethod
    def _copy_stream(
        stream: BinaryIO,
        destination: Path,
    ) -> None:
        source = require_binary_stream(
            stream,
            field="source",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="copy_stream",
        )

        try:
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise UnsupportedAppInputError(
                "IFC source stream must be seekable.",
                component=_COMPONENT,
                operation="copy_stream",
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
                        "IFC source stream yielded non-binary data.",
                        component=_COMPONENT,
                        operation="copy_stream",
                        field="source",
                    )

                target.write(bytes(chunk))

    @staticmethod
    def _open_model(path: Path) -> Any:
        try:
            return ifcopenshell.open(str(path))
        except Exception as exc:
            raise AppValidationError(
                "IfcOpenShell could not parse the uploaded IFC model.",
                component=_COMPONENT,
                operation="open_ifc",
                field="source",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

    @staticmethod
    def _close_model(model: Any) -> None:
        close = getattr(model, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _inspection(model: Any) -> DataSourceInspection:
        schema = str(
            getattr(model, "schema", "") or ""
        ).strip()
        products = tuple(model.by_type("IfcProduct"))
        projects = tuple(model.by_type("IfcProject"))

        project_name = None
        if projects:
            value = getattr(projects[0], "Name", None)
            if isinstance(value, str) and value.strip():
                project_name = value.strip()

        if not schema:
            raise AppIntegrityError(
                "Parsed IFC model did not expose a schema identifier.",
                component=_COMPONENT,
                operation="inspect",
                field="schema",
            )

        if not products:
            raise AppValidationError(
                "IFC model contains no IfcProduct instances to extract.",
                component=_COMPONENT,
                operation="inspect",
                field="source",
            )

        return DataSourceInspection(
            source_format=ExtractionSourceFormat.IFC,
            schema=schema,
            product_count=len(products),
            project_name=project_name,
        )

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Inspecting IFC source",
            event="ifc_data_extractor_inspect_start",
        )
        source = ExtractionSourceFormat.parse(source_format)

        if source is not ExtractionSourceFormat.IFC:
            raise UnsupportedAppInputError(
                "IfcOpenShell extractor supports IFC sources only.",
                component=_COMPONENT,
                operation="inspect",
                field="source_format",
            )

        with tempfile.TemporaryDirectory(
            prefix="bimap-ifc-extract-"
        ) as directory_name:
            source_path = Path(directory_name) / "source.ifc"
            self._copy_stream(stream, source_path)
            model = self._open_model(source_path)
            try:
                return self._inspection(model)
            finally:
                self._close_model(model)

    def extract(
        self,
        stream: BinaryIO,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Extracting IFC model data",
            event="ifc_data_extractor_extract_start",
        )

        source = ExtractionSourceFormat.parse(source_format)
        selected = normalize_datasets(list(datasets))

        if source is not ExtractionSourceFormat.IFC:
            raise UnsupportedAppInputError(
                "IfcOpenShell extractor supports IFC sources only.",
                component=_COMPONENT,
                operation="extract",
                field="source_format",
            )

        unsupported = tuple(
            dataset.value
            for dataset in selected
            if dataset not in _SUPPORTED_DATASETS
        )
        if unsupported:
            raise UnsupportedAppInputError(
                "Requested dataset is not supported for IFC extraction.",
                component=_COMPONENT,
                operation="extract",
                field="datasets",
                context={"unsupported": unsupported},
            )

        with tempfile.TemporaryDirectory(
            prefix="bimap-ifc-extract-"
        ) as directory_name:
            source_path = Path(directory_name) / "source.ifc"
            self._copy_stream(stream, source_path)
            model = self._open_model(source_path)

            try:
                inspection = self._inspection(model)
                products = tuple(model.by_type("IfcProduct"))

                class_counts = Counter(
                    _entity_class(product) or "Unknown"
                    for product in products
                )

                extracted: dict[
                    str,
                    tuple[dict[str, Any], ...],
                ] = {}
                counts: dict[str, int] = {}

                if ExtractionDataset.ELEMENTS in selected:
                    rows = tuple(
                        _json_value(
                            _element_identity(product)
                        )
                        for product in products
                    )
                    extracted[
                        ExtractionDataset.ELEMENTS.value
                    ] = rows
                    counts[
                        ExtractionDataset.ELEMENTS.value
                    ] = len(rows)

                if ExtractionDataset.PROPERTIES in selected:
                    property_rows: list[
                        dict[str, Any]
                    ] = []
                    for product in products:
                        property_rows.extend(
                            _flatten_psets(
                                product,
                                quantities=False,
                            )
                        )
                    rows = tuple(
                        _json_value(row)
                        for row in property_rows
                    )
                    extracted[
                        ExtractionDataset.PROPERTIES.value
                    ] = rows
                    counts[
                        ExtractionDataset.PROPERTIES.value
                    ] = len(rows)

                if ExtractionDataset.QUANTITIES in selected:
                    quantity_rows: list[
                        dict[str, Any]
                    ] = []
                    for product in products:
                        quantity_rows.extend(
                            _flatten_psets(
                                product,
                                quantities=True,
                            )
                        )
                    rows = tuple(
                        _json_value(row)
                        for row in quantity_rows
                    )
                    extracted[
                        ExtractionDataset.QUANTITIES.value
                    ] = rows
                    counts[
                        ExtractionDataset.QUANTITIES.value
                    ] = len(rows)

                if ExtractionDataset.MATERIALS in selected:
                    material_rows: list[
                        dict[str, Any]
                    ] = []
                    for product in products:
                        material_rows.extend(
                            _material_rows(product)
                        )
                    rows = tuple(
                        _json_value(row)
                        for row in material_rows
                    )
                    extracted[
                        ExtractionDataset.MATERIALS.value
                    ] = rows
                    counts[
                        ExtractionDataset.MATERIALS.value
                    ] = len(rows)

                logger.info(
                    {
                        "event": "ifc_data_extraction_completed",
                        "schema": inspection.schema,
                        "product_count": inspection.product_count,
                        "selected_datasets": tuple(
                            item.value
                            for item in selected
                        ),
                        "row_counts": dict(counts),
                    }
                )

                return ExtractedModelData(
                    inspection=inspection,
                    project=_json_value(
                        _project_record(model)
                    ),
                    units=tuple(_units(model)),
                    datasets=extracted,
                    counts=counts,
                    # Compatibility field in the current application contract.
                    # A later contract migration should rename this to
                    # ``class_counts`` for all source formats.
                    ifc_class_counts=dict(
                        sorted(class_counts.items())
                    ),
                )
            finally:
                self._close_model(model)


__all__ = ["IfcOpenShellDataExtractor"]


if __name__ == "__main__":
    print("\n=== Running IFC Data Extractor Self-Test ===\n")
    printer.status("TEST", "IFC extractor initialized", "info")
    extractor = IfcOpenShellDataExtractor()
    assert extractor.capabilities[0].source_format is ExtractionSourceFormat.IFC
    assert extractor.capabilities[0].extensions == (".ifc",)
    printer.status("PASS", "IFC capability contract", "success")
    print("\n=== Test ran successfully ===\n")
