"""
Local concrete infrastructure adapters for BIMAP.

Location
--------
SLAI/applications/bimap/infrastructure/local.py

Purpose
-------
These adapters provide a deterministic, dependency-free local deployment for
BIMAP development, integration testing, and single-process evaluation.

They implement the existing BIMAP application ports without changing those
ports or leaking provider-specific semantics into the application layer.

Important deployment boundary
-----------------------------
This module is intentionally local/development infrastructure:

- persistence is process-local and non-durable;
- object storage is process-local and non-durable;
- queue delivery is process-local and does not supervise worker execution;
- payment operations fail closed because no payment provider is configured;
- malware scanning returns ``indeterminate`` unless the deployment explicitly
  opts into a development-only trust override.

A production deployment must replace these adapters with durable/provider-backed
implementations while preserving the same application-port contracts.
"""

from __future__ import annotations

import hashlib
import secrets
import phonenumbers  # type: ignore

from abc import abstractmethod
from datetime import datetime, timezone, timedelta
from io import BytesIO
from threading import RLock
from typing import BinaryIO

from src.functions.auth import AuthService as SLAIAuthService  # type: ignore
from ..notifications.email_models import EmailVerificationData
from ..notifications.email_service import EmailService
from ..notifications.utils.email_errors import EmailError as BIMAPEmailError, EmailTransportTimeoutError
from src.functions.phone_verification import PhoneVerificationService  # type: ignore
from src.functions.utils.functions_error import (  # type: ignore
    AccountLockedError,
    CredentialPolicyError,
    InvalidCountryCodeError,
    InvalidCredentialsError,
    InvalidPhoneNumberError,
    PhoneCountryMismatchError,
    SMSError,
    UserAlreadyExistsError,
    VerificationAttemptsExceededError,
    VerificationCodeExpiredError,
    VerificationNotFoundError,
    VerificationRateLimitError,
)
from ..app.ports.clock import Clock
from ..app.ports.malware import *
from ..app.ports.payment import *
from ..app.ports.queue import Queue, QueueReceipt
from ..app.ports.repositories import Repository
from ..app.ports.storage import Storage, StoredObject
from ..app.ports.accounts import Accounts
from ..app.ports.authentication import *
from ..app.services.entitlement_service import *
from ..app.utils.app_errors import *
from ..contracts.audit_job import AuditJob
from ..contracts.report_manifest import ReportManifest
from ..domain.accounts.models import Account
from ..domain.accounts.plans import *
from ..domain.evidence.models import EvidenceItem
from ..domain.findings.models import Finding
from ..domain.governance.review import Review
from ..domain.orders.models import Order
from ..domain.products.models import ProductTier
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Local Infrastructure")
printer = PrettyPrinter()


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class SystemClock(Clock):
    """UTC system clock implementation of the BIMAP ``Clock`` port."""

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local UTC clock", "info")
        super().__init__()

    def _read_utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Canonical account repository
# ---------------------------------------------------------------------------


