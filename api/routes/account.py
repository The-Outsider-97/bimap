"""FastAPI account-profile routes for BIMAP customer accounts."""

from __future__ import annotations

import inspect

from collections.abc import Mapping
from typing import Any
from fastapi import APIRouter, Request, Response, status # type: ignore

from ._shared import *
from ..dependencies import AccountAvatarUploader, AccountSummaryResolver
from .auth import account_profile_to_public_dict, resolve_authenticated_account
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.services.account_service import AccountProfileView, AccountService
from ...app.services.authentication_service import AuthenticationService
from ...domain.accounts.models import Account
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Account")
printer = PrettyPrinter()

_COMPONENT = "api_route_account"


class RouteAccount:
    """Dependency-injected account profile/summary route group."""

    __slots__ = (
        "router",
        "_account_service",
        "_authentication",
        "_summary_resolver",
        "_avatar_uploader",
    )

    def __init__(
        self,
        account_service: AccountService,
        authentication: AuthenticationService,
        *,
        summary_resolver: AccountSummaryResolver | None = None,
        avatar_uploader: AccountAvatarUploader | None = None,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing account API routes",
            event="api_route_account_init_start",
        )
        if not isinstance(account_service, AccountService):
            raise APIConfigurationError(
                "account_service must be an AccountService.",
                component=_COMPONENT,
                operation="initialize",
                field="account_service",
                context={"received_type": type(account_service).__name__},
            )
        if not isinstance(authentication, AuthenticationService):
            raise APIConfigurationError(
                "authentication must be an AuthenticationService.",
                component=_COMPONENT,
                operation="initialize",
                field="authentication",
                context={"received_type": type(authentication).__name__},
            )
        if summary_resolver is not None and not callable(summary_resolver):
            raise APIConfigurationError(
                "summary_resolver must be callable or None.",
                component=_COMPONENT,
                operation="initialize",
                field="summary_resolver",
            )
        if avatar_uploader is not None and not callable(avatar_uploader):
            raise APIConfigurationError(
                "avatar_uploader must be callable or None.",
                component=_COMPONENT,
                operation="initialize",
                field="avatar_uploader",
            )

        self._account_service = account_service
        self._authentication = authentication
        self._summary_resolver = summary_resolver
        self._avatar_uploader = avatar_uploader

        router = APIRouter(prefix="/account", tags=["account"])
        router.add_api_route(
            "/me",
            self.me,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="get_current_account",
        )
        router.add_api_route(
            "/summary",
            self.summary,
            methods=["GET"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="get_account_summary",
        )
        router.add_api_route(
            "/avatar",
            self.upload_avatar,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="upload_account_avatar",
        )
        self.router = router

        logger.info({"event": "api_route_account_initialized", "registered_route_count": 3})

    async def me(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling current-account request",
            event="api_route_account_me_start",
        )
        account = resolve_authenticated_account(self._authentication, request)
        profile = AccountProfileView.from_account(account)
        return json_response(
            account_profile_to_public_dict(profile),
            headers={"Cache-Control": "no-store"},
        )

    async def summary(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling account-summary request",
            event="api_route_account_summary_start",
        )
        account = resolve_authenticated_account(self._authentication, request)
        resolver = self._summary_resolver
        if resolver is None:
            raise APIServiceUnavailableError(
                "Account summary resolver is not configured.",
                component=_COMPONENT,
                operation="summary",
            )

        try:
            result = resolver(request, account)
            if inspect.isawaitable(result):
                result = await result
        except APIError:
            raise
        except Exception as exc:
            raise APIInternalError(
                "Account summary resolver failed.",
                component=_COMPONENT,
                operation="summary",
                context={"lower_error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        if not isinstance(result, Mapping):
            raise APIInternalError(
                "Account summary resolver returned an unsupported result.",
                component=_COMPONENT,
                operation="summary",
                field="result",
                context={"received_type": type(result).__name__},
            )

        required = {"audits", "purchases", "currentPlan", "usage", "rewards"}
        returned = set(result)
        if returned != required:
            raise APIInternalError(
                "Account summary resolver returned an invalid top-level contract.",
                component=_COMPONENT,
                operation="summary",
                field="result",
                context={
                    "missing": tuple(sorted(required - returned)),
                    "unknown": tuple(sorted(returned - required)),
                },
            )

        return json_response(
            dict(result),
            headers={"Cache-Control": "no-store"},
        )

    async def upload_avatar(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling account-avatar upload request",
            event="api_route_account_avatar_start",
        )
        account = resolve_authenticated_account(self._authentication, request)
        uploader = self._avatar_uploader
        if uploader is None:
            raise APIServiceUnavailableError(
                "Account avatar upload is not configured.",
                component=_COMPONENT,
                operation="upload_avatar",
            )

        try:
            avatar_url = uploader(request, account)
            if inspect.isawaitable(avatar_url):
                avatar_url = await avatar_url
        except APIError:
            raise
        except Exception as exc:
            raise APIInternalError(
                "Account avatar uploader failed.",
                component=_COMPONENT,
                operation="upload_avatar",
                context={"lower_error_type": type(exc).__name__},
                cause=exc,
            ) from exc

        if avatar_url is not None and not isinstance(avatar_url, str):
            raise APIInternalError(
                "Account avatar uploader returned an unsupported URL value.",
                component=_COMPONENT,
                operation="upload_avatar",
                field="avatar_url",
                context={"received_type": type(avatar_url).__name__},
            )

        updated = self._account_service.set_avatar_url(
            account.account_id,
            avatar_url,
        )
        profile = AccountProfileView.from_account(updated)
        return json_response(
            account_profile_to_public_dict(profile),
            headers={"Cache-Control": "no-store"},
        )


__all__ = [
    "AccountSummaryResolver",
    "AccountAvatarUploader",
    "RouteAccount",
]
