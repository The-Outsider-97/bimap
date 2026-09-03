"""
Read-only BIMAP audit-status query.

BIMAP currently has no persisted ``AuditJobStatus`` model and the ``AuditJob``
contract is deliberately only a work envelope.  The canonical observable audit
lifecycle is therefore the authoritative ``Order.state``.  This module exposes
that state as a small immutable query projection and does not invent queue
positions, completion percentages, SLAI progress, or worker-specific status
vocabularies.

An optional ``AuditJob`` may be supplied when the caller already holds the job
envelope.  It is used only to verify job/order/product/revision binding and to
add provenance to the returned projection; the query never treats the job as an
independent source of lifecycle truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ports.repositories import Repository
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.audit_job import AuditJob
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState, TERMINAL_EXCEPTION_STATES
from ...domain.products.models import ProductCode
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Get Audit Status Query")
printer = PrettyPrinter()

_COMPONENT = "get_audit_status_query"


@dataclass(frozen=True, slots=True)
class AuditStatus:
    """Immutable projection of the authoritative order-backed audit lifecycle."""

    order_id: str
    product_code: ProductCode
    state: OrderState
    updated_at: str
    order_version: int
    job_id: str | None = None
    job_order_version: int | None = None
    job_submitted_at: str | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating audit-status projection",
            event="get_audit_status_projection_validate_start",
            context={"order_id": self.order_id, "job_id": self.job_id},
        )
        object.__setattr__(
            self,
            "order_id",
            require_app_text(
                self.order_id,
                field="order_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_status",
            ),
        )
        try:
            product = ProductCode.parse(self.product_code)
            state = OrderState.parse(self.state)
        except DomainError as exc:
            raise AppIntegrityError(
                "Audit status contains an invalid canonical product or order state.",
                component=_COMPONENT,
                operation="validate_status",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        object.__setattr__(self, "product_code", product)
        object.__setattr__(self, "state", state)
        object.__setattr__(
            self,
            "order_version",
            require_non_negative_int(
                self.order_version,
                field="order_version",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_status",
            ),
        )
        # Re-normalize the serialized timestamp to guarantee canonical UTC output.
        normalized_updated = ensure_app_utc_datetime(
            self.updated_at,
            field="updated_at",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_status",
        )
        object.__setattr__(
            self,
            "updated_at",
            format_app_utc_datetime(
                normalized_updated,
                field="updated_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_status",
            ),
        )

        if self.job_id is None:
            if self.job_order_version is not None or self.job_submitted_at is not None:
                raise AppIntegrityError(
                    "Job provenance fields require job_id.",
                    component=_COMPONENT,
                    operation="validate_status",
                    field="job_id",
                )
            return

        object.__setattr__(
            self,
            "job_id",
            require_app_text(
                self.job_id,
                field="job_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_status",
            ),
        )
        if self.job_order_version is None or self.job_submitted_at is None:
            raise AppIntegrityError(
                "job_order_version and job_submitted_at are required with job_id.",
                component=_COMPONENT,
                operation="validate_status",
                field="job_provenance",
            )
        normalized_job_version = require_non_negative_int(
            self.job_order_version,
            field="job_order_version",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_status",
        )
        if normalized_job_version > self.order_version:
            raise AppIntegrityError(
                "Audit-job revision cannot exceed the authoritative order revision.",
                component=_COMPONENT,
                operation="validate_status",
                field="job_order_version",
                context={
                    "job_order_version": normalized_job_version,
                    "order_version": self.order_version,
                },
            )
        object.__setattr__(self, "job_order_version", normalized_job_version)
        normalized_submitted = ensure_app_utc_datetime(
            self.job_submitted_at,
            field="job_submitted_at",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_status",
        )
        object.__setattr__(
            self,
            "job_submitted_at",
            format_app_utc_datetime(
                normalized_submitted,
                field="job_submitted_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_status",
            ),
        )

    @property
    def is_processing(self) -> bool:
        return self.state.is_processing

    @property
    def is_exception(self) -> bool:
        return self.state.is_exception

    @property
    def is_delivered(self) -> bool:
        return self.state.is_delivered

    @property
    def is_terminal(self) -> bool:
        return self.state.is_delivered or self.state in TERMINAL_EXCEPTION_STATES

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-ready audit status metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing audit status",
            event="get_audit_status_projection_to_dict_start",
            context={"order_id": self.order_id, "job_id": self.job_id},
        )
        return {
            "order_id": self.order_id,
            "product_code": self.product_code.value,
            "state": self.state.value,
            "updated_at": self.updated_at,
            "order_version": self.order_version,
            "is_processing": self.is_processing,
            "is_exception": self.is_exception,
            "is_delivered": self.is_delivered,
            "is_terminal": self.is_terminal,
            "job_id": self.job_id,
            "job_order_version": self.job_order_version,
            "job_submitted_at": self.job_submitted_at,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Encode status using BIMAP's canonical application JSON rules."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Encoding audit status JSON",
            event="get_audit_status_projection_to_json_start",
            context={"order_id": self.order_id},
        )
        return canonical_app_json(self.to_dict(), pretty=pretty)

    @classmethod
    def from_order(cls, order: Order, *, job: AuditJob | None = None) -> "AuditStatus":
        """Build a status projection from the authoritative order and optional job."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building audit status from authoritative order",
            event="get_audit_status_projection_from_order_start",
            context={
                "order_id": getattr(order, "order_id", None),
                "job_id": getattr(job, "job_id", None),
            },
        )
        if not isinstance(order, Order):
            raise UnsupportedAppInputError(
                "order must be a canonical Order.",
                component=_COMPONENT,
                operation="from_order",
                field="order",
                context={"received_type": type(order).__name__},
            )
        if job is not None and not isinstance(job, AuditJob):
            raise UnsupportedAppInputError(
                "job must be an AuditJob or None.",
                component=_COMPONENT,
                operation="from_order",
                field="job",
                context={"received_type": type(job).__name__},
            )

        try:
            product = ProductCode.parse(order.product_code)
        except DomainError as exc:
            raise AppIntegrityError(
                "Authoritative order contains an invalid product code.",
                component=_COMPONENT,
                operation="from_order",
                field="order.product_code",
                context={"order_id": order.order_id, **lower_error_context(exc)},
                cause=exc,
            ) from exc

        if job is not None:
            if job.order_id != order.order_id:
                raise AppIntegrityError(
                    "Audit job belongs to a different order.",
                    component=_COMPONENT,
                    operation="from_order",
                    field="job.order_id",
                    context={
                        "order_id": order.order_id,
                        "job_order_id": job.order_id,
                        "job_id": job.job_id,
                    },
                )
            try:
                job_product = ProductCode.parse(job.product_code)
            except DomainError as exc:
                raise AppIntegrityError(
                    "Audit job contains an invalid product code.",
                    component=_COMPONENT,
                    operation="from_order",
                    field="job.product_code",
                    context={"job_id": job.job_id, **lower_error_context(exc)},
                    cause=exc,
                ) from exc
            if job_product is not product:
                raise AppIntegrityError(
                    "Audit job product does not match the authoritative order.",
                    component=_COMPONENT,
                    operation="from_order",
                    field="job.product_code",
                    context={
                        "job_id": job.job_id,
                        "job_product_code": job_product.value,
                        "order_product_code": product.value,
                    },
                )
            if order.version < job.order_version:
                raise AppIntegrityError(
                    "Authoritative order revision is older than the audit-job revision.",
                    component=_COMPONENT,
                    operation="from_order",
                    field="job.order_version",
                    context={
                        "job_id": job.job_id,
                        "job_order_version": job.order_version,
                        "current_order_version": order.version,
                    },
                )

        return cls(
            order_id=order.order_id,
            product_code=product,
            state=order.state,
            updated_at=format_app_utc_datetime(
                order.updated_at,
                field="order.updated_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="from_order",
            ),
            order_version=order.version,
            job_id=job.job_id if job is not None else None,
            job_order_version=job.order_version if job is not None else None,
            job_submitted_at=(
                format_app_utc_datetime(
                    ensure_app_utc_datetime(
                        job.submitted_at,
                        field="job.submitted_at",
                        error_type=AppValidationError,
                        component=_COMPONENT,
                        operation="from_order",
                    ),
                    field="job.submitted_at",
                    error_type=AppValidationError,
                    component=_COMPONENT,
                    operation="from_order",
                )
                if job is not None
                else None
            ),
        )