class InMemoryAccounts(Accounts):
    """Thread-safe process-local canonical account repository."""

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local account repository", "info")
        self._lock = RLock()
        self._accounts: dict[str, Account] = {}
        self._auth_index: dict[str, str] = {}
        self._username_index: dict[str, str] = {}
        self._email_index: dict[str, str] = {}
        self._phone_index: dict[str, str] = {}
        super().__init__()

    def _get_account(self, account_id: str) -> Account | None:
        with self._lock:
            return self._accounts.get(account_id)

    def _get_by_auth_user_id(self, auth_user_id: str) -> Account | None:
        with self._lock:
            account_id = self._auth_index.get(auth_user_id)
            return None if account_id is None else self._accounts.get(account_id)

    def _get_by_username(self, username_key: str) -> Account | None:
        with self._lock:
            account_id = self._username_index.get(username_key)
            return None if account_id is None else self._accounts.get(account_id)

    def _get_by_email(self, normalized_email: str) -> Account | None:
        with self._lock:
            account_id = self._email_index.get(normalized_email)
            return None if account_id is None else self._accounts.get(account_id)

    def _get_by_phone(self, phone_e164: str) -> Account | None:
        with self._lock:
            account_id = self._phone_index.get(phone_e164)
            return None if account_id is None else self._accounts.get(account_id)

    def _save_account(
        self,
        account: Account,
        *,
        expected_version: int | None,
    ) -> Account:
        with self._lock:
            current = self._accounts.get(account.account_id)

            if expected_version is None:
                if current is not None:
                    raise RepositoryConflictError(
                        "Account already exists.",
                        component="local_accounts",
                        operation="save_account",
                        field="account_id",
                        context={"account_id": account.account_id},
                    )
            elif current is None or current.version != expected_version:
                raise RepositoryConflictError(
                    "Account optimistic-concurrency precondition failed.",
                    component="local_accounts",
                    operation="save_account",
                    field="expected_version",
                    context={
                        "account_id": account.account_id,
                        "expected_version": expected_version,
                        "actual_version": None if current is None else current.version,
                    },
                )

            identities = (
                ("auth_user_id", account.auth_user_id, self._auth_index),
                ("username", Account.username_lookup_key(account.username), self._username_index),
                ("email", account.email, self._email_index),
                ("phone_e164", account.phone_e164, self._phone_index),
            )
            for field, key, index in identities:
                owner = index.get(key)
                if owner is not None and owner != account.account_id:
                    raise RepositoryConflictError(
                        "Account identity is already bound to another account.",
                        component="local_accounts",
                        operation="save_account",
                        field=field,
                    )

            if current is not None:
                previous = (
                    (current.auth_user_id, self._auth_index),
                    (Account.username_lookup_key(current.username), self._username_index),
                    (current.email, self._email_index),
                    (current.phone_e164, self._phone_index),
                )
                for key, index in previous:
                    if index.get(key) == account.account_id:
                        index.pop(key, None)

            self._accounts[account.account_id] = account
            self._auth_index[account.auth_user_id] = account.account_id
            self._username_index[Account.username_lookup_key(account.username)] = account.account_id
            self._email_index[account.email] = account.account_id
            self._phone_index[account.phone_e164] = account.account_id
            return account


