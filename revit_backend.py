"""
Deployment-owned native Revit extraction backend for BIMAP.

Location
--------
SLAI/deployment/revit_backend.py

Architectural role
------------------
Implements the ``RevitExtractionBackend`` protocol consumed by
``applications.bimap.infra.extraction.RevitDataExtractor``.

It does not parse RVT/RFA bytes itself. Native Autodesk Revit access remains
inside a controlled Revit/RevitCoreConsole/Design-Automation worker. This
module is the hardened process boundary between BIMAP and that worker.

Native worker command contract
------------------------------
The executable is invoked with ``shell=False``:

    <executable> [extra args]
        --mode inspect|extract
        --input <absolute source path>
        --source-format rfa|rvt
        --output <absolute JSON result path>
        [--datasets elements,properties,quantities,materials]

The worker must write UTF-8 JSON.

Inspection result:
    {
      "inspection": {
        "source_format": "rfa",
        "schema": "Revit 2026",
        "product_count": 12,
        "project_name": "FamilyName"
      }
    }

Extraction result:
    {
      "inspection": {...},
      "project": {...},
      "units": [{...}],
      "datasets": {
        "elements": [{...}],
        "properties": [{...}],
        "quantities": [{...}],
        "materials": [{...}]
      },
      "counts": {...},
      "ifc_class_counts": {}
    }

Raw source content, paths, stdout/stderr, credentials and worker payloads are
not written to BIMAP logs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from applications.bimap.app.ports.data_extraction import (
    DataExtractionCapability,
    DataSourceInspection,
    ExtractedModelData,
    ExtractionDataset,
    ExtractionSourceFormat,
)
from applications.bimap.app.utils.app_errors import (
    AppConfigurationError,
    AppError,
    AppIntegrityError,
    AppPortOperationError,
    AppPortTimeoutError,
    AppPortUnavailableError,
    AppValidationError,
    UnsupportedAppInputError,
)
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Deployment Revit Backend")
printer = PrettyPrinter()

_COMPONENT: Final[str] = "deployment_revit_backend"

REVIT_EXTRACTOR_EXECUTABLE_ENV: Final[str] = "BIMAP_REVIT_EXTRACTOR_EXECUTABLE"
REVIT_EXTRACTOR_ARGS_ENV: Final[str] = "BIMAP_REVIT_EXTRACTOR_ARGS_JSON"
REVIT_EXTRACTOR_TIMEOUT_ENV: Final[str] = "BIMAP_REVIT_EXTRACTOR_TIMEOUT_SECONDS"
REVIT_EXTRACTOR_MAX_OUTPUT_ENV: Final[str] = "BIMAP_REVIT_EXTRACTOR_MAX_OUTPUT_BYTES"

_DEFAULT_TIMEOUT_SECONDS: Final[int] = 300
_DEFAULT_MAX_OUTPUT_BYTES: Final[int] = 64 * 1024 * 1024

_SUPPORTED_FORMATS: Final[frozenset[ExtractionSourceFormat]] = frozenset(
    {
        ExtractionSourceFormat.RFA,
        ExtractionSourceFormat.RVT,
    }
)
_SUPPORTED_DATASETS: Final[tuple[ExtractionDataset, ...]] = (
    ExtractionDataset.ELEMENTS,
    ExtractionDataset.PROPERTIES,
    ExtractionDataset.QUANTITIES,
    ExtractionDataset.MATERIALS,
)


def _positive_int(
    value: Any,
    *,
    field: str,
    default: int | None = None,
) -> int:
    if value is None:
        if default is None:
            raise AppConfigurationError(
                f"{field} is required.",
                component=_COMPONENT,
                operation="configure",
                field=field,
            )
        return default

    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AppConfigurationError(
            f"{field} must be a positive integer.",
            component=_COMPONENT,
            operation="configure",
            field=field,
            cause=exc,
        ) from exc

    if parsed <= 0:
        raise AppConfigurationError(
            f"{field} must be greater than zero.",
            component=_COMPONENT,
            operation="configure",
            field=field,
            context={"received": parsed},
        )

    return parsed


def _resolve_executable(value: str) -> Path:
    raw = str(value).strip()

    if not raw:
        raise AppConfigurationError(
            "Native Revit extractor executable is not configured.",
            component=_COMPONENT,
            operation="configure",
            field=REVIT_EXTRACTOR_EXECUTABLE_ENV,
        )

    candidate = Path(raw).expanduser()

    if candidate.is_file():
        return candidate.resolve()

    discovered = shutil.which(raw)

    if discovered:
        resolved = Path(discovered).resolve()
        if resolved.is_file():
            return resolved

    raise AppConfigurationError(
        "Configured native Revit extractor executable does not exist "
        "or is not discoverable on PATH.",
        component=_COMPONENT,
        operation="configure",
        field=REVIT_EXTRACTOR_EXECUTABLE_ENV,
    )


def _parse_extra_args(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise AppConfigurationError(
            f"{REVIT_EXTRACTOR_ARGS_ENV} must contain a JSON array of strings.",
            component=_COMPONENT,
            operation="configure",
            field=REVIT_EXTRACTOR_ARGS_ENV,
            cause=exc,
        ) from exc

    if not isinstance(decoded, list):
        raise AppConfigurationError(
            f"{REVIT_EXTRACTOR_ARGS_ENV} must contain a JSON array.",
            component=_COMPONENT,
            operation="configure",
            field=REVIT_EXTRACTOR_ARGS_ENV,
        )

    result: list[str] = []

    for index, item in enumerate(decoded):
        if not isinstance(item, str) or not item.strip():
            raise AppConfigurationError(
                "Native Revit extractor arguments must be non-empty strings.",
                component=_COMPONENT,
                operation="configure",
                field=f"{REVIT_EXTRACTOR_ARGS_ENV}[{index}]",
                context={"received_type": type(item).__name__},
            )
        result.append(item.strip())

    return tuple(result)


def _require_source_file(
    path: Path,
    *,
    source_format: ExtractionSourceFormat,
    operation: str,
) -> Path:
    if not isinstance(path, Path):
        raise UnsupportedAppInputError(
            "Native Revit backend requires pathlib.Path source input.",
            component=_COMPONENT,
            operation=operation,
            field="path",
            context={"received_type": type(path).__name__},
        )

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AppValidationError(
            "Native Revit source file does not exist or cannot be resolved.",
            component=_COMPONENT,
            operation=operation,
            field="path",
            cause=exc,
        ) from exc

    if not resolved.is_file():
        raise AppValidationError(
            "Native Revit source must be a regular file.",
            component=_COMPONENT,
            operation=operation,
            field="path",
        )

    if resolved.stat().st_size <= 0:
        raise AppValidationError(
            "Native Revit source file cannot be empty.",
            component=_COMPONENT,
            operation=operation,
            field="path",
        )

    expected_suffix = f".{source_format.value}"

    if resolved.suffix.casefold() != expected_suffix:
        raise AppValidationError(
            "Native Revit source extension does not match source_format.",
            component=_COMPONENT,
            operation=operation,
            field="path",
            context={
                "source_format": source_format.value,
                "received_extension": resolved.suffix.casefold(),
            },
        )

    return resolved


def _mapping(
    value: Any,
    *,
    field: str,
    operation: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AppIntegrityError(
            "Native Revit worker returned an invalid JSON object shape.",
            component=_COMPONENT,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


def _mapping_rows(
    value: Any,
    *,
    field: str,
    operation: str,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise AppIntegrityError(
            "Native Revit extraction dataset must be a JSON array.",
            component=_COMPONENT,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )

    rows: list[Mapping[str, Any]] = []

    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise AppIntegrityError(
                "Native Revit extraction dataset rows must be JSON objects.",
                component=_COMPONENT,
                operation=operation,
                field=f"{field}[{index}]",
                context={"received_type": type(item).__name__},
            )
        rows.append(dict(item))

    return tuple(rows)


class RevitBackend:
    """Hardened deployment backend for native RVT/RFA structured extraction."""

    __slots__ = (
        "_executable",
        "_extra_args",
        "_timeout_seconds",
        "_max_output_bytes",
        "_capabilities",
    )

    def __init__(
        self,
        executable: str | Path,
        *,
        extra_args: tuple[str, ...] = (),
        timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        printer.status(
            "BIMAP",
            "Initializing native Revit extraction backend",
            "info",
        )

        self._executable = _resolve_executable(str(executable))

        if isinstance(extra_args, (str, bytes, bytearray)):
            raise AppConfigurationError(
                "extra_args must be a tuple of command arguments.",
                component=_COMPONENT,
                operation="initialize",
                field="extra_args",
            )

        normalized_args: list[str] = []
        for index, item in enumerate(extra_args):
            if not isinstance(item, str) or not item.strip():
                raise AppConfigurationError(
                    "extra_args must contain non-empty strings only.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"extra_args[{index}]",
                )
            normalized_args.append(item.strip())

        self._extra_args = tuple(normalized_args)
        self._timeout_seconds = _positive_int(
            timeout_seconds,
            field="timeout_seconds",
        )
        self._max_output_bytes = _positive_int(
            max_output_bytes,
            field="max_output_bytes",
        )

        self._capabilities = (
            DataExtractionCapability(
                source_format=ExtractionSourceFormat.RFA,
                extensions=(".rfa",),
                datasets=_SUPPORTED_DATASETS,
            ),
            DataExtractionCapability(
                source_format=ExtractionSourceFormat.RVT,
                extensions=(".rvt",),
                datasets=_SUPPORTED_DATASETS,
            ),
        )

        logger.info(
            {
                "event": "deployment_revit_backend_initialized",
                "supported_formats": tuple(
                    capability.source_format.value
                    for capability in self._capabilities
                ),
                "timeout_seconds": self._timeout_seconds,
                "max_output_bytes": self._max_output_bytes,
            }
        )

    @classmethod
    def from_env(cls) -> "RevitBackend":
        executable = os.getenv(REVIT_EXTRACTOR_EXECUTABLE_ENV)

        if executable is None or not executable.strip():
            raise AppConfigurationError(
                "Native Revit extraction was requested but no executable "
                "is configured.",
                component=_COMPONENT,
                operation="from_env",
                field=REVIT_EXTRACTOR_EXECUTABLE_ENV,
            )

        return cls(
            executable.strip(),
            extra_args=_parse_extra_args(
                os.getenv(REVIT_EXTRACTOR_ARGS_ENV)
            ),
            timeout_seconds=_positive_int(
                os.getenv(REVIT_EXTRACTOR_TIMEOUT_ENV),
                field=REVIT_EXTRACTOR_TIMEOUT_ENV,
                default=_DEFAULT_TIMEOUT_SECONDS,
            ),
            max_output_bytes=_positive_int(
                os.getenv(REVIT_EXTRACTOR_MAX_OUTPUT_ENV),
                field=REVIT_EXTRACTOR_MAX_OUTPUT_ENV,
                default=_DEFAULT_MAX_OUTPUT_BYTES,
            ),
        )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._capabilities

    @staticmethod
    def _source_format(
        value: ExtractionSourceFormat,
        *,
        operation: str,
    ) -> ExtractionSourceFormat:
        source = ExtractionSourceFormat.parse(value)

        if source not in _SUPPORTED_FORMATS:
            raise UnsupportedAppInputError(
                "Native Revit backend supports RVT/RFA sources only.",
                component=_COMPONENT,
                operation=operation,
                field="source_format",
                context={"source_format": source.value},
            )

        return source

    def _run(
        self,
        path: Path,
        *,
        source_format: ExtractionSourceFormat,
        mode: str,
        datasets: tuple[ExtractionDataset, ...] = (),
    ) -> Mapping[str, Any]:
        operation = "inspect_file" if mode == "inspect" else "extract_file"

        source = self._source_format(
            source_format,
            operation=operation,
        )
        input_path = _require_source_file(
            path,
            source_format=source,
            operation=operation,
        )

        with tempfile.TemporaryDirectory(
            prefix="bimap-revit-native-"
        ) as directory_name:
            output_path = Path(directory_name) / "result.json"

            command = [
                str(self._executable),
                *self._extra_args,
                "--mode",
                mode,
                "--input",
                str(input_path),
                "--source-format",
                source.value,
                "--output",
                str(output_path),
            ]

            if datasets:
                command.extend(
                    (
                        "--datasets",
                        ",".join(item.value for item in datasets),
                    )
                )

            try:
                completed = subprocess.run(
                    command,
                    shell=False,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise AppPortTimeoutError(
                    "Native Revit extraction worker exceeded its timeout.",
                    component=_COMPONENT,
                    operation=operation,
                    context={
                        "source_format": source.value,
                        "timeout_seconds": self._timeout_seconds,
                    },
                    cause=exc,
                ) from exc
            except OSError as exc:
                raise AppPortUnavailableError(
                    "Native Revit extraction worker could not be started.",
                    component=_COMPONENT,
                    operation=operation,
                    context={"source_format": source.value},
                    cause=exc,
                ) from exc

            if completed.returncode != 0:
                raise AppPortOperationError(
                    "Native Revit extraction worker returned a failure status.",
                    component=_COMPONENT,
                    operation=operation,
                    context={
                        "source_format": source.value,
                        "return_code": completed.returncode,
                    },
                )

            if not output_path.is_file():
                raise AppIntegrityError(
                    "Native Revit extraction worker produced no result JSON.",
                    component=_COMPONENT,
                    operation=operation,
                    field="output",
                    context={"source_format": source.value},
                )

            output_size = output_path.stat().st_size

            if output_size <= 0:
                raise AppIntegrityError(
                    "Native Revit extraction worker produced an empty result.",
                    component=_COMPONENT,
                    operation=operation,
                    field="output",
                )

            if output_size > self._max_output_bytes:
                raise AppIntegrityError(
                    "Native Revit extraction result exceeds the configured "
                    "maximum size.",
                    component=_COMPONENT,
                    operation=operation,
                    field="output",
                    context={
                        "result_size_bytes": output_size,
                        "max_output_bytes": self._max_output_bytes,
                    },
                )

            try:
                raw = output_path.read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise AppIntegrityError(
                    "Native Revit extraction worker returned invalid UTF-8 JSON.",
                    component=_COMPONENT,
                    operation=operation,
                    field="output",
                    cause=exc,
                ) from exc

        return _mapping(
            payload,
            field="result",
            operation=operation,
        )

    @staticmethod
    def _inspection(
        payload: Mapping[str, Any],
        *,
        expected_format: ExtractionSourceFormat,
        operation: str,
    ) -> DataSourceInspection:
        data = _mapping(
            payload.get("inspection"),
            field="inspection",
            operation=operation,
        )

        try:
            inspection = DataSourceInspection(
                source_format=data.get("source_format"),
                schema=data.get("schema"),
                product_count=data.get("product_count"),
                project_name=data.get("project_name"),
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Native Revit inspection payload is invalid.",
                component=_COMPONENT,
                operation=operation,
                field="inspection",
                context={"error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        if inspection.source_format is not expected_format:
            raise AppIntegrityError(
                "Native Revit worker reported the wrong source format.",
                component=_COMPONENT,
                operation=operation,
                field="inspection.source_format",
                context={
                    "expected": expected_format.value,
                    "received": inspection.source_format.value,
                },
            )

        return inspection

    def inspect_file(
        self,
        path: Path,
        *,
        source_format: ExtractionSourceFormat,
    ) -> DataSourceInspection:
        source = self._source_format(
            source_format,
            operation="inspect_file",
        )
        payload = self._run(
            path,
            source_format=source,
            mode="inspect",
        )
        return self._inspection(
            payload,
            expected_format=source,
            operation="inspect_file",
        )

    def extract_file(
        self,
        path: Path,
        *,
        source_format: ExtractionSourceFormat,
        datasets: tuple[ExtractionDataset, ...],
    ) -> ExtractedModelData:
        source = self._source_format(
            source_format,
            operation="extract_file",
        )

        selected: list[ExtractionDataset] = []

        for raw in datasets:
            dataset = ExtractionDataset.parse(raw)

            if dataset not in _SUPPORTED_DATASETS:
                raise UnsupportedAppInputError(
                    "Requested Revit extraction dataset is unsupported.",
                    component=_COMPONENT,
                    operation="extract_file",
                    field="datasets",
                    context={"dataset": dataset.value},
                )

            if dataset not in selected:
                selected.append(dataset)

        if not selected:
            raise AppValidationError(
                "At least one Revit extraction dataset is required.",
                component=_COMPONENT,
                operation="extract_file",
                field="datasets",
            )

        normalized_datasets = tuple(selected)

        payload = self._run(
            path,
            source_format=source,
            mode="extract",
            datasets=normalized_datasets,
        )

        inspection = self._inspection(
            payload,
            expected_format=source,
            operation="extract_file",
        )

        project = _mapping(
            payload.get("project"),
            field="project",
            operation="extract_file",
        )

        raw_units = payload.get("units")
        if not isinstance(raw_units, list):
            raise AppIntegrityError(
                "Native Revit extraction units must be a JSON array.",
                component=_COMPONENT,
                operation="extract_file",
                field="units",
                context={"received_type": type(raw_units).__name__},
            )

        units: list[Mapping[str, Any]] = []
        for index, item in enumerate(raw_units):
            if not isinstance(item, Mapping):
                raise AppIntegrityError(
                    "Native Revit extraction unit rows must be JSON objects.",
                    component=_COMPONENT,
                    operation="extract_file",
                    field=f"units[{index}]",
                    context={"received_type": type(item).__name__},
                )
            units.append(dict(item))

        raw_dataset_map = _mapping(
            payload.get("datasets"),
            field="datasets",
            operation="extract_file",
        )

        dataset_map: dict[str, tuple[Mapping[str, Any], ...]] = {}

        for dataset in normalized_datasets:
            if dataset.value not in raw_dataset_map:
                raise AppIntegrityError(
                    "Native Revit worker omitted a requested dataset.",
                    component=_COMPONENT,
                    operation="extract_file",
                    field=f"datasets.{dataset.value}",
                    context={"dataset": dataset.value},
                )

            dataset_map[dataset.value] = _mapping_rows(
                raw_dataset_map[dataset.value],
                field=f"datasets.{dataset.value}",
                operation="extract_file",
            )

        counts = _mapping(
            payload.get("counts"),
            field="counts",
            operation="extract_file",
        )
        ifc_class_counts = _mapping(
            payload.get("ifc_class_counts"),
            field="ifc_class_counts",
            operation="extract_file",
        )

        try:
            return ExtractedModelData(
                inspection=inspection,
                project=dict(project),
                units=tuple(units),
                datasets=dataset_map,
                counts=dict(counts),
                ifc_class_counts=dict(ifc_class_counts),
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Native Revit extraction result does not satisfy "
                "BIMAP's ExtractedModelData contract.",
                component=_COMPONENT,
                operation="extract_file",
                field="result",
                context={"error_type": type(exc).__name__},
                cause=exc,
            ) from exc


__all__ = [
    "REVIT_EXTRACTOR_EXECUTABLE_ENV",
    "REVIT_EXTRACTOR_ARGS_ENV",
    "REVIT_EXTRACTOR_TIMEOUT_ENV",
    "REVIT_EXTRACTOR_MAX_OUTPUT_ENV",
    "RevitBackend",
]


if __name__ == "__main__":
    print(
        "RevitBackend is a deployment adapter and requires "
        f"{REVIT_EXTRACTOR_EXECUTABLE_ENV}."
    )
