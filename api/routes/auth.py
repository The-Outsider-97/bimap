"""FastAPI authentication routes for BIMAP customer accounts."""

from __future__ import annotations

import math

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response, status # type: ignore

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ...app.utils.app_errors import AppValidationError
from ...app.ports.authentication import AuthSession, VerificationFailure
from ...app.services.account_service import AccountProfileView
from ...app.services.authentication_service import AuthenticationService, LoginFailure
from ...domain.accounts.models import Account
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Authentication")
printer = PrettyPrinter()

_COMPONENT = "api_route_auth"
SESSION_COOKIE_NAME = "bimap_session"
_SESSION_COOKIE_PATH = "/"
_SESSION_COOKIE_SAMESITE = "lax"


def account_profile_to_public_dict(profile: AccountProfileView) -> dict[str, Any]:
    """Map the transport-neutral application projection to the frontend contract."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Projecting public account profile",
        event="api_route_auth_profile_project_start",
    )
    if not isinstance(profile, AccountProfileView):
        raise APIInternalError(
            "Account profile projection has an unsupported type.",
            component=_COMPONENT,
            operation="project_profile",
            field="profile",
            context={"received_type": type(profile).__name__},
        )

    return {
        "userId": profile.user_id,
        "username": profile.username,
        "name": profile.name,
        "surname": profile.surname,
        "occupation": profile.occupation,
        "business": profile.business,
        "phoneE164": profile.phone_e164,
        "email": profile.email,
        "avatarUrl": profile.avatar_url,
        "plan": profile.plan.value,
    }


def session_token_from_request(request: Request, *, required: bool = True) -> str | None:
    """Read the opaque HttpOnly BIMAP session cookie."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Reading BIMAP session cookie",
        event="api_route_auth_session_cookie_read_start",
        context={"required": required},
    )
    if not isinstance(request, Request):
        raise APIConfigurationError(
            "Session resolution requires a FastAPI Request.",
            component=_COMPONENT,
            operation="read_session_cookie",
            field="request",
        )
    if not isinstance(required, bool):
        raise APIConfigurationError(
            "required must be boolean.",
            component=_COMPONENT,
            operation="read_session_cookie",
            field="required",
        )

    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if raw is None or not raw.strip():
        if required:
            raise APIUnauthorizedError(
                "BIMAP session cookie is missing.",
                component=_COMPONENT,
                operation="read_session_cookie",
            )
        return None

    return require_api_text(
        raw,
        field=SESSION_COOKIE_NAME,
        error_type=APIUnauthorizedError,
        component=_COMPONENT,
        operation="read_session_cookie",
        max_length=8192,
    )


def resolve_authenticated_account(authentication: AuthenticationService, request: Request) -> Account:
    """Resolve the request cookie to one active verified BIMAP account."""
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Resolving authenticated BIMAP account",
        event="api_route_auth_account_resolve_start",
    )
    if not isinstance(authentication, AuthenticationService):
        raise APIConfigurationError(
            "authentication must be an AuthenticationService.",
            component=_COMPONENT,
            operation="resolve_authenticated_account",
            field="authentication",
        )

    token = session_token_from_request(request, required=True)
    assert token is not None
    account = authentication.resolve_session_account(token)
    if account is None:
        raise APIUnauthorizedError(
            "BIMAP session is invalid or expired.",
            component=_COMPONENT,
            operation="resolve_authenticated_account",
        )
    return account


def _set_session_cookie(response: Response, request: Request, session: AuthSession) -> None:
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Setting BIMAP session cookie",
        event="api_route_auth_session_cookie_set_start",
    )
    if not isinstance(session, AuthSession):
        raise APIInternalError(
            "Authentication result omitted a valid session.",
            component=_COMPONENT,
            operation="set_session_cookie",
            field="session",
        )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session.access_token,
        expires=session.expires_at,
        path=_SESSION_COOKIE_PATH,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite=_SESSION_COOKIE_SAMESITE,
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    announce_api_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Clearing BIMAP session cookie",
        event="api_route_auth_session_cookie_clear_start",
    )
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path=_SESSION_COOKIE_PATH,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite=_SESSION_COOKIE_SAMESITE,
    )


def _require_password(value: Any) -> str:
    if not isinstance(value, str):
        raise APIValidationError(
            "Password must be text.",
            component=_COMPONENT,
            operation="validate_password",
            field="password",
        )
    if not value or len(value) > 4096:
        raise APIValidationError(
            "Password length is invalid.",
            component=_COMPONENT,
            operation="validate_password",
            field="password",
        )
    return value