class LocalSLAIAuthentication(Authentication):
    """Local BIMAP adapter over SLAI authentication, email, and SMS services."""

    _EMAIL_PURPOSE = "signup_verification"

    def __init__(
        self,
        auth_service: SLAIAuthService,
        email_service: EmailService,
        phone_verification: PhoneVerificationService | None = None,
        *,
        email_code_ttl_minutes: int = 15,
        require_phone_verification: bool = True,
    ) -> None:
        printer.status("BIMAP", "Initializing SLAI authentication adapter", "info")

        if not isinstance(auth_service, SLAIAuthService):
            raise TypeError("auth_service must be an SLAI AuthService")
        if not isinstance(email_service, EmailService):
            raise TypeError("email_service must be a BIMAP EmailService")
        if not isinstance(require_phone_verification, bool):
            raise TypeError("require_phone_verification must be boolean")
        if (
            require_phone_verification
            and not isinstance(phone_verification, PhoneVerificationService)):
            raise TypeError(
                "phone_verification must be a "
                "PhoneVerificationService when phone "
                "verification is required"
            )

        if (
            phone_verification is not None
            and not isinstance(phone_verification, PhoneVerificationService)
        ):
            raise TypeError(
                "phone_verification must be a "
                "PhoneVerificationService or None"
            )

        if (
            isinstance(email_code_ttl_minutes, bool)
            or not isinstance(email_code_ttl_minutes, int)
            or email_code_ttl_minutes <= 0
        ):
            raise ValueError(
                "email_code_ttl_minutes must be "
                "a positive integer"
            )

        self._auth = auth_service
        self._email = email_service
        self._phone = phone_verification
        self._require_phone_verification = require_phone_verification
        self._email_code_ttl_minutes = (email_code_ttl_minutes)
        self._lock = RLock()
        # self._usernames_by_auth_id: dict[str, str] = {}
        self._contacts_by_auth_id: dict[str, tuple[str, str, str, str]] = {}
        self._sessions: dict[str, SessionPrincipal] = {}
        super().__init__()

    @staticmethod
    def _mask_email(email: str) -> str:
        local, domain = email.rsplit("@", 1)
        return f"{local[:1]}***@{domain}"

    @staticmethod
    def _mask_phone(phone_e164: str) -> str:
        return f"{phone_e164[:3]}***{phone_e164[-4:]}"

    def _create_identity(self, *, username: str, password: str, email: str) -> IdentityCreationResult:
        try:
            auth_user_id = self._auth.sign_up(username=username, password=password, email=email)
        except UserAlreadyExistsError:
            return IdentityCreationResult(status=IdentityCreationStatus.USERNAME_EXISTS)
        except CredentialPolicyError as exc:
            raise AppValidationError(
                "Password does not satisfy the configured credential policy.",
                component="local_slai_authentication",
                operation="create_identity",
                field="password",
                cause=exc,
            ) from exc

        identity = AuthenticationIdentity(
            auth_user_id=auth_user_id,
            username=username,
            email=email,
        )
        return IdentityCreationResult(status=IdentityCreationStatus.CREATED, identity=identity)

    def _delete_identity(self, auth_user_id: str) -> None:
        raise AppPortOperationError(
            "SLAI AuthService does not expose an identity-deletion operation.",
            component="local_slai_authentication",
            operation="delete_identity",
            field="auth_user_id",
        )

    def _issue_signup_verification(
        self,
        *,
        auth_user_id: str,
        username: str,
        email: str,
        phone_e164: str,
        country: str,
    ) -> VerificationDispatch:
        challenge_id = secrets.token_hex(16)

        try:
            email_code = (
                self._auth
                .create_verification_challenge(
                    username=username,
                    purpose=self._EMAIL_PURPOSE,
                    expires_in_minutes=(
                        self._email_code_ttl_minutes
                    ),
                    invalidate_existing=True,
                )
            )

            self._email.send_verification_email(
                email,
                EmailVerificationData(
                    user_name=username,
                    verification_code=email_code,
                    expires_in_minutes=(
                        self._email_code_ttl_minutes
                    ),
                ),
                idempotency_key=(f"signup:{challenge_id}:email"),
                correlation_id=challenge_id,
            )

        except EmailTransportTimeoutError as exc:
            raise AppPortTimeoutError(
                "Email verification delivery timed out.",
                component="local_slai_authentication",
                operation="issue_signup_verification",
                context={
                    "email_error_type": type(exc).__name__,
                    "email_error_code": getattr(exc, "code", None),
                },
                cause=exc,
            ) from exc

        except BIMAPEmailError as exc:
            context = {
                "email_error_type": type(exc).__name__,
                "email_error_code": getattr(exc, "code", None),
            }

            if bool(getattr(exc, "retryable", False)):
                raise AppPortUnavailableError(
                    "Email verification delivery is unavailable.",
                    component="local_slai_authentication",
                    operation="issue_signup_verification",
                    context=context,
                    cause=exc,
                ) from exc

            raise AppPortOperationError(
                "Email verification delivery failed.",
                component="local_slai_authentication",
                operation="issue_signup_verification",
                context=context,
                cause=exc,
            ) from exc

        phone_request = None

        if self._require_phone_verification:
            assert self._phone is not None

            try:
                parsed = phonenumbers.parse(phone_e164, None)

                phone_request = (
                    self._phone.send_verification_code(
                        phone_e164,
                        country_code=(f"+{parsed.country_code}"),
                        country_region=country,
                    )
                )

            except (
                InvalidPhoneNumberError,
                InvalidCountryCodeError,
                PhoneCountryMismatchError,
            ) as exc:
                raise AppValidationError(
                    "Phone number does not match "
                    "the selected country.",
                    component="local_slai_authentication",
                    operation="issue_signup_verification",
                    field="phone_e164",
                    cause=exc,
                ) from exc

            except VerificationRateLimitError as exc:
                raise AppValidationError(
                    "Phone verification cannot be resent yet.",
                    component="local_slai_authentication",
                    operation="issue_signup_verification",
                    field="phone_e164",
                    cause=exc,
                ) from exc

            except SMSError as exc:
                raise AppPortUnavailableError(
                    "SMS verification delivery is unavailable.",
                    component="local_slai_authentication",
                    operation="issue_signup_verification",
                    cause=exc,
                ) from exc

        with self._lock:
            self._contacts_by_auth_id[auth_user_id] = (
                username,
                email,
                phone_e164,
                country,
            )

        expires_in = float(self._email_code_ttl_minutes * 60)

        phone_masked = None

        if phone_request is not None:
            expires_in = min(expires_in, float(phone_request.expires_in_seconds))
            phone_masked = self._mask_phone(phone_e164)

        return VerificationDispatch(
            challenge_id=challenge_id,
            expires_at=( datetime.now(timezone.utc) + timedelta(seconds=expires_in)),
            email_masked=self._mask_email(email),
            phone_masked=phone_masked,
        )

    def _verify_signup(self, *, auth_user_id: str, username: str, email_code: str, sms_code: str | None) -> SignupVerificationResult:
        with self._lock:
            contact = self._contacts_by_auth_id.get(auth_user_id)

        if contact is None:
            return SignupVerificationResult(
                email_verified=False,
                phone_verified=False,
                failure=VerificationFailure.EXPIRED,
            )

        stored_username, _, phone_e164, country = contact
        if stored_username != username:
            raise AppIntegrityError(
                "Verification identity does not match the bound username.",
                component="local_slai_authentication",
                operation="verify_signup",
                field="username",
            )

        email_verified = self._auth.verify_passcode(
            username=username,
            passcode=email_code,
            purpose=self._EMAIL_PURPOSE,
        )

        phone_failure: VerificationFailure | None = None

        if self._require_phone_verification:
            assert self._phone is not None

            if sms_code is None:
                raise AppValidationError(
                    "SMS verification code is required.",
                    component="local_slai_authentication",
                    operation="verify_signup",
                    field="sms_code",
                )

            try:
                phone_verified = (self._phone.verify_code(phone_e164, sms_code, default_region=country))

            except VerificationAttemptsExceededError:
                phone_verified = False
                phone_failure = (VerificationFailure.ATTEMPTS_EXHAUSTED)

            except (
                VerificationCodeExpiredError,
                VerificationNotFoundError,
            ):
                phone_verified = False
                phone_failure = (VerificationFailure.EXPIRED)

            except (
                InvalidPhoneNumberError,
                InvalidCountryCodeError,
                PhoneCountryMismatchError,
            ) as exc:
                raise AppIntegrityError(
                    "Stored phone-verification identity "
                    "is invalid.",
                    component="local_slai_authentication",
                    operation="verify_signup",
                    field="phone_e164",
                    cause=exc,
                ) from exc

        else:
            # Temporary policy bypass:
            # the phone channel is considered satisfied
            # when SMS verification is disabled.
            phone_verified = True

        if email_verified and phone_verified:
            with self._lock:
                self._contacts_by_auth_id.pop(auth_user_id, None)
            return SignupVerificationResult(
                email_verified=True,
                phone_verified=True,
            )

        return SignupVerificationResult(
            email_verified=email_verified,
            phone_verified=phone_verified,
            failure=phone_failure or VerificationFailure.INVALID_CODE,
        )

    def _verify_credentials(self, *, username: str, password: str) -> CredentialVerification:
        try:
            auth_user_id = self._auth.verify_credentials(username=username, password=password)
        except InvalidCredentialsError:
            return CredentialVerification(status=CredentialStatus.INVALID)
        except AccountLockedError as exc:
            return CredentialVerification(
                status=CredentialStatus.LOCKED,
                retry_after=getattr(exc, "lockout_until", None),
            )

        return CredentialVerification(
            status=CredentialStatus.ACCEPTED,
            auth_user_id=auth_user_id,
        )

    def _create_session(self, auth_user_id: str) -> AuthSession:
        token = self._auth.complete_login_by_user_id(auth_user_id)

        if token.user_id != auth_user_id:
            raise AppIntegrityError(
                "Authentication session identity "
                "does not match the requested "
                "authentication identity.",
                component="local_slai_authentication",
                operation="create_session",
                field="auth_user_id",
            )

        session = AuthSession(
            auth_user_id=auth_user_id,
            access_token=token.token,
            expires_at=token.expires_at,
        )

        with self._lock:
            self._sessions[
                session.access_token
            ] = SessionPrincipal(
                auth_user_id=auth_user_id,
                expires_at=session.expires_at,
            )

        return session

    def _resolve_session(self, access_token: str) -> SessionPrincipal | None:
        if not self._auth.is_token_valid(access_token):
            with self._lock:
                self._sessions.pop(access_token, None)
            return None
        with self._lock:
            return self._sessions.get(access_token)

    def _revoke_session(self, access_token: str) -> None:
        self._auth.log_out(access_token)
        with self._lock:
            self._sessions.pop(access_token, None)


