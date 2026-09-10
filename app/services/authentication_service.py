"""
BIMAP signup, verification, login, logout, and session orchestration.

This service coordinates the BIMAP ``Account`` aggregate with the abstract
``Authentication`` port. It does not hash passwords, generate verification
codes, send email/SMS directly, persist authentication tokens, set HTTP cookies,
or depend on SLAI concrete classes.

Registration consistency
------------------------
Identity creation and BIMAP account persistence span two dependencies and cannot
be made atomically by this service. Registration therefore uses a small saga:

1. preflight BIMAP account uniqueness;
2. create the authentication identity;
3. create the pending BIMAP account;
4. if step 3 fails, compensate by deleting the just-created identity;
5. issue signup verification after the account is durably present.

Verification-delivery failure intentionally does not delete the pending account:
the user can safely request a new challenge through ``resend_signup_codes``.
Concrete account persistence still owns atomic uniqueness; preflight reads are
not relied on for concurrency correctness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..ports.authentication import *
from ..utils.app_errors import *
from ..utils.app_helpers import *
from .account_service import AccountProfileView, AccountService
from ...domain.accounts.models import Account, AccountStatus
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Authentication Service")
printer = PrettyPrinter()

_COMPONENT = "authentication_service"


class LoginFailure(str, Enum):
    INVALID_CREDENTIALS = "invalid_credentials"
    LOCKED = "locked"
    DISABLED = "disabled"
    UNVERIFIED = "unverified"
    SUSPENDED = "suspended"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SignupStartResult:
    """Successful start of a signup flow awaiting dual-channel verification."""

    account: Account
    verification: VerificationDispatch

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating signup start result",
            event="authentication_service_signup_start_result_validate_start",
        )
        if not isinstance(self.account, Account):
            raise AppIntegrityError(
                "SignupStartResult requires a canonical Account.",
                component=_COMPONENT,
                operation="validate_signup_start_result",
                field="account",
            )
        if self.account.status is not AccountStatus.PENDING_VERIFICATION:
            raise AppIntegrityError(
                "Signup start result must reference a pending account.",
                component=_COMPONENT,
                operation="validate_signup_start_result",
                field="account.status",
                context={"status": self.account.status.value},
            )
        if not isinstance(self.verification, VerificationDispatch):
            raise AppIntegrityError(
                "SignupStartResult requires VerificationDispatch.",
                component=_COMPONENT,
                operation="validate_signup_start_result",
                field="verification",
            )

    @property
    def verification_required(self) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing signup start result",
            event="authentication_service_signup_start_result_to_dict_start",
        )
        return {
            "verification_required": True,
            "username": self.account.username,
            "email_masked": self.verification.email_masked,
            "phone_masked": self.verification.phone_masked,
            "expires_at": self.verification.to_dict()["expires_at"],
        }


@dataclass(frozen=True, slots=True)
class AuthenticatedAccount:
    """Bind a canonical account to the opaque session issued for its identity."""

    account: Account
    session: AuthSession

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating authenticated account",
            event="authentication_service_authenticated_account_validate_start",
            context={"account_id": getattr(self.account, "account_id", None)},
        )
        if not isinstance(self.account, Account):
            raise AppIntegrityError(
                "AuthenticatedAccount requires a canonical Account.",
                component=_COMPONENT,
                operation="validate_authenticated_account",
                field="account",
            )
        if not isinstance(self.session, AuthSession):
            raise AppIntegrityError(
                "AuthenticatedAccount requires AuthSession.",
                component=_COMPONENT,
                operation="validate_authenticated_account",
                field="session",
            )
        if self.account.auth_user_id != self.session.auth_user_id:
            raise AppIntegrityError(
                "Authentication session is bound to a different identity.",
                component=_COMPONENT,
                operation="validate_authenticated_account",
                field="session.auth_user_id",
            )
        if not self.account.can_authenticate:
            raise AppIntegrityError(
                "Session was issued for an account that is not authentication-eligible.",
                component=_COMPONENT,
                operation="validate_authenticated_account",
                field="account.status",
                context={"status": self.account.status.value},
            )

    @property
    def profile(self) -> AccountProfileView:
        return AccountProfileView.from_account(self.account)


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Expected login outcome; routine denial is not an infrastructure error."""

    authenticated: AuthenticatedAccount | None = None
    failure: LoginFailure | None = None
    retry_after: datetime | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating login result",
            event="authentication_service_login_result_validate_start",
        )
        if (self.authenticated is None) == (self.failure is None):
            raise AppIntegrityError(
                "LoginResult must contain exactly one of authenticated or failure.",
                component=_COMPONENT,
                operation="validate_login_result",
            )
        if self.authenticated is not None and not isinstance(
            self.authenticated,
            AuthenticatedAccount,
        ):
            raise AppIntegrityError(
                "LoginResult authenticated value has unsupported type.",
                component=_COMPONENT,
                operation="validate_login_result",
                field="authenticated",
            )
        if self.failure is not None and not isinstance(self.failure, LoginFailure):
            try:
                object.__setattr__(self, "failure", LoginFailure(str(self.failure)))
            except ValueError as exc:
                raise AppIntegrityError(
                    "LoginResult failure is unsupported.",
                    component=_COMPONENT,
                    operation="validate_login_result",
                    field="failure",
                    cause=exc,
                ) from exc

        retry_after = (
            None
            if self.retry_after is None
            else ensure_app_utc_datetime(
                self.retry_after,
                field="retry_after",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_login_result",
            )
        )
        if self.failure is not LoginFailure.LOCKED and retry_after is not None:
            raise AppIntegrityError(
                "retry_after is valid only for a locked login result.",
                component=_COMPONENT,
                operation="validate_login_result",
                field="retry_after",
            )
        object.__setattr__(self, "retry_after", retry_after)

    @property
    def accepted(self) -> bool:
        return self.authenticated is not None


