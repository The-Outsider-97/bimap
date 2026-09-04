"""
FastAPI route for authorized BIMAP report-artifact download grants.

The published BIMAP API uses:

    POST /orders/{order_id}/download/{artifact_id}

to issue a short-lived signed download URL.  The current ``Storage`` port
intentionally has no provider-neutral signed-URL method, and ``ReportManifest``
does not contain concrete object-store keys.  Consequently this route never
constructs bucket paths, guesses storage keys, or opens storage directly.

After per-order authorization and authoritative manifest/artifact resolution, an
injected ``DownloadURLIssuer`` performs the deployment-specific capability
issuance.  It receives only the validated order/report/artifact context and must
return a bounded ``DownloadGrant``.  This seam can later be replaced by an
explicit application port/use case without changing the public route contract.
"""

from __future__ import annotations

import inspect

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeAlias
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response, status

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.queries.list_reports import ListReports, ReportListResult
from ...contracts.report_manifest import ReportArtifactContract, ReportManifest
from ...domain.utils.domain_errors import DomainError
from ...domain.utils.domain_helpers import ensure_utc_datetime, format_utc_datetime
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Downloads")
printer = PrettyPrinter()

_COMPONENT = "api_route_downloads"


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    """Validated public capability returned by a download-link issuer."""

    download_url: str
    expires_at: datetime | str

    def __post_init__(self) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating download grant",
            event="api_route_downloads_grant_validate_start",
        )
        download_url = require_api_text(
            self.download_url,
            field="download_url",
            error_type=APIConfigurationError,
            component=_COMPONENT,
            operation="validate_download_grant",
            max_length=8192,
        )

        try:
            parsed = urlsplit(download_url)
        except ValueError as exc:
            raise APIConfigurationError(
                "Download issuer returned an invalid URL.",
                component=_COMPONENT,
                operation="validate_download_grant",
                field="download_url",
                cause=exc,
            ) from exc

        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise APIConfigurationError(
                "Download issuer must return an absolute HTTPS URL without userinfo or fragment.",
                component=_COMPONENT,
                operation="validate_download_grant",
                field="download_url",
            )

        try:
            expires = ensure_utc_datetime(
                self.expires_at,
                field="expires_at",
            )
        except DomainError as exc:
            raise APIConfigurationError(
                "Download issuer returned an invalid expiry timestamp.",
                component=_COMPONENT,
                operation="validate_download_grant",
                field="expires_at",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        object.__setattr__(self, "download_url", download_url)
        object.__setattr__(self, "expires_at", format_utc_datetime(expires))

    def to_dict(self) -> dict[str, str]:
        """Return the client-facing signed-capability projection."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing download grant",
            event="api_route_downloads_grant_to_dict_start",
        )
        return {
            "download_url": self.download_url,
            "expires_at": str(self.expires_at),
        }


DownloadURLIssuer: TypeAlias = Callable[
    [Request, str, ReportManifest, ReportArtifactContract],
    DownloadGrant | Awaitable[DownloadGrant],
]


def _require_download_issuer(issuer: DownloadURLIssuer) -> DownloadURLIssuer:
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating download URL issuer",
        event="api_route_downloads_issuer_validate_start",
    )
    if not callable(issuer):
        raise APIConfigurationError(
            "download_url_issuer must be callable.",
            component=_COMPONENT,
            operation="initialize",
            field="download_url_issuer",
            context={"received_type": type(issuer).__name__},
        )
    return issuer


class RouteDownloads:
    """Dependency-injected short-lived download-grant route group."""

    __slots__ = (
        "router",
        "_list_reports",
        "_report_id_resolver",
        "_download_url_issuer",
        "_authorize",
    )

    def __init__(
        self,
        list_reports: ListReports,
        *,
        report_id_resolver: OrderReportIdResolver,
        download_url_issuer: DownloadURLIssuer,
        authorizer: RouteAuthorizer,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing downloads API routes",
            event="api_route_downloads_init_start",
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
        self._download_url_issuer = _require_download_issuer(download_url_issuer)
        self._authorize = require_route_authorizer(authorizer)

        router = APIRouter(prefix="/orders", tags=["downloads"])
        router.add_api_route(
            "/{order_id}/download/{artifact_id}",
            self.issue,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="issue_report_download",
        )
        self.router = router

        logger.info(
            {
                "event": "api_route_downloads_initialized",
                "registered_route_count": 1,
            }
        )

    async def _resolve_reports(
        self,
        request: Request,
        order_id: str,
    ) -> ReportListResult:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving download report manifests",
            event="api_route_downloads_reports_resolve_start",
            context={"order_id": order_id},
        )
        report_ids = await resolve_order_report_ids(
            self._report_id_resolver,
            request,
            order_id,
        )
        result = self._list_reports.execute(
            report_ids,
            expected_order_id=order_id,
        )
        if result.missing_report_ids:
            raise APIInternalError(
                "Authorized report resolver referenced missing persisted manifests.",
                component=_COMPONENT,
                operation="resolve_reports",
                context={
                    "order_id": order_id,
                    "missing_report_count": result.missing_count,
                },
            )
        return result

    @staticmethod
    def _select_artifact(
        manifests: tuple[ReportManifest, ...],
        artifact_id: str,
    ) -> tuple[ReportManifest, ReportArtifactContract]:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Selecting report artifact for download",
            event="api_route_downloads_artifact_select_start",
            context={"artifact_id": artifact_id},
        )
        matches: list[tuple[ReportManifest, ReportArtifactContract]] = []
        for manifest in manifests:
            artifact = manifest.artifact(artifact_id)
            if artifact is not None:
                matches.append((manifest, artifact))

        if not matches:
            raise APINotFoundError(
                "Requested report artifact does not exist.",
                component=_COMPONENT,
                operation="select_artifact",
                field="artifact_id",
                context={"artifact_id": artifact_id},
            )
        if len(matches) > 1:
            # The public route contains no report_id/version selector.  Choosing
            # a "latest" manifest would invent an ordering/version policy.
            raise APIConflictError(
                "Artifact identifier is ambiguous across report manifests.",
                component=_COMPONENT,
                operation="select_artifact",
                field="artifact_id",
                context={
                    "artifact_id": artifact_id,
                    "matching_report_count": len(matches),
                },
            )
        return matches[0]

    async def _issue_grant(
        self,
        request: Request,
        order_id: str,
        manifest: ReportManifest,
        artifact: ReportArtifactContract,
    ) -> DownloadGrant:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Issuing signed report download capability",
            event="api_route_downloads_issue_grant_start",
            context={
                "order_id": order_id,
                "report_id": manifest.report_id,
                "artifact_id": artifact.artifact_id,
            },
        )
        try:
            result = self._download_url_issuer(
                request,
                order_id,
                manifest,
                artifact,
            )
            if inspect.isawaitable(result):
                result = await result
        except APIError:
            raise
        except Exception as exc:
            raise APIInternalError(
                "Download URL issuer failed outside the BIMAP API error contract.",
                component=_COMPONENT,
                operation="issue_download_grant",
                context={
                    "order_id": order_id,
                    "report_id": manifest.report_id,
                    "artifact_id": artifact.artifact_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if not isinstance(result, DownloadGrant):
            raise APIInternalError(
                "Download URL issuer returned an unsupported result type.",
                component=_COMPONENT,
                operation="issue_download_grant",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result

    async def issue(
        self,
        request: Request,
        order_id: str,
        artifact_id: str,
    ) -> Response:
        """POST ``/orders/{order_id}/download/{artifact_id}`` -> signed grant."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling report download request",
            event="api_route_downloads_issue_start",
            context={
                "order_id": order_id,
                "artifact_id": artifact_id,
            },
        )
        target_order = require_api_text(
            order_id,
            field="order_id",
            component=_COMPONENT,
            operation="issue_download",
        )
        target_artifact = require_api_text(
            artifact_id,
            field="artifact_id",
            component=_COMPONENT,
            operation="issue_download",
        )

        await authorize_request(
            self._authorize,
            request,
            operation="download_report_artifact",
            resource_id=target_order,
        )

        reports = await self._resolve_reports(request, target_order)
        manifest, artifact = self._select_artifact(
            reports.items,
            target_artifact,
        )
        grant = await self._issue_grant(
            request,
            target_order,
            manifest,
            artifact,
        )

        # Never log the signed URL; query strings commonly contain bearer-like
        # signatures/credentials.
        logger.info(
            {
                "event": "api_route_downloads_issue_completed",
                "order_id": target_order,
                "report_id": manifest.report_id,
                "artifact_id": artifact.artifact_id,
                "expires_at": grant.expires_at,
            }
        )
        return json_response(
            {
                "report_id": manifest.report_id,
                "artifact": artifact.to_dict(),
                **grant.to_dict(),
            },
            headers={"Cache-Control": "no-store"},
        )


__all__ = [
    "DownloadGrant",
    "DownloadURLIssuer",
    "RouteDownloads",
]