# ---------------------------------------------------------------------------
# Account entitlements
# ---------------------------------------------------------------------------

class InMemoryEntitlementStore:
    """
    Thread-safe local entitlement persistence.

    Production must replace this with an atomic durable implementation.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local entitlement store", "info")

        self._lock = RLock()
        self._consumptions: list[dict[str, object]] = []
        self._bonus: dict[tuple[str, UsageKind], int] = {}

    def find_by_source(self, *, account_id: str, kind: UsageKind, source_id: str) -> dict[str, object] | None:
        with self._lock:
            for record in self._consumptions:
                if (
                    record["account_id"]
                    == account_id
                    and record["kind"]
                    is kind
                    and record["source_id"]
                    == source_id
                ):
                    return dict(record)

        return None

    def find_by_idempotency_key(self, *, account_id: str, idempotency_key: str) -> dict[str, object] | None:
        with self._lock:
            for record in self._consumptions:
                if (
                    record["account_id"]
                    == account_id
                    and record[
                        "idempotency_key"
                    ]
                    == idempotency_key
                ):
                    return dict(record)

        return None

    def _existing_locked(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        source_id: str,
        idempotency_key: str,
    ) -> dict[str, object] | None:
        for record in self._consumptions:
            if (
                record["account_id"]
                == account_id
                and record["kind"]
                is kind
                and record["source_id"]
                == source_id
            ):
                return dict(record)

            if (
                record["account_id"]
                == account_id
                and record[
                    "idempotency_key"
                ]
                == idempotency_key
            ):
                if (
                    record["kind"]
                    is not kind
                    or record["source_id"]
                    != source_id
                ):
                    raise RuntimeError(
                        "Entitlement idempotency key "
                        "is already bound to another source."
                    )

                return dict(record)

        return None

    def try_consume_recurring(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        plan_code: AccountPlanCode,
        source_id: str,
        idempotency_key: str,
        occurred_at: datetime,
        period_start: datetime,
        period_end: datetime,
        limit: int,
    ) -> dict[str, object] | None:
        with self._lock:
            existing = self._existing_locked(
                account_id=account_id,
                kind=kind,
                source_id=source_id,
                idempotency_key=(
                    idempotency_key
                ),
            )

            if existing is not None:
                return existing

            used = sum(
                1
                for record
                in self._consumptions
                if (
                    record["account_id"]
                    == account_id
                    and record["kind"]
                    is kind
                    and record["source"]
                    == (
                        EntitlementSource
                        .RECURRING_QUOTA
                        .value
                    )
                    and record[
                        "period_start"
                    ]
                    == period_start
                    and record[
                        "period_end"
                    ]
                    == period_end
                )
            )

            if used >= limit:
                return None

            record: dict[str, object] = {
                "account_id": account_id,
                "kind": kind,
                "source_id": source_id,
                "idempotency_key": idempotency_key,
                "source": EntitlementSource.RECURRING_QUOTA.value,
                "plan_code": plan_code,
                "occurred_at": occurred_at,
                "period_start": period_start,
                "period_end": period_end,
            }

            self._consumptions.append(
                record
            )

            return dict(record)

    def try_consume_bonus(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        plan_code: AccountPlanCode,
        source_id: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> dict[str, object] | None:
        with self._lock:
            existing = self._existing_locked(
                account_id=account_id,
                kind=kind,
                source_id=source_id,
                idempotency_key=(
                    idempotency_key
                ),
            )

            if existing is not None:
                return existing

            key = (
                account_id,
                kind,
            )

            balance = self._bonus.get(
                key,
                0,
            )

            if balance <= 0:
                return None

            self._bonus[key] = (
                balance - 1
            )

            record: dict[
                str,
                object,
            ] = {
                "account_id":
                    account_id,
                "kind":
                    kind,
                "source_id":
                    source_id,
                "idempotency_key":
                    idempotency_key,
                "source":
                    EntitlementSource
                    .BONUS_CREDIT
                    .value,
                "plan_code":
                    plan_code,
                "occurred_at":
                    occurred_at,
                "period_start":
                    None,
                "period_end":
                    None,
            }

            self._consumptions.append(
                record
            )

            return dict(record)

    def record_unlimited(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        plan_code: AccountPlanCode,
        source_id: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> dict[str, object]:
        with self._lock:
            existing = self._existing_locked(
                account_id=account_id,
                kind=kind,
                source_id=source_id,
                idempotency_key=(
                    idempotency_key
                ),
            )

            if existing is not None:
                return existing

            record: dict[
                str,
                object,
            ] = {
                "account_id":
                    account_id,
                "kind":
                    kind,
                "source_id":
                    source_id,
                "idempotency_key":
                    idempotency_key,
                "source":
                    EntitlementSource
                    .UNLIMITED
                    .value,
                "plan_code":
                    plan_code,
                "occurred_at":
                    occurred_at,
                "period_start":
                    None,
                "period_end":
                    None,
            }

            self._consumptions.append(
                record
            )

            return dict(record)

    def grant_bonus(self, account_id: str, kind: UsageKind, *, units: int = 1) -> None:
        if (
            isinstance(units, bool)
            or not isinstance(units, int)
            or units <= 0
        ):
            raise ValueError("units must be a positive integer")

        with self._lock:
            key = (account_id, kind)
            self._bonus[key] = (self._bonus.get(key, 0) + units)

    def recurring_usage_count(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        period_start: datetime,
        period_end: datetime,
    ) -> int:
        with self._lock:
            return sum(
                1
                for record in self._consumptions
                if record["account_id"] == account_id
                and record["kind"] is kind
                and record["source"] == EntitlementSource.RECURRING_QUOTA.value
                and record["period_start"] == period_start
                and record["period_end"] == period_end
            )

    def bonus_balance(self, *, account_id: str, kind: UsageKind) -> int:
        with self._lock:
            return self._bonus.get((account_id, kind), 0)


class CalendarUTCRenewalWindowResolver:
    """
    Explicit local-development calendar renewal policy.

    Weekly:
        ISO-style Monday 00:00 UTC -> next Monday.

    Monthly:
        first day 00:00 UTC -> first day of next month.

    A production subscription may replace this with account-anniversary
    boundaries without changing the entitlement service.
    """

    def resolve(self, *, account_id: str, plan: AccountPlan, quota: UsageQuota, at: datetime) -> EntitlementWindow:
        del account_id, plan

        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")

        current = at.astimezone(timezone.utc)

        if (
            quota.renewal
            is RenewalCadence.WEEKLY
        ):
            start = current.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ) - timedelta(
                days=current.weekday()
            )

            end = (start + timedelta(days=7))

            return EntitlementWindow(start=start, end=end)

        if (
            quota.renewal
            is RenewalCadence.MONTHLY
        ):
            start = current.replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )

            if start.month == 12:
                end = start.replace(year=start.year + 1, month=1)
            else:
                end = start.replace(month=start.month + 1)

            return EntitlementWindow(start=start, end=end)

        raise ValueError(
            "Finite entitlement requires "
            "weekly or monthly renewal."
        )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class InMemoryRepository(Repository):
    """
    Thread-safe process-local implementation of the composite Repository port.

    The adapter preserves the port's optimistic-concurrency requirement for
    ``Order`` writes whenever ``expected_version`` is supplied.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local repository", "info")

        self._lock = RLock()
        self._orders: dict[str, Order] = {}
        self._evidence: dict[str, EvidenceItem] = {}
        self._findings: dict[str, Finding] = {}
        self._reviews: dict[str, Review] = {}
        self._reports: dict[str, ReportManifest] = {}

        super().__init__()

    def list_orders_for_account(self, account_id: str) -> tuple[Order, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (order for order in self._orders.values() if order.account_id == account_id),
                    key=lambda order: (order.updated_at, order.order_id),
                    reverse=True,
                )
            )

    def _get_order(self, order_id: str) -> Order | None:
        with self._lock:
            return self._orders.get(order_id)

    def _save_order(
        self,
        order: Order,
        *,
        expected_version: int | None,
    ) -> Order:
        with self._lock:
            current = self._orders.get(order.order_id)

            if expected_version is not None:
                actual_version = None if current is None else current.version

                if actual_version != expected_version:
                    raise RepositoryConflictError(
                        "Order optimistic-concurrency precondition failed.",
                        component="local_repository",
                        operation="save_order",
                        field="expected_version",
                        context={
                            "order_id": order.order_id,
                            "expected_version": expected_version,
                            "actual_version": actual_version,
                        },
                    )

            if current is not None and order.version < current.version:
                raise RepositoryConflictError(
                    "Refusing to persist an older Order aggregate revision.",
                    component="local_repository",
                    operation="save_order",
                    field="order.version",
                    context={
                        "order_id": order.order_id,
                        "stored_version": current.version,
                        "received_version": order.version,
                    },
                )

            self._orders[order.order_id] = order
            return order

    def _get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        with self._lock:
            return self._evidence.get(evidence_id)

    def _save_evidence(self, evidence: EvidenceItem) -> EvidenceItem:
        with self._lock:
            self._evidence[evidence.evidence_id] = evidence
            return evidence

    def _get_finding(self, finding_id: str) -> Finding | None:
        with self._lock:
            return self._findings.get(finding_id)

    def _save_finding(self, finding: Finding) -> Finding:
        with self._lock:
            self._findings[finding.finding_id] = finding
            return finding

    def _get_review(self, review_id: str) -> Review | None:
        with self._lock:
            return self._reviews.get(review_id)

    def _save_review(self, review: Review) -> Review:
        with self._lock:
            self._reviews[review.review_id] = review
            return review

    def _get_report_manifest(self, report_id: str) -> ReportManifest | None:
        with self._lock:
            return self._reports.get(report_id)

    def _save_report_manifest(self, manifest: ReportManifest) -> ReportManifest:
        with self._lock:
            self._reports[manifest.report_id] = manifest
            return manifest


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class InMemoryStorage(Storage):
    """Thread-safe process-local binary storage with integrity checking."""

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local object storage", "info")

        self._lock = RLock()
        self._objects: dict[str, tuple[bytes, StoredObject]] = {}

        super().__init__()

    def _put(
        self,
        stream: BinaryIO,
        *,
        object_id: str,
        content_type: str | None,
        hash_algorithm: str,
        expected_size_bytes: int | None,
        expected_hash: str | None,
    ) -> StoredObject:
        payload = stream.read()

        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise StorageIntegrityError(
                "Binary storage stream returned non-binary content.",
                component="local_storage",
                operation="put",
                field="stream",
                context={
                    "object_id": object_id,
                    "received_type": type(payload).__name__,
                },
            )

        data = bytes(payload)
        digest = hashlib.new(hash_algorithm, data).hexdigest()
        size_bytes = len(data)

        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            raise StorageIntegrityError(
                "Stored content size does not match expected_size_bytes.",
                component="local_storage",
                operation="put",
                field="expected_size_bytes",
                context={
                    "object_id": object_id,
                    "expected_size_bytes": expected_size_bytes,
                    "actual_size_bytes": size_bytes,
                },
            )

        if expected_hash is not None and digest.casefold() != expected_hash.casefold():
            raise StorageIntegrityError(
                "Stored content hash does not match expected_hash.",
                component="local_storage",
                operation="put",
                field="expected_hash",
                context={
                    "object_id": object_id,
                    "hash_algorithm": hash_algorithm,
                },
            )

        metadata = StoredObject(
            object_id=object_id,
            size_bytes=size_bytes,
            content_hash=digest,
            hash_algorithm=hash_algorithm,
            content_type=content_type,
        )

        with self._lock:
            self._objects[object_id] = (data, metadata)

        return metadata

    def _open(self, object_id: str) -> BinaryIO:
        with self._lock:
            stored = self._objects.get(object_id)

        if stored is None:
            raise StorageNotFoundError(
                "Stored object does not exist.",
                component="local_storage",
                operation="open",
                context={"object_id": object_id},
            )

        payload, _ = stored
        return BytesIO(payload)

    def _stat(self, object_id: str) -> StoredObject | None:
        with self._lock:
            stored = self._objects.get(object_id)
            return None if stored is None else stored[1]

    def _delete(self, object_id: str) -> bool:
        with self._lock:
            return self._objects.pop(object_id, None) is not None


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

