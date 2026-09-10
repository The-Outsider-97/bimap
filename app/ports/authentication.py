"""
BIMAP authentication application port.

This port is the dependency-inversion boundary between BIMAP application
orchestration and the concrete authentication/verification subsystem. The
concrete deployment may adapt SLAI ``src.functions.auth.AuthService`` together
with email/SMS verification providers, but those implementations must never be
imported by this module.

Expected business outcomes such as invalid credentials, lockout, an existing
identity, or an incorrect verification code are represented as immutable result
values. Infrastructure/provider failures remain exceptions. This distinction
prevents routine authentication denial from being misclassified as an outage.

Sensitive-data rule
-------------------
Passwords, verification codes, access tokens, refresh tokens, and provider
payloads must never be copied into logs or diagnostic contexts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, TypeVar

from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Authentication Port")
printer = PrettyPrinter()

_COMPONENT = "authentication"
T = TypeVar("T")


class IdentityCreationStatus(str, Enum):
    CREATED = "created"
    USERNAME_EXISTS = "username_exists"
    EMAIL_EXISTS = "email_exists"


class CredentialStatus(str, Enum):
    ACCEPTED = "accepted"
    INVALID = "invalid"
    LOCKED = "locked"
    DISABLED = "disabled"


class VerificationFailure(str, Enum):
    INVALID_CODE = "invalid_code"
    EXPIRED = "expired"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"


@dataclass(frozen=True, slots=True)
class AuthenticationIdentity:
    """Provider-neutral identity created by the authentication subsystem."""

    auth_user_id: str
    username: str
    email: str

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating authentication identity",
            event="authentication_identity_validate_start",
        )
        object.__setattr__(
            self,
            "auth_user_id",
            require_app_text(
                self.auth_user_id,
                field="auth_user_id",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_identity",
                max_length=512,
            ),
        )
        object.__setattr__(
            self,
            "username",
            require_app_text(
                self.username,
                field="username",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_identity",
                max_length=64,
            ),
        )
        object.__setattr__(
            self,
            "email",
            require_app_text(
                self.email,
                field="email",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_identity",
                max_length=254,
            ),
        )


@dataclass(frozen=True, slots=True)
class IdentityCreationResult:
    """Expected outcome of attempting to create one authentication identity."""

    status: IdentityCreationStatus
    identity: AuthenticationIdentity | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating identity creation result",
            event="authentication_identity_creation_result_validate_start",
        )
        if isinstance(self.status, IdentityCreationStatus):
            status = self.status
        else:
            normalized_status = require_app_text(
                self.status,
                field="status",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_identity_creation_result",
            ).lower()
            try:
                status = IdentityCreationStatus(normalized_status)
            except ValueError as exc:
                raise AppIntegrityError(
                    "Authentication adapter returned unsupported identity creation status.",
                    component=_COMPONENT,
                    operation="validate_identity_creation_result",
                    field="status",
                    context={"received": normalized_status},
                    cause=exc,
                ) from exc

        if status is IdentityCreationStatus.CREATED:
            if not isinstance(self.identity, AuthenticationIdentity):
                raise AppIntegrityError(
                    "Created identity result requires AuthenticationIdentity.",
                    component=_COMPONENT,
                    operation="validate_identity_creation_result",
                    field="identity",
                )
        elif self.identity is not None:
            raise AppIntegrityError(
                "Identity conflict result must not include a created identity.",
                component=_COMPONENT,
                operation="validate_identity_creation_result",
                field="identity",
            )

        object.__setattr__(self, "status", status)

    @property
    def created(self) -> bool:
        return self.status is IdentityCreationStatus.CREATED


@dataclass(frozen=True, slots=True)
class VerificationDispatch:
    """Non-sensitive metadata for a newly issued signup-verification attempt."""

    challenge_id: str
    expires_at: datetime
    email_masked: str | None = None
    phone_masked: str | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating signup verification dispatch",
            event="authentication_verification_dispatch_validate_start",
        )
        object.__setattr__(
            self,
            "challenge_id",
            require_app_text(
                self.challenge_id,
                field="challenge_id",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_verification_dispatch",
                max_length=512,
            ),
        )
        object.__setattr__(
            self,
            "expires_at",
            ensure_app_utc_datetime(
                self.expires_at,
                field="expires_at",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_verification_dispatch",
            ),
        )
        object.__setattr__(
            self,
            "email_masked",
            optional_app_text(
                self.email_masked,
                field="email_masked",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_verification_dispatch",
                max_length=254,
            ),
        )
        object.__setattr__(
            self,
            "phone_masked",
            optional_app_text(
                self.phone_masked,
                field="phone_masked",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_verification_dispatch",
                max_length=32,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "expires_at": format_app_utc_datetime(self.expires_at),
            "email_masked": self.email_masked,
            "phone_masked": self.phone_masked,
        }


@dataclass(frozen=True, slots=True)
class SignupVerificationResult:
    """Expected dual-channel signup-verification outcome."""

    email_verified: bool
    phone_verified: bool
    failure: VerificationFailure | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating signup verification result",
            event="authentication_signup_verification_result_validate_start",
        )
        if not isinstance(self.email_verified, bool) or not isinstance(self.phone_verified, bool):
            raise AppIntegrityError(
                "Verification channel results must be boolean.",
                component=_COMPONENT,
                operation="validate_signup_verification_result",
                field="verification",
            )

        failure = self.failure
        if failure is not None and not isinstance(failure, VerificationFailure):
            try:
                failure = VerificationFailure(
                    require_app_text(
                        failure,
                        field="failure",
                        error_type=AppIntegrityError,
                        component=_COMPONENT,
                        operation="validate_signup_verification_result",
                    ).lower()
                )
            except ValueError as exc:
                raise AppIntegrityError(
                    "Authentication adapter returned unsupported verification failure.",
                    component=_COMPONENT,
                    operation="validate_signup_verification_result",
                    field="failure",
                    cause=exc,
                ) from exc

        if self.email_verified and self.phone_verified and failure is not None:
            raise AppIntegrityError(
                "Successful signup verification cannot carry a failure reason.",
                component=_COMPONENT,
                operation="validate_signup_verification_result",
                field="failure",
            )

        if not (self.email_verified and self.phone_verified) and failure is None:
            raise AppIntegrityError(
                "Incomplete signup verification requires a failure reason.",
                component=_COMPONENT,
                operation="validate_signup_verification_result",
                field="failure",
            )

        object.__setattr__(self, "failure", failure)

    @property
    def verified(self) -> bool:
        return self.email_verified and self.phone_verified


@dataclass(frozen=True, slots=True)
class CredentialVerification:
    """Provider-neutral credential check without issuing a session."""

    status: CredentialStatus
    auth_user_id: str | None = None
    retry_after: datetime | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating credential verification result",
            event="authentication_credentials_result_validate_start",
        )
        if isinstance(self.status, CredentialStatus):
            status = self.status
        else:
            normalized_status = require_app_text(
                self.status,
                field="status",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_credentials_result",
            ).lower()
            try:
                status = CredentialStatus(normalized_status)
            except ValueError as exc:
                raise AppIntegrityError(
                    "Authentication adapter returned unsupported credential status.",
                    component=_COMPONENT,
                    operation="validate_credentials_result",
                    field="status",
                    context={"received": normalized_status},
                    cause=exc,
                ) from exc

        auth_user_id = (
            None
            if self.auth_user_id is None
            else require_app_text(
                self.auth_user_id,
                field="auth_user_id",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_credentials_result",
                max_length=512,
            )
        )
        retry_after = (
            None
            if self.retry_after is None
            else ensure_app_utc_datetime(
                self.retry_after,
                field="retry_after",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_credentials_result",
            )
        )

        if status is CredentialStatus.ACCEPTED and auth_user_id is None:
            raise AppIntegrityError(
                "Accepted credential result requires auth_user_id.",
                component=_COMPONENT,
                operation="validate_credentials_result",
                field="auth_user_id",
            )

        if status is not CredentialStatus.ACCEPTED and auth_user_id is not None:
            raise AppIntegrityError(
                "Rejected credential result must not expose auth_user_id.",
                component=_COMPONENT,
                operation="validate_credentials_result",
                field="auth_user_id",
            )

        if status is not CredentialStatus.LOCKED and retry_after is not None:
            raise AppIntegrityError(
                "retry_after is valid only for locked credentials.",
                component=_COMPONENT,
                operation="validate_credentials_result",
                field="retry_after",
            )

        object.__setattr__(self, "status", status)
        object.__setattr__(self, "auth_user_id", auth_user_id)
        object.__setattr__(self, "retry_after", retry_after)

    @property
    def accepted(self) -> bool:
        return self.status is CredentialStatus.ACCEPTED


@dataclass(frozen=True, slots=True)
class AuthSession:
    """Opaque authentication session issued to a verified BIMAP account."""

    auth_user_id: str
    access_token: str
    expires_at: datetime
    refresh_token: str | None = None
    refresh_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating authentication session",
            event="authentication_session_validate_start",
            context={"has_refresh_token": self.refresh_token is not None},
        )
        object.__setattr__(
            self,
            "auth_user_id",
            require_app_text(
                self.auth_user_id,
                field="auth_user_id",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_session",
                max_length=512,
            ),
        )
        object.__setattr__(
            self,
            "access_token",
            require_app_text(
                self.access_token,
                field="access_token",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_session",
                max_length=8192,
            ),
        )
        expires_at = ensure_app_utc_datetime(
            self.expires_at,
            field="expires_at",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_session",
        )
        refresh_token = (
            None
            if self.refresh_token is None
            else require_app_text(
                self.refresh_token,
                field="refresh_token",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_session",
                max_length=8192,
            )
        )
        refresh_expires_at = (
            None
            if self.refresh_expires_at is None
            else ensure_app_utc_datetime(
                self.refresh_expires_at,
                field="refresh_expires_at",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_session",
            )
        )

        if (refresh_token is None) != (refresh_expires_at is None):
            raise AppIntegrityError(
                "Refresh token and refresh expiry must either both exist or both be absent.",
                component=_COMPONENT,
                operation="validate_session",
                field="refresh_token",
            )

        if refresh_expires_at is not None and refresh_expires_at <= expires_at:
            raise AppIntegrityError(
                "Refresh expiry must be later than access-token expiry.",
                component=_COMPONENT,
                operation="validate_session",
                field="refresh_expires_at",
            )

        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "refresh_token", refresh_token)
        object.__setattr__(self, "refresh_expires_at", refresh_expires_at)


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    """Resolved authenticated principal for an opaque access token."""

    auth_user_id: str
    expires_at: datetime

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating session principal",
            event="authentication_session_principal_validate_start",
        )
        object.__setattr__(
            self,
            "auth_user_id",
            require_app_text(
                self.auth_user_id,
                field="auth_user_id",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_session_principal",
                max_length=512,
            ),
        )
        object.__setattr__(
            self,
            "expires_at",
            ensure_app_utc_datetime(
                self.expires_at,
                field="expires_at",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_session_principal",
            ),
        )


def _run_auth_operation(
    operation: str,
    callback: Callable[[], T],
    *,
    context: dict[str, Any] | None = None,
) -> T:
    """Translate unexpected adapter failures without leaking authentication data."""
    try:
        return callback()
    except AppError:
        raise
    except TimeoutError as exc:
        raise AppPortTimeoutError(
            "Authentication dependency timed out.",
            component=_COMPONENT,
            operation=operation,
            context=context,
            cause=exc,
        ) from exc
    except ConnectionError as exc:
        raise AppPortUnavailableError(
            "Authentication dependency is unavailable.",
            component=_COMPONENT,
            operation=operation,
            context=context,
            cause=exc,
        ) from exc
    except Exception as exc:
        raise AppPortOperationError(
            "Authentication adapter failed to complete an operation.",
            component=_COMPONENT,
            operation=operation,
            context={
                **(context or {}),
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc


class Authentication(ABC):
    """Abstract authentication and signup-verification dependency for BIMAP."""

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing authentication port",
            event="authentication_init_start",
        )
        logger.debug(
            {
                "event": "authentication_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _create_identity(
        self,
        *,
        username: str,
        password: str,
        email: str,
    ) -> IdentityCreationResult:
        raise NotImplementedError

    @abstractmethod
    def _delete_identity(self, auth_user_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def _issue_signup_verification(
        self,
        *,
        auth_user_id: str,
        username: str,
        email: str,
        phone_e164: str,
        country: str,
    ) -> VerificationDispatch:
        raise NotImplementedError

    @abstractmethod
    def _verify_signup(
        self,
        *,
        auth_user_id: str,
        username: str,
        email_code: str,
        sms_code: str | None,
    ) -> SignupVerificationResult:
        raise NotImplementedError

    @abstractmethod
    def _verify_credentials(
        self,
        *,
        username: str,
        password: str,
    ) -> CredentialVerification:
        raise NotImplementedError

    @abstractmethod
    def _create_session(self, auth_user_id: str) -> AuthSession:
        raise NotImplementedError

    @abstractmethod
    def _resolve_session(self, access_token: str) -> SessionPrincipal | None:
        raise NotImplementedError

    @abstractmethod
    def _revoke_session(self, access_token: str) -> None:
        raise NotImplementedError

    def create_identity(
        self,
        *,
        username: str,
        password: str,
        email: str,
    ) -> IdentityCreationResult:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating authentication identity",
            event="authentication_identity_create_start",
        )
        normalized_username = require_app_text(
            username,
            field="username",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="create_identity",
            max_length=64,
        )
        normalized_email = require_app_text(
            email,
            field="email",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="create_identity",
            max_length=254,
        )
        normalized_password = require_app_text(
            password,
            field="password",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="create_identity",
            max_length=4096,
        )

        result = _run_auth_operation(
            "create_identity",
            lambda: self._create_identity(
                username=normalized_username,
                password=normalized_password,
                email=normalized_email,
            ),
        )
        if not isinstance(result, IdentityCreationResult):
            raise AppIntegrityError(
                "Authentication adapter returned an invalid identity creation result.",
                component=_COMPONENT,
                operation="create_identity",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result

    def delete_identity(self, auth_user_id: str) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Deleting authentication identity",
            event="authentication_identity_delete_start",
        )
        target = require_app_text(
            auth_user_id,
            field="auth_user_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="delete_identity",
            max_length=512,
        )
        _run_auth_operation(
            "delete_identity",
            lambda: self._delete_identity(target),
        )

    def issue_signup_verification(
        self,
        *,
        auth_user_id: str,
        username: str,
        email: str,
        phone_e164: str,
        country: str,
    ) -> VerificationDispatch:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Issuing signup verification",
            event="authentication_signup_verification_issue_start",
        )
        auth_id = require_app_text(
            auth_user_id,
            field="auth_user_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="issue_signup_verification",
            max_length=512,
        )
        normalized_username = require_app_text(
            username,
            field="username",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="issue_signup_verification",
            max_length=64,
        )
        normalized_email = require_app_text(
            email,
            field="email",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="issue_signup_verification",
            max_length=254,
        )
        normalized_phone = require_app_text(
            phone_e164,
            field="phone_e164",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="issue_signup_verification",
            max_length=16,
        )
        normalized_country = require_app_text(
            country,
            field="country",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="issue_signup_verification",
            max_length=2,
        )

        result = _run_auth_operation(
            "issue_signup_verification",
            lambda: self._issue_signup_verification(
                auth_user_id=auth_id,
                username=normalized_username,
                email=normalized_email,
                phone_e164=normalized_phone,
                country=normalized_country,
            ),
            context={"auth_user_id": auth_id},
        )
        if not isinstance(result, VerificationDispatch):
            raise AppIntegrityError(
                "Authentication adapter returned an invalid verification dispatch.",
                component=_COMPONENT,
                operation="issue_signup_verification",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result

    def verify_signup(
        self,
        *,
        auth_user_id: str,
        username: str,
        email_code: str,
        sms_code: str | None,
    ) -> SignupVerificationResult:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Verifying signup channels",
            event="authentication_signup_verify_start",
        )
        auth_id = require_app_text(
            auth_user_id,
            field="auth_user_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="verify_signup",
            max_length=512,
        )
        normalized_username = require_app_text(
            username,
            field="username",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="verify_signup",
            max_length=64,
        )
        normalized_email_code = require_app_text(
            email_code,
            field="email_code",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="verify_signup",
            max_length=128,
        )
        normalized_sms_code = optional_app_text(
            sms_code,
            field="sms_code",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="verify_signup",
            max_length=128,
        )

        result = _run_auth_operation(
            "verify_signup",
            lambda: self._verify_signup(
                auth_user_id=auth_id,
                username=normalized_username,
                email_code=normalized_email_code,
                sms_code=normalized_sms_code,
            ),
            context={"auth_user_id": auth_id},
        )
        if not isinstance(result, SignupVerificationResult):
            raise AppIntegrityError(
                "Authentication adapter returned an invalid signup verification result.",
                component=_COMPONENT,
                operation="verify_signup",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result

    def verify_credentials(
        self,
        *,
        username: str,
        password: str,
    ) -> CredentialVerification:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Verifying account credentials",
            event="authentication_credentials_verify_start",
        )
        normalized_username = require_app_text(
            username,
            field="username",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="verify_credentials",
            max_length=64,
        )
        normalized_password = require_app_text(
            password,
            field="password",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="verify_credentials",
            max_length=4096,
        )

        result = _run_auth_operation(
            "verify_credentials",
            lambda: self._verify_credentials(
                username=normalized_username,
                password=normalized_password,
            ),
        )
        if not isinstance(result, CredentialVerification):
            raise AppIntegrityError(
                "Authentication adapter returned an invalid credential result.",
                component=_COMPONENT,
                operation="verify_credentials",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result

    def create_session(self, auth_user_id: str) -> AuthSession:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating authentication session",
            event="authentication_session_create_start",
        )
        target = require_app_text(
            auth_user_id,
            field="auth_user_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="create_session",
            max_length=512,
        )
        result = _run_auth_operation(
            "create_session",
            lambda: self._create_session(target),
            context={"auth_user_id": target},
        )
        if not isinstance(result, AuthSession):
            raise AppIntegrityError(
                "Authentication adapter returned an invalid session.",
                component=_COMPONENT,
                operation="create_session",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.auth_user_id != target:
            raise AppIntegrityError(
                "Authentication session principal does not match requested identity.",
                component=_COMPONENT,
                operation="create_session",
                field="result.auth_user_id",
            )
        return result

    def resolve_session(self, access_token: str) -> SessionPrincipal | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving authentication session",
            event="authentication_session_resolve_start",
            context={"has_access_token": bool(access_token)},
        )
        token = require_app_text(
            access_token,
            field="access_token",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="resolve_session",
            max_length=8192,
        )
        result = _run_auth_operation(
            "resolve_session",
            lambda: self._resolve_session(token),
        )
        if result is not None and not isinstance(result, SessionPrincipal):
            raise AppIntegrityError(
                "Authentication adapter returned an invalid session principal.",
                component=_COMPONENT,
                operation="resolve_session",
                field="result",
                context={"received_type": type(result).__name__},
            )
        return result

    def revoke_session(self, access_token: str) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Revoking authentication session",
            event="authentication_session_revoke_start",
            context={"has_access_token": bool(access_token)},
        )
        token = require_app_text(
            access_token,
            field="access_token",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="revoke_session",
            max_length=8192,
        )
        _run_auth_operation(
            "revoke_session",
            lambda: self._revoke_session(token),
        )


__all__ = [
    "IdentityCreationStatus",
    "CredentialStatus",
    "VerificationFailure",
    "AuthenticationIdentity",
    "IdentityCreationResult",
    "VerificationDispatch",
    "SignupVerificationResult",
    "CredentialVerification",
    "AuthSession",
    "SessionPrincipal",
    "Authentication",
]


if __name__ == "__main__":
    print("\n=== Running Authentication Port Self-Test ===\n")
    printer.status("TEST", "Authentication port module initialized", "info")

    class _TestAuthentication(Authentication):
        def _create_identity(self, *, username: str, password: str, email: str) -> IdentityCreationResult:
            del password
            return IdentityCreationResult(
                IdentityCreationStatus.CREATED,
                AuthenticationIdentity("auth-1", username, email),
            )

        def _delete_identity(self, auth_user_id: str) -> None:
            del auth_user_id

        def _issue_signup_verification(self, **kwargs: Any) -> VerificationDispatch:
            del kwargs
            return VerificationDispatch(
                "challenge-1",
                datetime.fromisoformat("2026-09-08T12:10:00+00:00"),
            )

        def _verify_signup(self, **kwargs: Any) -> SignupVerificationResult:
            del kwargs
            return SignupVerificationResult(True, True)

        def _verify_credentials(self, *, username: str, password: str) -> CredentialVerification:
            del username, password
            return CredentialVerification(CredentialStatus.ACCEPTED, "auth-1")

        def _create_session(self, auth_user_id: str) -> AuthSession:
            return AuthSession(
                auth_user_id,
                "access",
                datetime.fromisoformat("2026-09-08T12:15:00+00:00"),
            )

        def _resolve_session(self, access_token: str) -> SessionPrincipal | None:
            del access_token
            return SessionPrincipal(
                "auth-1",
                datetime.fromisoformat("2026-09-08T12:15:00+00:00"),
            )

        def _revoke_session(self, access_token: str) -> None:
            del access_token

    adapter = _TestAuthentication()
    created = adapter.create_identity(username="user", password="Password!1", email="u@example.com")
    assert created.created is True
    assert adapter.verify_credentials(username="user", password="Password!1").accepted is True
    printer.status("PASS", "Authentication result and adapter boundary", "success")
    print("\n=== Test ran successfully ===\n")
