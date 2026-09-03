"""
Read-only application query for a known set of BIMAP report manifests.

The current ``Repository`` port intentionally exposes point lookup for
``ReportManifest`` and does not define global report scans, persistence-side
sorting, customer ownership filters, or pagination.  This query therefore
accepts the ordered report identifiers already authorized/resolved by its
caller and performs only deterministic point reads.

An optional ``expected_order_id`` is a binding assertion, not a filter: when it
is supplied, every found report must belong to that order.  A mismatch is
reported as an integrity failure instead of being silently filtered, which
helps prevent cross-order report leakage caused by an incorrect identifier set.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..ports.repositories import Repository
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.report_manifest import ReportManifest
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP List Reports Query")
printer = PrettyPrinter()

_COMPONENT = "list_reports_query"


def _normalize_report_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize and stable-deduplicate report identifiers."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing report identifiers",
        event="list_reports_query_ids_normalize_start",
    )
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedAppInputError(
            "report_ids must be an iterable of report identifier strings.",
            component=_COMPONENT,
            operation="normalize_report_ids",
            field="report_ids",
            context={"received_type": type(values).__name__},
        )
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise UnsupportedAppInputError(
            "report_ids must be iterable.",
            component=_COMPONENT,
            operation="normalize_report_ids",
            field="report_ids",
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    result: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(iterator):
        report_id = require_app_text(
            value,
            field=f"report_ids[{index}]",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="normalize_report_ids",
        )
        if report_id not in seen:
            seen.add(report_id)
            result.append(report_id)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ReportListResult:
    """Deterministic result of resolving a caller-supplied report-id set."""

    items: tuple[ReportManifest, ...]
    missing_report_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating report-list result",
            event="list_reports_query_result_validate_start",
        )
        if not isinstance(self.items, tuple):
            raise AppValidationError(
                "items must be a tuple of ReportManifest values.",
                component=_COMPONENT,
                operation="validate_result",
                field="items",
                context={"received_type": type(self.items).__name__},
            )
        for index, item in enumerate(self.items):
            if not isinstance(item, ReportManifest):
                raise AppValidationError(
                    "items contains a non-ReportManifest value.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=f"items[{index}]",
                    context={"received_type": type(item).__name__},
                )

        item_ids = tuple(item.report_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise AppValidationError(
                "items contains duplicate report identifiers.",
                component=_COMPONENT,
                operation="validate_result",
                field="items",
            )

        missing = _normalize_report_ids(self.missing_report_ids)
        found_ids = {item.report_id for item in self.items}
        overlap = tuple(report_id for report_id in missing if report_id in found_ids)
        if overlap:
            raise AppValidationError(
                "A report identifier cannot be both found and missing.",
                component=_COMPONENT,
                operation="validate_result",
                field="missing_report_ids",
                context={"overlap": overlap},
            )
        object.__setattr__(self, "missing_report_ids", missing)

    @property
    def found_count(self) -> int:
        return len(self.items)

    @property
    def missing_count(self) -> int:
        return len(self.missing_report_ids)

    @property
    def requested_count(self) -> int:
        return self.found_count + self.missing_count

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-ready report-list data."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing report-list result",
            event="list_reports_query_result_to_dict_start",
        )
        return {
            "items": [item.to_dict() for item in self.items],
            "missing_report_ids": list(self.missing_report_ids),
            "found_count": self.found_count,
            "missing_count": self.missing_count,
            "requested_count": self.requested_count,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Encode the result using BIMAP's canonical application JSON rules."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Encoding report-list result JSON",
            event="list_reports_query_result_to_json_start",
        )
        return canonical_app_json(self.to_dict(), pretty=pretty)


class ListReports:
    """Resolve report manifests through the current point-read repository port."""

    def __init__(self, repository: Repository) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing list-reports query",
            event="list_reports_query_init_start",
        )
        if not isinstance(repository, Repository):
            raise AppConfigurationError(
                "repository must implement the BIMAP Repository port.",
                component=_COMPONENT,
                operation="initialize",
                field="repository",
                context={"received_type": type(repository).__name__},
            )
        self.repository = repository
        logger.debug(
            {
                "event": "list_reports_query_initialized",
                "repository_implementation": type(repository).__name__,
            }
        )

    def execute(
        self,
        report_ids: Iterable[str],
        *,
        expected_order_id: str | None = None,
    ) -> ReportListResult:
        """Resolve unique report ids, optionally asserting one owning order."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing list-reports query",
            event="list_reports_query_execute_start",
            context={"has_expected_order_id": expected_order_id is not None},
        )
        targets = _normalize_report_ids(report_ids)
        owner = (
            None
            if expected_order_id is None
            else require_app_text(
                expected_order_id,
                field="expected_order_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="execute",
            )
        )

        items: list[ReportManifest] = []
        missing: list[str] = []
        for report_id in targets:
            manifest = self.repository.get_report_manifest(report_id)
            if manifest is None:
                missing.append(report_id)
                continue

            if owner is not None and manifest.order_id != owner:
                raise AppIntegrityError(
                    "Report manifest belongs to a different order than the requested binding.",
                    component=_COMPONENT,
                    operation="execute",
                    field="report.order_id",
                    context={
                        "report_id": report_id,
                        "expected_order_id": owner,
                        "returned_order_id": manifest.order_id,
                    },
                )
            items.append(manifest)

        result = ReportListResult(
            items=tuple(items),
            missing_report_ids=tuple(missing),
        )
        logger.info(
            {
                "event": "list_reports_query_completed",
                "requested_count": result.requested_count,
                "found_count": result.found_count,
                "missing_count": result.missing_count,
                "order_bound": owner is not None,
            }
        )
        return result


# Backward-compatible name retained from the original scaffold.
ListReport = ListReports


__all__ = ["ReportListResult", "ListReports", "ListReport"]