class InProcessQueue(Queue):
    """
    Idempotent process-local AuditJob submission adapter.

    This adapter acknowledges jobs and retains them for the lifetime of the
    process. It deliberately does not pretend to be a durable message broker.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local audit queue", "info")

        self._lock = RLock()
        self._receipts: dict[str, QueueReceipt] = {}
        self._jobs: dict[str, AuditJob] = {}

        super().__init__()

    def _enqueue(
        self,
        job: AuditJob,
        *,
        idempotency_key: str,
    ) -> QueueReceipt:
        with self._lock:
            existing = self._receipts.get(idempotency_key)

            if existing is not None:
                if existing.job_id != job.job_id:
                    raise QueueIntegrityError(
                        "Queue idempotency key is already bound to another job.",
                        component="local_queue",
                        operation="enqueue",
                        field="idempotency_key",
                        context={
                            "existing_job_id": existing.job_id,
                            "received_job_id": job.job_id,
                        },
                    )
                return existing

            receipt = QueueReceipt(
                job_id=job.job_id,
                queue_reference=f"local:{job.job_id}",
                idempotency_key=idempotency_key,
            )

            self._jobs[job.job_id] = job
            self._receipts[idempotency_key] = receipt

            return receipt

    def snapshot(self) -> tuple[AuditJob, ...]:
        """Return a stable process-local snapshot for deployment diagnostics."""
        with self._lock:
            return tuple(self._jobs.values())


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

class DisabledPayment(Payment):
    """
    Fail-closed payment adapter for deployments without a payment provider.

    It exists so the application graph can be composed without inventing a
    successful payment implementation or accepting unverifiable webhooks.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing disabled payment adapter", "info")
        super().__init__()

    def _create_checkout(
        self,
        order: Order,
        tier: ProductTier,
        *,
        idempotency_key: str,
    ) -> PaymentCheckout:
        del tier, idempotency_key

        raise PaymentUnavailableError(
            "No payment provider is configured for this BIMAP deployment.",
            component="disabled_payment",
            operation="create_checkout",
            context={"order_id": order.order_id},
        )

    def _verify_event(
        self,
        payload: bytes,
        *,
        signature: str,
    ) -> PaymentEvent:
        del payload, signature

        raise PaymentUnavailableError(
            "No payment provider is configured for this BIMAP deployment.",
            component="disabled_payment",
            operation="verify_event",
        )