class GetAuditStatus:
    """Read the authoritative order-backed audit status without mutation."""

    def __init__(self, repository: Repository) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing get-audit-status query",
            event="get_audit_status_query_init_start",
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
                "event": "get_audit_status_query_initialized",
                "repository_implementation": type(repository).__name__,
            }
        )

    def find(self, order_id: str, *, job: AuditJob | None = None) -> AuditStatus | None:
        """Return audit status for an order, or ``None`` when the order is absent."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding audit status",
            event="get_audit_status_query_find_start",
            context={"order_id": order_id, "job_id": getattr(job, "job_id", None)},
        )
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="find",
        )
        if job is not None and not isinstance(job, AuditJob):
            raise UnsupportedAppInputError(
                "job must be an AuditJob or None.",
                component=_COMPONENT,
                operation="find",
                field="job",
                context={"received_type": type(job).__name__},
            )
        if job is not None and job.order_id != target:
            raise AppValidationError(
                "Supplied audit job does not belong to the requested order_id.",
                component=_COMPONENT,
                operation="find",
                field="job.order_id",
                context={
                    "requested_order_id": target,
                    "job_order_id": job.order_id,
                    "job_id": job.job_id,
                },
            )

        order = self.repository.get_order(target)
        if order is None:
            logger.debug(
                {
                    "event": "get_audit_status_query_not_found",
                    "order_id": target,
                }
            )
            return None

        result = AuditStatus.from_order(order, job=job)
        logger.info(
            {
                "event": "get_audit_status_query_completed",
                "order_id": result.order_id,
                "state": result.state.value,
                "order_version": result.order_version,
                "job_bound": result.job_id is not None,
            }
        )
        return result

    def execute(self, order_id: str, *, job: AuditJob | None = None) -> AuditStatus:
        """Return audit status and fail explicitly when the order does not exist."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing get-audit-status query",
            event="get_audit_status_query_execute_start",
            context={"order_id": order_id, "job_id": getattr(job, "job_id", None)},
        )
        result = self.find(order_id, job=job)
        if result is None:
            raise AppValidationError(
                "Order does not exist; audit status is unavailable.",
                component=_COMPONENT,
                operation="execute",
                field="order_id",
                context={"order_id": order_id},
            )
        return result


# Backward-compatible alias retained from the original scaffold.
GAS = GetAuditStatus


__all__ = [
    "AuditStatus",
    "GetAuditStatus",
    "GAS"
]