"""
Internal governance/operations routes backed by currently implemented BIMAP use cases.

The strategic BIMAP admin dashboard is broader than the read/write surfaces
currently present in ``app/``.  In particular, the current repository/query
ports do not provide global scans or filters for:

* orders by state/age/product/payment/priority;
* review queues;
* analysis jobs/retries/runtime;
* retention/deletion schedules;
* refunds/support threads.

This module therefore exposes only operations that can be implemented without
inventing persistence/query semantics:

* point-read one order;
* point-read one report manifest;
* point-read one governance review;
* append one governance decision.

``resolve_orders`` and ``resolve_reports`` are intentionally unregistered helper
methods for a future trusted admin read model that has already selected explicit
IDs.  They reuse the current ``ListOrders``/``ListReports`` point-read queries
without pretending those queries are database search APIs.
"""

from __future__ import annotations

from collections.abc import Iterable
from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.queries.get_order import GetOrder
from ...app.queries.list_orders import ListOrders
from ...app.queries.list_reports import ListReports
from ...app.services.review_service import ReviewService
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Admin")
printer = PrettyPrinter()

_COMPONENT = "api_route_admin"


class RouteAdmin:
    """Dependency-injected internal governance/operations route group."""

    __slots__ = (
        "router",
        "_review_service",
        "_get_order",
        "_list_orders",
        "_list_reports",
        "_authorize",
    )

    def __init__(
        self,
        review_service: ReviewService,
        get_order: GetOrder,
        list_orders: ListOrders,
        list_reports: ListReports,
        *,
        authorizer: RouteAuthorizer,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing admin API routes",
            event="api_route_admin_init_start",
        )
        dependencies = (
            ("review_service", review_service, ReviewService),
            ("get_order", get_order, GetOrder),
            ("list_orders", list_orders, ListOrders),
            ("list_reports", list_reports, ListReports),
        )
        for field, value, expected in dependencies:
            if not isinstance(value, expected):
                raise APIConfigurationError(
                    f"{field} must be a {expected.__name__} handler.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=field,
                    context={"received_type": type(value).__name__},
                )

        self._review_service = review_service
        self._get_order = get_order
        self._list_orders = list_orders
        self._list_reports = list_reports
        self._authorize = require_route_authorizer(authorizer)

        router = APIRouter(prefix="/admin", tags=["admin"])
        router.add_api_route(
            "/orders/{order_id}",
            self.get_order,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="admin_get_order",
        )
        router.add_api_route(
            "/reports/{report_id}",
            self.get_report,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="admin_get_report",
        )
        router.add_api_route(
            "/reviews/{review_id}",
            self.get_review,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="admin_get_review",
        )
        router.add_api_route(
            "/reviews/{review_id}/decisions",
            self.record_decision,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="admin_record_review_decision",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_admin_initialized",
                "registered_route_count": 4,
            }
        )

    async def get_order(self, request: Request, order_id: str) -> Response:
        """GET ``/admin/orders/{order_id}`` -> authorized order contract."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling admin get-order request",
            event="api_route_admin_get_order_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="admin_get_order",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="admin_get_order",
            resource_id=target,
        )

        result = self._get_order.find(target)
        if result is None:
            raise APINotFoundError(
                "Requested order does not exist.",
                component=_COMPONENT,
                operation="admin_get_order",
                field="order_id",
                context={"order_id": target},
            )

        logger.info(
            {
                "event": "api_route_admin_get_order_completed",
                "order_id": result.order_id,
                "version": result.version,
                "state": result.state.value, # type: ignore
            }
        )
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def get_report(self, request: Request, report_id: str) -> Response:
        """GET ``/admin/reports/{report_id}`` -> one persisted report manifest."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling admin get-report request",
            event="api_route_admin_get_report_start",
            context={"report_id": report_id},
        )
        target = require_api_text(
            report_id,
            field="report_id",
            component=_COMPONENT,
            operation="admin_get_report",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="admin_get_report",
            resource_id=target,
        )

        result = self._list_reports.execute((target,))
        if result.missing_report_ids:
            raise APINotFoundError(
                "Requested report does not exist.",
                component=_COMPONENT,
                operation="admin_get_report",
                field="report_id",
                context={"report_id": target},
            )
        if len(result.items) != 1:
            raise APIInternalError(
                "Single-report query returned an inconsistent cardinality.",
                component=_COMPONENT,
                operation="admin_get_report",
                context={"result_count": len(result.items)},
            )

        manifest = result.items[0]
        logger.info(
            {
                "event": "api_route_admin_get_report_completed",
                "report_id": manifest.report_id,
                "order_id": manifest.order_id,
                "artifact_count": len(manifest.artifacts),
            }
        )
        return json_response(
            manifest.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def get_review(self, request: Request, review_id: str) -> Response:
        """GET ``/admin/reviews/{review_id}`` -> governance review."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling admin get-review request",
            event="api_route_admin_get_review_start",
            context={"review_id": review_id},
        )
        target = require_api_text(
            review_id,
            field="review_id",
            component=_COMPONENT,
            operation="admin_get_review",
        )
        await authorize_request(
            self._authorize,
            request,
            operation="admin_get_review",
            resource_id=target,
        )

        review = self._review_service.find_review(target)
        if review is None:
            raise APINotFoundError(
                "Requested governance review does not exist.",
                component=_COMPONENT,
                operation="admin_get_review",
                field="review_id",
                context={"review_id": target},
            )

        logger.info(
            {
                "event": "api_route_admin_get_review_completed",
                "review_id": review.review_id,
                "finding_id": review.finding_id,
                "pending": review.is_pending(),
            }
        )
        return json_response(
            review.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def record_decision(self, request: Request, review_id: str) -> Response:
        """POST ``/admin/reviews/{review_id}/decisions`` -> append decision."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling admin governance decision request",
            event="api_route_admin_record_decision_start",
            context={"review_id": review_id},
        )
        target = require_api_text(
            review_id,
            field="review_id",
            component=_COMPONENT,
            operation="admin_record_decision",
        )
        actor = await authorize_request(
            self._authorize,
            request,
            operation="admin_record_review_decision",
            resource_id=target,
        )

        payload = validate_object_fields(
            await read_json_object(request),
            required=("decision_id", "outcome", "reason_code"),
            optional=("rationale", "is_override"),
        )
        decision_id = require_api_text(
            payload["decision_id"],
            field="decision_id",
            component=_COMPONENT,
            operation="admin_record_decision",
        )
        outcome = require_api_text(
            payload["outcome"],
            field="outcome",
            component=_COMPONENT,
            operation="admin_record_decision",
        )
        reason_code = require_api_text(
            payload["reason_code"],
            field="reason_code",
            component=_COMPONENT,
            operation="admin_record_decision",
        )
        rationale = optional_route_text(
            payload.get("rationale"),
            field="rationale",
        )
        is_override = payload.get("is_override", False)
        if not isinstance(is_override, bool):
            raise APIValidationError(
                "is_override must be boolean.",
                component=_COMPONENT,
                operation="admin_record_decision",
                field="is_override",
                context={"received_type": type(is_override).__name__},
            )

        # decided_by comes from the authorization boundary, never from an
        # arbitrary request body.  decided_at is omitted so ReviewService.Clock
        # remains the timestamp authority.
        review = self._review_service.record_decision(
            target,
            decision_id=decision_id,
            outcome=outcome,
            reason_code=reason_code,
            rationale=rationale,
            decided_by=actor,
            is_override=is_override,
        )

        current = review.current_decision()
        logger.info(
            {
                "event": "api_route_admin_record_decision_completed",
                "review_id": review.review_id,
                "finding_id": review.finding_id,
                "decision_id": None if current is None else current.decision_id,
                "outcome": None if current is None else current.outcome.value,
                "is_override": False if current is None else current.is_override,
            }
        )
        return json_response(
            review.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def resolve_orders(self, request: Request, order_ids: Iterable[str]) -> Response:
        """Resolve an explicit authorized admin order-ID set.

        This helper is not registered as a public route because no global
        admin-order search/list read model exists in the current application
        boundary.
        """
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving explicit admin order identifiers",
            event="api_route_admin_resolve_orders_start",
        )
        targets = normalize_route_texts(
            order_ids,
            field="order_ids",
            allow_empty=True,
        )
        for order_id in targets:
            await authorize_request(
                self._authorize,
                request,
                operation="admin_get_order",
                resource_id=order_id,
            )
        result = self._list_orders.execute(targets)
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )

    async def resolve_reports(self, request: Request, report_ids: Iterable[str]) -> Response:
        """Resolve an explicit authorized admin report-ID set.

        This helper is not registered as a public route because the current
        repository/query boundary does not define an admin report search API.
        """
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving explicit admin report identifiers",
            event="api_route_admin_resolve_reports_start",
        )
        targets = normalize_route_texts(
            report_ids,
            field="report_ids",
            allow_empty=True,
        )
        for report_id in targets:
            await authorize_request(
                self._authorize,
                request,
                operation="admin_get_report",
                resource_id=report_id,
            )
        result = self._list_reports.execute(targets)
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteAdmin"]