@dataclass(frozen=True, slots=True)
class SignupCompletionResult:
    """Result of one dual-channel signup verification attempt."""

    account: Account
    authenticated: AuthenticatedAccount | None = None
    failure: VerificationFailure | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating signup completion result",
            event="authentication_service_signup_completion_validate_start",
            context={"account_id": getattr(self.account, "account_id", None)},
        )
        if not isinstance(self.account, Account):
            raise AppIntegrityError(
                "SignupCompletionResult requires a canonical Account.",
                component=_COMPONENT,
                operation="validate_signup_completion_result",
                field="account",
            )

        if self.authenticated is not None:
            if not isinstance(self.authenticated, AuthenticatedAccount):
                raise AppIntegrityError(
                    "Signup completion authenticated value has unsupported type.",
                    component=_COMPONENT,
                    operation="validate_signup_completion_result",
                    field="authenticated",
                )
            if self.authenticated.account.account_id != self.account.account_id:
                raise AppIntegrityError(
                    "Signup completion account/session binding is inconsistent.",
                    component=_COMPONENT,
                    operation="validate_signup_completion_result",
                    field="authenticated.account",
                )
            if self.failure is not None:
                raise AppIntegrityError(
                    "Successful signup completion cannot carry a failure.",
                    component=_COMPONENT,
                    operation="validate_signup_completion_result",
                    field="failure",
                )
        else:
            if self.failure is None:
                raise AppIntegrityError(
                    "Incomplete signup completion requires a verification failure.",
                    component=_COMPONENT,
                    operation="validate_signup_completion_result",
                    field="failure",
                )
            if not isinstance(self.failure, VerificationFailure):
                raise AppIntegrityError(
                    "Signup completion failure has unsupported type.",
                    component=_COMPONENT,
                    operation="validate_signup_completion_result",
                    field="failure",
                )

    @property
    def verified(self) -> bool:
        return self.authenticated is not None


