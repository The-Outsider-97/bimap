"""
FastAPI report-manifest routes for BIMAP.

The current application ``Repository`` port and ``ListReports`` query expose
point reads only; they intentionally do not define a global/customer report
scan, pagination policy, or persistence-side ownership filter.  This route
therefore obtains the explicit report identifiers for one already-authorized
order through an injected ``OrderReportIdResolver`` and then delegates manifest
resolution/binding checks to ``ListReports``.

This preserves the published customer endpoint:

    GET /orders/{order_id}/reports

while avoiding an invented ``Repository.list_reports()`` method.

Report release is deliberately not exposed here.  ``ReleaseReport`` currently
requires authoritative findings/evidence, governance records, report identities,
and explicit storage object identities.  Treating arbitrary HTTP input as those
authoritative values would bypass application composition and governance rather
than producing a safe admin action.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.queries.list_reports import ListReports
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Reports")
printer = PrettyPrinter()

_COMPONENT = "api_route_reports"


class RouteReports:
    """Dependency-injected customer report-manifest route group."""

    __slots__ = ("router", "_list_reports", "_report_id_resolver", "_authorize")

    def __init__(
        self,
        list_reports: ListReports,
        *,
        report_id_resolver: OrderReportIdResolver,
        authorizer: RouteAuthorizer,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing reports API routes",
            event="api_route_reports_init_start",
        )
        if not isinstance(list_reports, ListReports):
            raise APIConfigurationError(
                "list_reports must be a ListReports query handler.",
                component=_COMPONENT,
                operation="initialize",
                field="list_reports",
                context={"received_type": type(list_reports).__name__},
            )

        self._list_reports = list_reports
        self._report_id_resolver = require_order_report_id_resolver(
            report_id_resolver
        )
        self._authorize = require_route_authorizer(authorizer)

        router = APIRouter(prefix="/orders", tags=["reports"])
        router.add_api_route(
            "/{order_id}/reports",
            self.list,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="list_order_reports",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_reports_initialized",
                "registered_route_count": 1,
            }
        )

    async def list(self, request: Request, order_id: str) -> Response:
        """GET ``/orders/{order_id}/reports`` -> authorized report manifests."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling list-order-reports request",
            event="api_route_reports_list_start",
            context={"order_id": order_id},
        )
        target = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="list_reports",
        )

        # Authorize before resolving report identifiers so this endpoint cannot
        # become an order/report enumeration oracle.
        await authorize_request(
            self._authorize,
            request,
            operation="list_reports",
            resource_id=target,
        )

        report_ids = await resolve_order_report_ids(
            self._report_id_resolver,
            request,
            target,
        )
        result = self._list_reports.execute(
            report_ids,
            expected_order_id=target,
        )

        if result.missing_report_ids:
            raise APIInternalError(
                "Authorized report resolver referenced missing persisted manifests.",
                component=_COMPONENT,
                operation="list_reports",
                context={
                    "order_id": target,
                    "missing_report_count": result.missing_count,
                },
            )

        logger.info(
            {
                "event": "api_route_reports_list_completed",
                "order_id": target,
                "report_count": result.found_count,
            }
        )
        return json_response(
            result.to_dict(),
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteReports"]