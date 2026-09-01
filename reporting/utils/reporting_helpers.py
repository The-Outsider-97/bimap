"""
Shared helpers for BIMAP report serialization.

This module owns reporting-specific mechanics that would otherwise be repeated
across ``reporting/serializers``: method-start diagnostics, iterable/type
validation, stable identifier checks, canonical JSON delegation, and safe CSV
encoding.

It deliberately reuses ``contracts.utils.contracts_helpers`` for canonical JSON
conversion instead of creating a second serialization policy. It does not know
about concrete report serializers, report templates, workers, API modules, or
SLAI orchestration and therefore remains at the bottom of the reporting graph.
"""

from __future__ import annotations

import csv

from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import Enum
from io import StringIO
from typing import Any, TypeVar, cast

from ...contracts.utils.contracts_errors import ContractError
from ...contracts.utils.contracts_helpers import *
from .reporting_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Reporting Helpers")
printer = PrettyPrinter()

T = TypeVar("T")

# Spreadsheet applications can interpret these prefixes as formulas. CSV is a
# transport format, not a security boundary, so customer/generative text is
# hardened by default when used in spreadsheet-facing artifacts.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def announce_reporting_action(
    target_printer: PrettyPrinter,
    target_logger: Any,
    *,
    component: str,
    action: str,
    event: str,
    context: Mapping[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Emit a consistent method-start diagnostic without report content."""
    safe_context = sanitize_reporting_context(context)
    target_printer.status("REPORTING", action, level)

    payload: dict[str, Any] = {
        "event": event,
        "component": component,
        "action": action,
    }
    if safe_context:
        payload["context"] = safe_context
    target_logger.debug(payload)


def require_record_sequence(
    value: Iterable[T] | T,
    *,
    accepted_types: type | tuple[type, ...],
    field: str,
    allow_single: bool = False,
    allow_empty: bool = True,
) -> tuple[T, ...]:
    """
    Normalize reporting input to a tuple and validate each record type.

    Strings, bytes and mappings are deliberately rejected as record sequences;
    accepting them as iterables is a common source of silent serializer bugs.
    """
    if isinstance(value, accepted_types):
        if not allow_single:
            raise ReportingValidationError(
                "Expected an iterable of report records, not a single record.",
                field=field,
                context={"received_type": type(value).__name__},
            )
        records = (value,)
    else:
        if isinstance(value, (str, bytes, bytearray, Mapping)):
            raise UnsupportedReportingInputError(
                "Reporting record collection must be an iterable of typed records.",
                field=field,
                context={"received_type": type(value).__name__},
            )
        try:
            records = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise UnsupportedReportingInputError(
                "Reporting input is not iterable.",
                field=field,
                context={"received_type": type(value).__name__},
                cause=exc,
            ) from exc

    if not records and not allow_empty:
        raise ReportingValidationError(
            "Reporting record collection must not be empty.",
            field=field,
        )

    for index, record in enumerate(records):
        if not isinstance(record, accepted_types):
            expected = (
                tuple(item.__name__ for item in accepted_types)
                if isinstance(accepted_types, tuple)
                else (accepted_types.__name__,)
            )
            raise UnsupportedReportingInputError(
                "Reporting record has an unsupported type.",
                field=f"{field}[{index}]",
                context={
                    "received_type": type(record).__name__,
                    "expected_types": expected,
                },
            )

    return cast(tuple[T, ...], records)


def ensure_unique_records(
    records: Sequence[T],
    *,
    identifier: Callable[[T], Any],
    identifier_name: str,
    component: str,
) -> None:
    """Require unique non-empty stable identifiers within an artifact."""
    seen: dict[str, int] = {}

    for index, record in enumerate(records):
        try:
            raw_identifier = identifier(record)
        except Exception as exc:  # accessor is reporting-owned and very small
            raise ReportingValidationError(
                "Unable to read the record identifier.",
                component=component,
                field=f"records[{index}].{identifier_name}",
                context={"record_type": type(record).__name__},
                cause=exc,
            ) from exc

        if not isinstance(raw_identifier, str) or not raw_identifier.strip():
            raise ReportingValidationError(
                "Report record identifier must be non-empty text.",
                component=component,
                field=f"records[{index}].{identifier_name}",
                context={"received_type": type(raw_identifier).__name__},
            )

        key = raw_identifier.strip()
        if key in seen:
            raise DuplicateReportingRecordError(
                "Duplicate stable identifier detected in report artifact.",
                component=component,
                field=identifier_name,
                context={
                    "identifier": key,
                    "first_index": seen[key],
                    "duplicate_index": index,
                },
            )
        seen[key] = index


def canonical_reporting_json(value: Any, *, pretty: bool = False) -> str:
    """Serialize reporting payloads using BIMAP's canonical contract JSON rules."""
    try:
        return canonical_json_dumps(value, pretty=pretty)
    except ContractError as exc:
        raise ReportingSerializationError(
            "Reporting payload cannot be encoded as canonical JSON.",
            component="reporting_helpers",
            cause=exc,
        ) from exc


def to_reporting_primitive(value: Any, *, field: str) -> Any:
    """Convert a supported contract/domain value into deterministic primitives."""
    if isinstance(value, Enum):
        value = value.value

    try:
        return to_json_primitive(value, field=field)
    except ContractError as exc:
        raise ReportingSerializationError(
            "Reporting value cannot be represented deterministically.",
            component="reporting_helpers",
            field=field,
            context={"received_type": type(value).__name__},
            cause=exc,
        ) from exc


def normalize_csv_cell(
    value: Any,
    *,
    field: str,
    excel_safe: bool = True,
) -> str | int | float:
    """
    Convert a reporting value into one deterministic CSV cell.

    Nested values are encoded as compact canonical JSON. Spreadsheet-formula
    prefixes in textual cells are escaped with an apostrophe when ``excel_safe``
    is enabled; numeric values remain numeric and are never modified.
    """
    primitive = to_reporting_primitive(value, field=field)

    if primitive is None:
        return ""

    if isinstance(primitive, bool):
        # Lower-case JSON spelling is deterministic and language-neutral.
        return "true" if primitive else "false"

    if isinstance(primitive, (int, float)):
        return primitive

    if isinstance(primitive, (dict, list)):
        text = canonical_reporting_json(primitive, pretty=False)
    else:
        text = str(primitive)

    if excel_safe:
        stripped = text.lstrip()
        if stripped.startswith(_FORMULA_PREFIXES):
            text = f"'{text}"

    return text


def build_csv(
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str],
    component: str,
    excel_safe: bool = True,
) -> str:
    """Build deterministic UTF-8-compatible CSV text with a fixed column order."""
    normalized_fields = tuple(str(field).strip() for field in fieldnames)
    if not normalized_fields or any(not field for field in normalized_fields):
        raise ReportingValidationError(
            "CSV fieldnames must contain non-empty column names.",
            component=component,
            field="fieldnames",
        )
    if len(set(normalized_fields)) != len(normalized_fields):
        raise ReportingValidationError(
            "CSV fieldnames contain duplicate columns.",
            component=component,
            field="fieldnames",
            context={"fieldnames": normalized_fields},
        )

    output = StringIO(newline="")
    try:
        writer = csv.DictWriter(
            output,
            fieldnames=list(normalized_fields),
            extrasaction="raise",
            lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writeheader()

        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ReportingValidationError(
                    "CSV row must be a mapping.",
                    component=component,
                    field=f"rows[{row_index}]",
                    context={"received_type": type(row).__name__},
                )

            missing = tuple(field for field in normalized_fields if field not in row)
            unexpected = tuple(sorted(set(row).difference(normalized_fields)))
            if missing or unexpected:
                raise ReportingValidationError(
                    "CSV row fields do not match the serializer column contract.",
                    component=component,
                    field=f"rows[{row_index}]",
                    context={"missing": missing, "unexpected": unexpected},
                )

            encoded = {
                field: normalize_csv_cell(
                    row[field],
                    field=f"rows[{row_index}].{field}",
                    excel_safe=excel_safe,
                )
                for field in normalized_fields
            }
            writer.writerow(encoded)

        return output.getvalue()
    except ReportingValidationError:
        raise
    except (csv.Error, UnicodeError, TypeError, ValueError) as exc:
        raise ReportingSerializationError(
            "CSV serialization failed.",
            component=component,
            cause=exc,
        ) from exc
    finally:
        output.close()


__all__ = [
    "announce_reporting_action",
    "require_record_sequence",
    "ensure_unique_records",
    "canonical_reporting_json",
    "to_reporting_primitive",
    "normalize_csv_cell",
    "build_csv",
]


if __name__ == "__main__":
    print("\n=== Running Reporting Helpers Self-Test ===\n")
    printer.status("TEST", "Reporting helpers initialized", "info")

    assert canonical_reporting_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert normalize_csv_cell("=SUM(A1:A2)", field="formula") == "'=SUM(A1:A2)"
    sample_csv = build_csv(
        ({"id": "A", "refs": ["EV-2", "EV-1"]},),
        fieldnames=("id", "refs"),
        component="self_test",
    )
    assert sample_csv.startswith("id,refs\n")
    printer.status("PASS", "Canonical JSON and CSV helpers", "success")

    print("\n=== Test ran successfully ===\n")