def _signup_validation_message(error: AppValidationError) -> str:
    """
    Map signup validation fields to safe actionable client messages.

    Messages intentionally avoid disclosing which existing account owns
    an identifier.
    """
    field = getattr(error, "field", None)

    if field == "password":
        return ("Password does not satisfy the required security policy.")

    if field == "phone_e164":
        return (
            "Phone verification could not be started. "
            "Check the selected country and phone number, "
            "then try again."
        )

    if field in {
        "username",
        "email",
    }:
        return (
            "An account already exists for one or more "
            "of the supplied sign-up identifiers."
        )

    return ("One or more sign-up fields are invalid.")

def _signup_response_payload(result: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "verificationRequired": True,
        "username": result.account.username,
    }
    if result.verification.email_masked is not None:
        payload["emailMasked"] = result.verification.email_masked
    if result.verification.phone_masked is not None:
        payload["phoneMasked"] = result.verification.phone_masked
    return payload


class RouteAuth:
    """Dependency-injected authentication route group."""

    __slots__ = ("router", "_authentication")

    def __init__(self, authentication: AuthenticationService) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing authentication API routes",
            event="api_route_auth_init_start",
        )
        if not isinstance(authentication, AuthenticationService):
            raise APIConfigurationError(
                "authentication must be an AuthenticationService.",
                component=_COMPONENT,
                operation="initialize",
                field="authentication",
                context={"received_type": type(authentication).__name__},
            )

        self._authentication = authentication
        router = APIRouter(prefix="/auth", tags=["authentication"])
        router.add_api_route(
            "/signup",
            self.signup,
            methods=["POST"],
            status_code=status.HTTP_202_ACCEPTED,
            response_class=Response,
            name="signup",
        )
        router.add_api_route(
            "/verify-signup",
            self.verify_signup,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="verify_signup",
        )
        router.add_api_route(
            "/login",
            self.login,
            methods=["POST"],
            status_code=status.HTTP_200_OK,
            response_class=Response,
            name="login",
        )
        router.add_api_route(
            "/logout",
            self.logout,
            methods=["POST"],
            status_code=status.HTTP_204_NO_CONTENT,
            response_class=Response,
            name="logout",
        )
        router.add_api_route(
            "/resend-signup-codes",
            self.resend_signup_codes,
            methods=["POST"],
            status_code=status.HTTP_202_ACCEPTED,
            response_class=Response,
            name="resend_signup_codes",
        )
        self.router = router

        logger.info({"event": "api_route_auth_initialized", "registered_route_count": 5})

    async def signup(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling signup request",
            event="api_route_auth_signup_start",
        )
        payload = validate_object_fields(
            await read_json_object(request),
            required=(
                "name",
                "surname",
                "country",
                "phoneE164",
                "username",
                "email",
                "password",
                ),
            optional=(
                "occupation",
                "business",
                "smscode",
                ),
        )

        try:
            result = self._authentication.sign_up(
                name=require_api_text(
                    payload["name"],
                    field="name",
                    component=_COMPONENT,
                    operation="signup",
                    max_length=128,
                ),
                surname=require_api_text(
                    payload["surname"],
                    field="surname",
                    component=_COMPONENT,
                    operation="signup",
                    max_length=128,
                ),
                occupation=optional_route_text(
                    payload.get("occupation"),
                    field="occupation",
                    max_length=256,
                ),
                business=optional_route_text(
                    payload.get("business"),
                    field="business",
                    max_length=256,
                ),
                country=require_api_text(
                    payload["country"],
                    field="country",
                    component=_COMPONENT,
                    operation="signup",
                    max_length=2,
                ),
                phone_e164=require_api_text(
                    payload["phoneE164"],
                    field="phoneE164",
                    component=_COMPONENT,
                    operation="signup",
                    max_length=16,
                ),
                username=require_api_text(
                    payload["username"],
                    field="username",
                    component=_COMPONENT,
                    operation="signup",
                    max_length=64,
                ),
                email=require_api_text(
                    payload["email"],
                    field="email",
                    component=_COMPONENT,
                    operation="signup",
                    max_length=254,
                ),
                password=_require_password(
                    payload["password"]
                ),
            )

        except AppValidationError as exc:
            raise APIValidationError(
                "Signup validation was rejected.",
                public_message=(
                    _signup_validation_message(
                        exc
                    )
                ),
                component=_COMPONENT,
                operation="signup",
                field=getattr(
                    exc,
                    "field",
                    None,
                ),
                cause=exc,
            ) from exc

        return json_response(
            _signup_response_payload(result),
            status_code=status.HTTP_202_ACCEPTED,
            headers={"Cache-Control": "no-store"},
        )

    async def verify_signup(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling signup verification request",
            event="api_route_auth_verify_signup_start",
        )
        payload = validate_object_fields(
            await read_json_object(request),
            required=("username", "emailCode"),
            optional=("smsCode",),
        )
        result = self._authentication.verify_signup(
            username=require_api_text(payload["username"], field="username", component=_COMPONENT, operation="verify_signup", max_length=64),
            email_code=require_api_text(payload["emailCode"], field="emailCode", component=_COMPONENT, operation="verify_signup", max_length=256),
            sms_code=optional_route_text(payload.get("smsCode"), field="smsCode", max_length=16),
        )

        if not result.verified:
            public_message = "One or more verification codes are invalid."
            if result.failure is VerificationFailure.EXPIRED:
                public_message = "Verification code has expired. Request new codes."
            elif result.failure is VerificationFailure.ATTEMPTS_EXHAUSTED:
                public_message = "Verification attempts are exhausted. Request new codes."
            raise APIValidationError(
                "Signup verification was rejected.",
                public_message=public_message,
                component=_COMPONENT,
                operation="verify_signup",
            )

        authenticated = result.authenticated
        assert authenticated is not None
        response = json_response(
            account_profile_to_public_dict(authenticated.profile),
            status_code=status.HTTP_200_OK,
            headers={"Cache-Control": "no-store"},
        )
        _set_session_cookie(response, request, authenticated.session)
        return response

    async def login(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling login request",
            event="api_route_auth_login_start",
        )
        payload = validate_object_fields(
            await read_json_object(request),
            required=("username", "password"),
        )
        result = self._authentication.login(
            username=require_api_text(payload["username"], field="username", component=_COMPONENT, operation="login", max_length=64),
            password=_require_password(payload["password"]),
        )

        if not result.accepted:
            if result.failure is LoginFailure.INVALID_CREDENTIALS:
                raise APIUnauthorizedError(
                    "Credentials were rejected.",
                    public_message="Invalid username or password.",
                    component=_COMPONENT,
                    operation="login",
                )
            if result.failure is LoginFailure.LOCKED:
                retry_after_seconds: int | None = None
                if result.retry_after is not None:
                    retry_after_seconds = max(
                        0,
                        math.ceil(
                            (result.retry_after - datetime.now(timezone.utc)).total_seconds()
                        ),
                    )
                raise APIRateLimitError(
                    "Authentication account is temporarily locked.",
                    retry_after_seconds=retry_after_seconds,
                    public_message="Account is temporarily locked. Try again later.",
                    component=_COMPONENT,
                    operation="login",
                )
            if result.failure is LoginFailure.UNVERIFIED:
                raise APIForbiddenError(
                    "Authentication requires completed signup verification.",
                    public_message="Account verification is required before login.",
                    component=_COMPONENT,
                    operation="login",
                )
            if result.failure in {
                LoginFailure.DISABLED,
                LoginFailure.SUSPENDED,
                LoginFailure.CLOSED,
            }:
                raise APIForbiddenError(
                    "Account is not permitted to authenticate.",
                    public_message="This account cannot currently log in.",
                    component=_COMPONENT,
                    operation="login",
                )
            raise APIInternalError(
                "Login result omitted a supported outcome.",
                component=_COMPONENT,
                operation="login",
            )

        authenticated = result.authenticated
        assert authenticated is not None
        response = json_response(
            account_profile_to_public_dict(authenticated.profile),
            status_code=status.HTTP_200_OK,
            headers={"Cache-Control": "no-store"},
        )
        _set_session_cookie(response, request, authenticated.session)
        return response

    async def logout(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling logout request",
            event="api_route_auth_logout_start",
        )
        token = session_token_from_request(request, required=False)
        if token is not None:
            self._authentication.logout(token)

        response = Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )
        _clear_session_cookie(response, request)
        return response

    async def resend_signup_codes(self, request: Request) -> Response:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling signup verification resend request",
            event="api_route_auth_resend_signup_start",
        )
        payload = validate_object_fields(
            await read_json_object(request),
            required=("username",),
        )
        result = self._authentication.resend_signup_codes(
            require_api_text(
                payload["username"],
                field="username",
                component=_COMPONENT,
                operation="resend_signup_codes",
                max_length=64,
            )
        )
        return json_response(
            _signup_response_payload(result),
            status_code=status.HTTP_202_ACCEPTED,
            headers={"Cache-Control": "no-store"},
        )


__all__ = [
    "SESSION_COOKIE_NAME",
    "account_profile_to_public_dict",
    "session_token_from_request",
    "resolve_authenticated_account",
    "RouteAuth",
]