# ---------------------------------------------------------------------------
# Malware
# ---------------------------------------------------------------------------

class DevelopmentMalware(Malware):
    """
    Explicit development-only malware boundary.

    Default behavior is fail-safe/indeterminate. ``trust_uploads=True`` exists
    only for controlled local integration work and must never be enabled in a
    production deployment.
    """

    def __init__(self, *, trust_uploads: bool = False) -> None:
        if not isinstance(trust_uploads, bool):
            raise TypeError("trust_uploads must be boolean")

        self._trust_uploads = trust_uploads

        printer.status(
            "BIMAP",
            (
                "Initializing development malware gate "
                f"(trust_uploads={trust_uploads})"
            ),
            "warning" if trust_uploads else "info",
        )

        super().__init__()

    @property
    def trust_uploads(self) -> bool:
        return self._trust_uploads

    def _scan_stream(
        self,
        stream: BinaryIO,
        *,
        object_id: str,
        filename: str | None,
        content_type: str | None,
        size_bytes: int | None,
    ) -> MalwareScanResult:
        del stream, filename, content_type, size_bytes

        return MalwareScanResult(
            object_id=object_id,
            verdict=(
                MalwareVerdict.CLEAN
                if self._trust_uploads
                else MalwareVerdict.INDETERMINATE
            ),
            scanned_at=datetime.now(timezone.utc),
            scanner_name="bimap-development-malware-gate",
            scanner_version="1",
        )


__all__ = [
    "SystemClock",
    "InMemoryAccounts",
    "LocalSLAIAuthentication",
    "InMemoryEntitlementStore",
    "CalendarUTCRenewalWindowResolver",
    "InMemoryRepository",
    "InMemoryStorage",
    "InProcessQueue",
    "DisabledPayment",
    "DevelopmentMalware",
]