class AuthenticationService:
    """Coordinate BIMAP account state with authentication-provider semantics."""

    def __init__(
        self,
        authentication: Authentication,
        account_service: AccountService,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing authentication service",
            event="authentication_service_init_start",
        )
        if not isinstance(authentication, Authentication):
            raise AppConfigurationError(
                "authentication must implement the BIMAP Authentication port.",
                component=_COMPONENT,
                operation="initialize",
                field="authentication",
                context={"received_type": type(authentication).__name__},
            )
        if not isinstance(account_service, AccountService):
            raise AppConfigurationError(
                "account_service must be an AccountService.",
                component=_COMPONENT,
                operation="initialize",
                field="account_service",
                context={"received_type": type(account_service).__name__},
            )

        self.authentication = authentication
        self.account_service = account_service

        logger.info(
            {
                "event": "authentication_service_initialized",
                "authentication_implementation": type(authentication).__name__,
            }
        )

    def sign_up(
        self,
        *,
        name: str,
        surname: str,
        country: str,
        phone_e164: str,
        username: str,
        email: str,
        password: str,
        occupation: str | None = None,
        business: str | None = None,
    ) -> SignupStartResult:
        """Create authentication identity + pending BIMAP account + verification."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Starting account signup",
            event="authentication_service_signup_start",
        )

        try:
            normalized_username = Account.normalize_username(username)
            normalized_email = Account.normalize_email(email)
            normalized_phone = Account.normalize_phone_e164(phone_e164)
            normalized_country = Account.normalize_country(country)
        except DomainError as exc:
            raise AppValidationError(
                "Signup identity data is invalid.",
                component=_COMPONENT,
                operation="sign_up",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        pending_account = (
            self.account_service.find_by_username(
                normalized_username
            )
        )

        if (
            pending_account is not None
            and pending_account.status
            is AccountStatus.PENDING_VERIFICATION
            and pending_account.email
            == normalized_email
            and pending_account.phone_e164
            == normalized_phone
            and pending_account.country
            == normalized_country
        ):
            logger.info(
                {
                    "event":
                        "authentication_service_signup_resuming_pending",
                    "account_id":
                        pending_account.account_id,
                }
            )

            return self.resend_signup_codes(
                pending_account.username
            )

        self._assert_signup_identity_available(
            username=normalized_username,
            email=normalized_email,
            phone_e164=normalized_phone,
        )

        identity_result = self.authentication.create_identity(
            username=normalized_username,
            password=password,
            email=normalized_email,
        )

        if identity_result.status is not IdentityCreationStatus.CREATED:
            field = (
                "username"
                if identity_result.status is IdentityCreationStatus.USERNAME_EXISTS
                else "email"
            )
            raise AppValidationError(
                "An authentication identity already exists for the supplied signup data.",
                component=_COMPONENT,
                operation="sign_up",
                field=field,
            )

        identity = identity_result.identity
        if identity is None:
            raise AppIntegrityError(
                "Authentication identity creation reported success without identity data.",
                component=_COMPONENT,
                operation="sign_up",
                field="identity",
            )

        try:
            identity_username_key = Account.username_lookup_key(identity.username)
            expected_username_key = Account.username_lookup_key(normalized_username)
            identity_email = Account.normalize_email(identity.email)
        except DomainError as exc:
            self._compensate_identity(identity.auth_user_id, initial_error=exc)
            raise AppIntegrityError(
                "Authentication provider returned identity data incompatible with BIMAP.",
                component=_COMPONENT,
                operation="sign_up",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if identity_username_key != expected_username_key or identity_email != normalized_email:
            error = AppIntegrityError(
                "Authentication provider changed requested username or email identity.",
                component=_COMPONENT,
                operation="sign_up",
                field="identity",
            )
            self._compensate_identity(identity.auth_user_id, initial_error=error)
            raise error

        try:
            account = self.account_service.create_pending_account(
                auth_user_id=identity.auth_user_id,
                username=normalized_username,
                email=normalized_email,
                phone_e164=normalized_phone,
                country=normalized_country,
                name=name,
                surname=surname,
                occupation=occupation,
                business=business,
            )
        except Exception as exc:
            self._compensate_identity(identity.auth_user_id, initial_error=exc)
            raise

        # Delivery is intentionally after durable account creation. If it fails,
        # the account remains pending and resend_signup_codes can recover it.
        verification = self.authentication.issue_signup_verification(
            auth_user_id=account.auth_user_id,
            username=account.username,
            email=account.email,
            phone_e164=account.phone_e164,
            country=account.country,
        )

        logger.info(
            {
                "event": "authentication_service_signup_started",
                "account_id": account.account_id,
                "status": account.status.value,
            }
        )
        return SignupStartResult(account=account, verification=verification)

    def verify_signup(self, *, username: str, email_code: str, sms_code: str | None) -> SignupCompletionResult:
        """Verify signup channels, persist channel state, and issue first session."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Completing signup verification",
            event="authentication_service_signup_verify_start",
        )
        account = self.account_service.find_by_username(username)
        if account is None:
            raise AppValidationError(
                "Signup verification cannot be completed for this account.",
                component=_COMPONENT,
                operation="verify_signup",
                field="username",
            )

        if account.status is not AccountStatus.PENDING_VERIFICATION:
            raise AppValidationError(
                "Account is not awaiting signup verification.",
                component=_COMPONENT,
                operation="verify_signup",
                field="username",
            )

        verification = self.authentication.verify_signup(
            auth_user_id=account.auth_user_id,
            username=account.username,
            email_code=email_code,
            sms_code=sms_code,
        )

        changed = self.account_service.record_signup_verification(
            account.account_id,
            email_verified=verification.email_verified,
            phone_verified=verification.phone_verified,
        )

        # Persisted channel verification is monotonic. A prior attempt may have
        # verified one channel while the current attempt verifies the other, so
        # account state—not only this single provider result—decides completion.
        if not changed.can_authenticate:
            logger.info(
                {
                    "event": "authentication_service_signup_verification_rejected",
                    "account_id": changed.account_id,
                    "failure": verification.failure.value if verification.failure else None,
                    "email_verified": changed.is_email_verified,
                    "phone_verified": changed.is_phone_verified,
                }
            )
            if verification.failure is None:
                raise AppIntegrityError(
                    "Incomplete signup verification omitted its failure reason.",
                    component=_COMPONENT,
                    operation="verify_signup",
                    field="verification.failure",
                )
            return SignupCompletionResult(
                account=changed,
                failure=verification.failure,
            )

        session = self.authentication.create_session(changed.auth_user_id)
        authenticated = AuthenticatedAccount(changed, session)

        logger.info(
            {
                "event": "authentication_service_signup_completed",
                "account_id": changed.account_id,
                "status": changed.status.value,
            }
        )
        return SignupCompletionResult(
            account=changed,
            authenticated=authenticated,
        )

    def resend_signup_codes(self, username: str) -> SignupStartResult:
        """Invalidate/replace the active verification challenge for a pending account."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resending signup verification",
            event="authentication_service_signup_resend_start",
        )
        account = self.account_service.find_by_username(username)
        if account is None:
            raise AppValidationError(
                "Signup verification cannot be resent for this account.",
                component=_COMPONENT,
                operation="resend_signup_codes",
                field="username",
            )
        if account.status is not AccountStatus.PENDING_VERIFICATION:
            raise AppValidationError(
                "Account is not awaiting signup verification.",
                component=_COMPONENT,
                operation="resend_signup_codes",
                field="username",
            )

        verification = self.authentication.issue_signup_verification(
            auth_user_id=account.auth_user_id,
            username=account.username,
            email=account.email,
            phone_e164=account.phone_e164,
            country=account.country,
        )
        return SignupStartResult(account=account, verification=verification)

    def login(self, *, username: str, password: str) -> LoginResult:
        """Verify credentials and issue a session only for active verified accounts."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Authenticating account login",
            event="authentication_service_login_start",
        )
        credentials = self.authentication.verify_credentials(
            username=username,
            password=password,
        )

        if credentials.status is CredentialStatus.INVALID:
            return LoginResult(failure=LoginFailure.INVALID_CREDENTIALS)
        if credentials.status is CredentialStatus.LOCKED:
            return LoginResult(
                failure=LoginFailure.LOCKED,
                retry_after=credentials.retry_after,
            )
        if credentials.status is CredentialStatus.DISABLED:
            return LoginResult(failure=LoginFailure.DISABLED)

        auth_user_id = credentials.auth_user_id
        if auth_user_id is None:
            raise AppIntegrityError(
                "Accepted credential result omitted authentication identity.",
                component=_COMPONENT,
                operation="login",
                field="auth_user_id",
            )

        account = self.account_service.find_by_auth_user_id(auth_user_id)
        if account is None:
            raise AppIntegrityError(
                "Authenticated identity is not bound to a BIMAP account.",
                component=_COMPONENT,
                operation="login",
                field="auth_user_id",
            )

        try:
            if Account.username_lookup_key(account.username) != Account.username_lookup_key(username):
                raise AppIntegrityError(
                    "Authenticated username does not match bound BIMAP account.",
                    component=_COMPONENT,
                    operation="login",
                    field="username",
                )
        except DomainError as exc:
            raise AppValidationError(
                "Login username is invalid.",
                component=_COMPONENT,
                operation="login",
                field="username",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        if account.status is AccountStatus.PENDING_VERIFICATION:
            return LoginResult(failure=LoginFailure.UNVERIFIED)
        if account.status is AccountStatus.SUSPENDED:
            return LoginResult(failure=LoginFailure.SUSPENDED)
        if account.status is AccountStatus.CLOSED:
            return LoginResult(failure=LoginFailure.CLOSED)
        if not account.can_authenticate:
            raise AppIntegrityError(
                "Active BIMAP account is not fully verified.",
                component=_COMPONENT,
                operation="login",
                field="account.status",
                context={"account_id": account.account_id},
            )

        session = self.authentication.create_session(account.auth_user_id)
        authenticated = AuthenticatedAccount(account, session)

        logger.info(
            {
                "event": "authentication_service_login_completed",
                "account_id": account.account_id,
            }
        )
        return LoginResult(authenticated=authenticated)

    def resolve_session_account(self, access_token: str) -> Account | None:
        """Resolve an access token to one currently authentication-eligible account."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving authenticated account session",
            event="authentication_service_session_resolve_start",
            context={"has_access_token": bool(access_token)},
        )
        principal = self.authentication.resolve_session(access_token)
        if principal is None:
            return None

        account = self._account_for_principal(principal, operation="resolve_session_account")
        if not account.can_authenticate:
            return None
        return account

    def logout(self, access_token: str) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Logging out account session",
            event="authentication_service_logout_start",
            context={"has_access_token": bool(access_token)},
        )
        self.authentication.revoke_session(access_token)

    def _account_for_principal(self, principal: SessionPrincipal, *, operation: str) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving BIMAP account for authenticated principal",
            event="authentication_service_principal_account_resolve_start",
        )
        if not isinstance(principal, SessionPrincipal):
            raise AppIntegrityError(
                "Authentication principal has unsupported type.",
                component=_COMPONENT,
                operation=operation,
                field="principal",
                context={"received_type": type(principal).__name__},
            )
        account = self.account_service.find_by_auth_user_id(principal.auth_user_id)
        if account is None:
            raise AppIntegrityError(
                "Valid authentication principal has no BIMAP account binding.",
                component=_COMPONENT,
                operation=operation,
                field="principal.auth_user_id",
            )
        return account

    def _assert_signup_identity_available(self, *, username: str, email: str, phone_e164: str)-> Account | None:
        """
        Resolve whether signup may proceed or resume an existing pending account.

        A retry of the exact same pending signup is recoverable. Identity
        collisions involving another account or changed signup identifiers remain
        validation failures.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving signup identity availability",
            event="authentication_service_signup_availability_start",
        )

        checks = (
            ("username", self.account_service.find_by_username(username)),
            ("email", self.account_service.find_by_email(email)),
            ("phone_e164", self.account_service.find_by_phone(phone_e164)),
        )

        matched = tuple(
            (field, account)
            for field, account in checks
            if account is not None
        )

        if not matched:
            return None

        account_ids = {
            account.account_id
            for _, account in matched
        }

        if len(account_ids) == 1:
            account = matched[0][1]

            same_identity = (
                Account.username_lookup_key(account.username) == Account.username_lookup_key(username)
                and account.email == email
                and account.phone_e164 == phone_e164
                )

            if (
                same_identity
                and account.status
                is AccountStatus.PENDING_VERIFICATION
            ):
                logger.info(
                    {"event": "authentication_service_signup_pending_resolved",
                     "account_id": account.account_id,
                     "status": account.status.value,
                    }
                )

                return account

        # Either:
        # - the supplied identifiers belong to different accounts; or
        # - an identifier belongs to an already-established account; or
        # - the caller changed part of an existing pending identity.
        #
        # Do not expose which account owns the identifier.
        conflict_field = matched[0][0]

        raise AppValidationError(
            "An account already exists for the supplied signup identity.",
            component=_COMPONENT,
            operation="sign_up",
            field=conflict_field,
        )

    def _compensate_identity(self, auth_user_id: str, *, initial_error: BaseException) -> None:
        """Best-effort compensation after identity creation but before account durability."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Compensating authentication identity",
            event="authentication_service_identity_compensate_start",
        )
        try:
            self.authentication.delete_identity(auth_user_id)
        except Exception as cleanup_exc:
            logger.exception(
                "Authentication identity compensation failed after %s",
                type(initial_error).__name__,
            )
            raise AppIntegrityError(
                "Signup failed and the newly created authentication identity "
                "could not be compensated.",
                component=_COMPONENT,
                operation="sign_up_compensation",
                context={
                    "initial_error_type": type(initial_error).__name__,
                    **lower_error_context(cleanup_exc),
                },
                cause=cleanup_exc,
            ) from cleanup_exc


__all__ = [
    "LoginFailure",
    "SignupStartResult",
    "AuthenticatedAccount",
    "LoginResult",
    "SignupCompletionResult",
    "AuthenticationService",
]
