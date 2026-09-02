"""
BIMAP application repository ports.

This module is the persistence dependency-inversion boundary for canonical
records already present in BIMAP:

* ``Order`` aggregate;
* canonical ``EvidenceItem``;
* canonical ``Finding``;
* governance ``Review``;
* versioned ``ReportManifest``.

It does not define a database schema, ORM model, SQL transaction manager, object
store, cache, migration system, or provider client. Concrete persistence
adapters belong above this Level-4 port and are composed at bootstrap.

Design decisions
----------------
* Reads return ``None`` for an absent record. Absence is a normal query result.
* Save operations return the canonical record accepted by the adapter so the
  BIMAP boundary can detect identity corruption.
* ``Order.version`` already exists for optimistic concurrency. ``save_order``
  therefore exposes an optional ``expected_version`` precondition; a concrete
  adapter must enforce it atomically and raise ``RepositoryConflictError`` when
  the comparison fails.
* No version fields are invented for Evidence, Finding, Review, or
  ReportManifest.
* Hard-delete/list/query APIs are deliberately not guessed here. Their semantics
  depend on explicit retention, auditability, and persistence requirements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, TypeVar, cast

from ..utils.app_errors import (
    RepositoryConflictError,
    RepositoryError,
    RepositoryIntegrityError,
    RepositoryOperationError,
    RepositoryTimeoutError,
    RepositoryUnavailableError,
    RepositoryValidationError,
)
from ..utils.app_helpers import (
    announce_app_action,
    require_app_text,
    require_non_negative_int,
)
from ...contracts.report_manifest import ReportManifest
from ...domain.evidence.models import EvidenceItem
from ...domain.findings.models import Finding
from ...domain.governance.review import Review
from ...domain.orders.models import Order
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Repository Ports")
printer = PrettyPrinter()

_COMPONENT = "repository"
T = TypeVar("T")


def _run_repository_operation(
    operation: str,
    callback: Callable[[], T],
    *,
    context: dict[str, Any],
) -> T:
    """Translate generic backend failures without repeating catch logic."""
    try:
        return callback()
    except RepositoryError:
        raise
    except TimeoutError as exc:
        raise RepositoryTimeoutError(
            "Repository operation timed out.",
            component=_COMPONENT,
            operation=operation,
            context=context,
            cause=exc,
        ) from exc
    except ConnectionError as exc:
        raise RepositoryUnavailableError(
            "Repository backend is unavailable.",
            component=_COMPONENT,
            operation=operation,
            context=context,
            cause=exc,
        ) from exc
    except Exception as exc:
        raise RepositoryOperationError(
            "Repository adapter failed to complete an operation.",
            component=_COMPONENT,
            operation=operation,
            context={
                **context,
                "error_type": type(exc).__name__,
            },
            cause=exc,
        ) from exc


def _validate_loaded_record(
    value: object | None,
    *,
    expected_type: type[T],
    identifier_name: str,
    requested_identifier: str,
    operation: str,
) -> T | None:
    """Validate one optional adapter result and bind its stable identity."""
    if value is None:
        return None
    if not isinstance(value, expected_type):
        raise RepositoryValidationError(
            "Repository adapter returned an unsupported record type.",
            component=_COMPONENT,
            operation=operation,
            field="result",
            context={
                "expected_type": expected_type.__name__,
                "received_type": type(value).__name__,
            },
        )

    returned_identifier = getattr(value, identifier_name, None)
    if returned_identifier != requested_identifier:
        raise RepositoryIntegrityError(
            "Repository result identity does not match the requested record.",
            component=_COMPONENT,
            operation=operation,
            field=f"result.{identifier_name}",
            context={
                "requested_identifier": requested_identifier,
                "returned_identifier": returned_identifier,
            },
        )
    return cast(T, value)


def _validate_saved_record(
    value: object,
    *,
    expected_type: type[T],
    identifier_name: str,
    expected_identifier: str,
    operation: str,
) -> T:
    """Validate one adapter save result and preserve stable identity."""
    result = _validate_loaded_record(
        value,
        expected_type=expected_type,
        identifier_name=identifier_name,
        requested_identifier=expected_identifier,
        operation=operation,
    )
    if result is None:
        raise RepositoryIntegrityError(
            "Repository save returned no persisted record.",
            component=_COMPONENT,
            operation=operation,
            field="result",
            context={"expected_identifier": expected_identifier},
        )
    return result


class Repository(ABC):
    """
    Composite BIMAP persistence port for the current canonical record set.

    A deployment may implement this composite with one transactional store or
    delegate internally to specialized persistence mechanisms. Application
    services remain coupled only to this BIMAP-owned interface.
    """

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing repository port",
            event="repository_init_start",
        )
        logger.debug(
            {
                "event": "repository_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _get_order(self, order_id: str) -> Order | None:
        raise NotImplementedError

    @abstractmethod
    def _save_order(
        self,
        order: Order,
        *,
        expected_version: int | None,
    ) -> Order:
        raise NotImplementedError

    @abstractmethod
    def _get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        raise NotImplementedError

    @abstractmethod
    def _save_evidence(self, evidence: EvidenceItem) -> EvidenceItem:
        raise NotImplementedError

    @abstractmethod
    def _get_finding(self, finding_id: str) -> Finding | None:
        raise NotImplementedError

    @abstractmethod
    def _save_finding(self, finding: Finding) -> Finding:
        raise NotImplementedError

    @abstractmethod
    def _get_review(self, review_id: str) -> Review | None:
        raise NotImplementedError

    @abstractmethod
    def _save_review(self, review: Review) -> Review:
        raise NotImplementedError

    @abstractmethod
    def _get_report_manifest(self, report_id: str) -> ReportManifest | None:
        raise NotImplementedError

    @abstractmethod
    def _save_report_manifest(self, manifest: ReportManifest) -> ReportManifest:
        raise NotImplementedError

    def get_order(self, order_id: str) -> Order | None:
        """Load one Order by stable identifier, or return ``None``."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading order",
            event="repository_get_order_start",
            context={"order_id": order_id},
        )
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_order",
        )
        value = _run_repository_operation(
            "get_order",
            lambda: self._get_order(target),
            context={"order_id": target},
        )
        return _validate_loaded_record(
            value,
            expected_type=Order,
            identifier_name="order_id",
            requested_identifier=target,
            operation="get_order",
        )

    def save_order(
        self,
        order: Order,
        *,
        expected_version: int | None = None,
    ) -> Order:
        """
        Persist an Order with an optional atomic optimistic-concurrency check.

        ``expected_version`` is the revision observed by the caller before its
        change. The concrete adapter owns the atomic compare-and-write.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Saving order",
            event="repository_save_order_start",
            context={"order_id": getattr(order, "order_id", None)},
        )
        if not isinstance(order, Order):
            raise RepositoryValidationError(
                "save_order() requires a canonical Order.",
                component=_COMPONENT,
                operation="save_order",
                field="order",
                context={"received_type": type(order).__name__},
            )
        normalized_expected = (
            None
            if expected_version is None
            else require_non_negative_int(
                expected_version,
                field="expected_version",
                error_type=RepositoryValidationError,
                component=_COMPONENT,
                operation="save_order",
            )
        )
        value = _run_repository_operation(
            "save_order",
            lambda: self._save_order(
                order,
                expected_version=normalized_expected,
            ),
            context={
                "order_id": order.order_id,
                "expected_version": normalized_expected,
            },
        )
        return _validate_saved_record(
            value,
            expected_type=Order,
            identifier_name="order_id",
            expected_identifier=order.order_id,
            operation="save_order",
        )

    def get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        """Load one canonical EvidenceItem by stable identifier."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading evidence record",
            event="repository_get_evidence_start",
            context={"evidence_id": evidence_id},
        )
        target = require_app_text(
            evidence_id,
            field="evidence_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_evidence",
        )
        value = _run_repository_operation(
            "get_evidence",
            lambda: self._get_evidence(target),
            context={"evidence_id": target},
        )
        return _validate_loaded_record(
            value,
            expected_type=EvidenceItem,
            identifier_name="evidence_id",
            requested_identifier=target,
            operation="get_evidence",
        )

    def save_evidence(self, evidence: EvidenceItem) -> EvidenceItem:
        """Persist one canonical EvidenceItem."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Saving evidence record",
            event="repository_save_evidence_start",
            context={"evidence_id": getattr(evidence, "evidence_id", None)},
        )
        if not isinstance(evidence, EvidenceItem):
            raise RepositoryValidationError(
                "save_evidence() requires an EvidenceItem.",
                component=_COMPONENT,
                operation="save_evidence",
                field="evidence",
                context={"received_type": type(evidence).__name__},
            )
        value = _run_repository_operation(
            "save_evidence",
            lambda: self._save_evidence(evidence),
            context={"evidence_id": evidence.evidence_id},
        )
        return _validate_saved_record(
            value,
            expected_type=EvidenceItem,
            identifier_name="evidence_id",
            expected_identifier=evidence.evidence_id,
            operation="save_evidence",
        )

    def get_finding(self, finding_id: str) -> Finding | None:
        """Load one canonical Finding by stable identifier."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading finding",
            event="repository_get_finding_start",
            context={"finding_id": finding_id},
        )
        target = require_app_text(
            finding_id,
            field="finding_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_finding",
        )
        value = _run_repository_operation(
            "get_finding",
            lambda: self._get_finding(target),
            context={"finding_id": target},
        )
        return _validate_loaded_record(
            value,
            expected_type=Finding,
            identifier_name="finding_id",
            requested_identifier=target,
            operation="get_finding",
        )

    def save_finding(self, finding: Finding) -> Finding:
        """Persist one canonical Finding."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Saving finding",
            event="repository_save_finding_start",
            context={"finding_id": getattr(finding, "finding_id", None)},
        )
        if not isinstance(finding, Finding):
            raise RepositoryValidationError(
                "save_finding() requires a canonical Finding.",
                component=_COMPONENT,
                operation="save_finding",
                field="finding",
                context={"received_type": type(finding).__name__},
            )
        value = _run_repository_operation(
            "save_finding",
            lambda: self._save_finding(finding),
            context={"finding_id": finding.finding_id},
        )
        return _validate_saved_record(
            value,
            expected_type=Finding,
            identifier_name="finding_id",
            expected_identifier=finding.finding_id,
            operation="save_finding",
        )

    def get_review(self, review_id: str) -> Review | None:
        """Load one governance Review by stable identifier."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading governance review",
            event="repository_get_review_start",
            context={"review_id": review_id},
        )
        target = require_app_text(
            review_id,
            field="review_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_review",
        )
        value = _run_repository_operation(
            "get_review",
            lambda: self._get_review(target),
            context={"review_id": target},
        )
        return _validate_loaded_record(
            value,
            expected_type=Review,
            identifier_name="review_id",
            requested_identifier=target,
            operation="get_review",
        )

    def save_review(self, review: Review) -> Review:
        """Persist one governance Review aggregate."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Saving governance review",
            event="repository_save_review_start",
            context={"review_id": getattr(review, "review_id", None)},
        )
        if not isinstance(review, Review):
            raise RepositoryValidationError(
                "save_review() requires a governance Review.",
                component=_COMPONENT,
                operation="save_review",
                field="review",
                context={"received_type": type(review).__name__},
            )
        value = _run_repository_operation(
            "save_review",
            lambda: self._save_review(review),
            context={"review_id": review.review_id},
        )
        return _validate_saved_record(
            value,
            expected_type=Review,
            identifier_name="review_id",
            expected_identifier=review.review_id,
            operation="save_review",
        )

    def get_report_manifest(self, report_id: str) -> ReportManifest | None:
        """Load one immutable versioned ReportManifest."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading report manifest",
            event="repository_get_report_manifest_start",
            context={"report_id": report_id},
        )
        target = require_app_text(
            report_id,
            field="report_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_report_manifest",
        )
        value = _run_repository_operation(
            "get_report_manifest",
            lambda: self._get_report_manifest(target),
            context={"report_id": target},
        )
        return _validate_loaded_record(
            value,
            expected_type=ReportManifest,
            identifier_name="report_id",
            requested_identifier=target,
            operation="get_report_manifest",
        )

    def save_report_manifest(self, manifest: ReportManifest) -> ReportManifest:
        """Persist a ReportManifest without redefining contract integrity."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Saving report manifest",
            event="repository_save_report_manifest_start",
            context={"report_id": getattr(manifest, "report_id", None)},
        )
        if not isinstance(manifest, ReportManifest):
            raise RepositoryValidationError(
                "save_report_manifest() requires a ReportManifest contract.",
                component=_COMPONENT,
                operation="save_report_manifest",
                field="manifest",
                context={"received_type": type(manifest).__name__},
            )
        value = _run_repository_operation(
            "save_report_manifest",
            lambda: self._save_report_manifest(manifest),
            context={"report_id": manifest.report_id},
        )
        return _validate_saved_record(
            value,
            expected_type=ReportManifest,
            identifier_name="report_id",
            expected_identifier=manifest.report_id,
            operation="save_report_manifest",
        )


__all__ = ["Repository"]