"""
Application service for canonical BIMAP customer accounts.

``AccountService`` owns account/profile lifecycle orchestration above the
immutable domain aggregate and the ``Accounts`` persistence port. It does not
perform password verification, issue sessions, send verification codes, store
uploads, calculate reward balances, or scan global order/report collections.

The service also validates plan assignment against the configured
``AccountPlanCatalog``. ``Account.plan_code`` remains the authoritative plan
assignment persisted with the account; entitlement consumers can resolve it
through ``Accounts.resolve_plan_code``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ports.accounts import Accounts
from ..ports.clock import Clock
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.accounts.models import Account, AccountStatus
from ...domain.accounts.plans import AccountPlanCatalog, AccountPlanCode
from ...domain.utils.domain_errors import DomainError, DomainInvariantError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Account Service")
printer = PrettyPrinter()

_COMPONENT = "account_service"


def _translate_domain_error(
    exc: DomainError,
    *,
    operation: str,
    message: str,
    field: str | None = None,
) -> AppError:
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Translating account-domain failure",
        event="account_service_domain_error_translate_start",
        context={"operation": operation, "error_type": type(exc).__name__},
    )
    error_type = AppIntegrityError if isinstance(exc, DomainInvariantError) else AppValidationError
    return error_type(
        message,
        component=_COMPONENT,
        operation=operation,
        field=field,
        context=lower_error_context(exc),
        cause=exc,
    )


@dataclass(frozen=True, slots=True)
class AccountProfileView:
    """Transport-neutral account-profile projection for API mapping."""

    user_id: str
    username: str
    name: str
    surname: str
    phone_e164: str
    email: str
    plan: AccountPlanCode
    occupation: str | None = None
    business: str | None = None
    avatar_url: str | None = None

    @classmethod
    def from_account(cls, account: Account) -> "AccountProfileView":
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Projecting account profile",
            event="account_service_profile_project_start",
            context={"account_id": getattr(account, "account_id", None)},
        )
        if not isinstance(account, Account):
            raise AppValidationError(
                "AccountProfileView requires a canonical Account.",
                component=_COMPONENT,
                operation="project_profile",
                field="account",
                context={"received_type": type(account).__name__},
            )
        return cls(
            user_id=account.account_id,
            username=account.username,
            name=account.name,
            surname=account.surname,
            occupation=account.occupation,
            business=account.business,
            phone_e164=account.phone_e164,
            email=account.email,
            avatar_url=account.avatar_url,
            plan=account.plan_code,
        )

    def to_dict(self) -> dict[str, Any]:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing account profile projection",
            event="account_service_profile_to_dict_start",
        )
        return {
            "user_id": self.user_id,
            "username": self.username,
            "name": self.name,
            "surname": self.surname,
            "occupation": self.occupation,
            "business": self.business,
            "phone_e164": self.phone_e164,
            "email": self.email,
            "avatar_url": self.avatar_url,
            "plan": self.plan.value,
        }


class AccountService:
    """Coordinate account creation, profile state, plan state, and lifecycle."""

    def __init__(
        self,
        accounts: Accounts,
        clock: Clock,
        *,
        plan_catalog: AccountPlanCatalog,
        default_plan: AccountPlanCode | str = AccountPlanCode.BASIC,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing account service",
            event="account_service_init_start",
        )
        if not isinstance(accounts, Accounts):
            raise AppConfigurationError(
                "accounts must implement the BIMAP Accounts port.",
                component=_COMPONENT,
                operation="initialize",
                field="accounts",
                context={"received_type": type(accounts).__name__},
            )
        if not isinstance(clock, Clock):
            raise AppConfigurationError(
                "clock must implement the BIMAP Clock port.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
                context={"received_type": type(clock).__name__},
            )
        if not isinstance(plan_catalog, AccountPlanCatalog):
            raise AppConfigurationError(
                "plan_catalog must be an AccountPlanCatalog.",
                component=_COMPONENT,
                operation="initialize",
                field="plan_catalog",
                context={"received_type": type(plan_catalog).__name__},
            )

        try:
            normalized_default = AccountPlanCode.parse(default_plan)
            plan_catalog.get(normalized_default)
        except DomainError as exc:
            raise AppConfigurationError(
                "Default account plan is not present in the configured plan catalog.",
                component=_COMPONENT,
                operation="initialize",
                field="default_plan",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        self.accounts = accounts
        self.clock = clock
        self.plan_catalog = plan_catalog
        self.default_plan = normalized_default

        logger.info(
            {
                "event": "account_service_initialized",
                "default_plan": self.default_plan.value,
                "configured_plan_count": len(self.plan_catalog.plans),
            }
        )

    def find_account(self, account_id: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding account",
            event="account_service_find_start",
            context={"account_id": account_id},
        )
        return self.accounts.get_account(account_id)

    def get_account(self, account_id: str) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading required account",
            event="account_service_get_start",
            context={"account_id": account_id},
        )
        account = self.find_account(account_id)
        if account is None:
            raise AppValidationError(
                "Account does not exist.",
                component=_COMPONENT,
                operation="get_account",
                field="account_id",
                context={"account_id": account_id},
            )
        return account

    def find_by_auth_user_id(self, auth_user_id: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding account by authentication identity",
            event="account_service_find_auth_identity_start",
        )
        return self.accounts.get_by_auth_user_id(auth_user_id)

    def find_by_username(self, username: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding account by username",
            event="account_service_find_username_start",
        )
        return self.accounts.get_by_username(username)

    def find_by_email(self, email: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding account by email",
            event="account_service_find_email_start",
        )
        return self.accounts.get_by_email(email)

    def find_by_phone(self, phone_e164: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Finding account by phone",
            event="account_service_find_phone_start",
        )
        return self.accounts.get_by_phone(phone_e164)

    def create_pending_account(
        self,
        *,
        auth_user_id: str,
        username: str,
        email: str,
        phone_e164: str,
        country: str,
        name: str,
        surname: str,
        occupation: str | None = None,
        business: str | None = None,
        account_id: str | None = None,
        plan_code: AccountPlanCode | str | None = None,
    ) -> Account:
        """Create one pending account after deterministic uniqueness preflight."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating pending account",
            event="account_service_create_pending_start",
        )

        try:
            normalized_username = Account.normalize_username(username)
            normalized_email = Account.normalize_email(email)
            normalized_phone = Account.normalize_phone_e164(phone_e164)
            normalized_country = Account.normalize_country(country)
            selected_plan = (
                self.default_plan
                if plan_code is None
                else AccountPlanCode.parse(plan_code)
            )
            self.plan_catalog.get(selected_plan)
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="create_pending_account",
                message="Account signup data is invalid.",
            ) from exc

        conflicts = (
            ("auth_user_id", self.find_by_auth_user_id(auth_user_id)),
            ("username", self.find_by_username(normalized_username)),
            ("email", self.find_by_email(normalized_email)),
            ("phone_e164", self.find_by_phone(normalized_phone)),
        )
        for field, existing in conflicts:
            if existing is not None:
                raise AppValidationError(
                    "An account already exists for the supplied signup identity.",
                    component=_COMPONENT,
                    operation="create_pending_account",
                    field=field,
                )

        try:
            account = Account.create_pending(
                account_id=account_id,
                auth_user_id=auth_user_id,
                username=normalized_username,
                email=normalized_email,
                phone_e164=normalized_phone,
                country=normalized_country,
                name=name,
                surname=surname,
                occupation=occupation,
                business=business,
                plan_code=selected_plan,
                created_at=self.clock.now(),
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="create_pending_account",
                message="Account signup data does not satisfy account constraints.",
            ) from exc

        persisted = self.accounts.save_account(
            account,
            expected_version=None,
        )
        self._require_persisted_account(
            persisted,
            expected=account,
            operation="create_pending_account",
        )

        logger.info(
            {
                "event": "account_service_account_created",
                "account_id": persisted.account_id,
                "plan_code": persisted.plan_code.value,
                "status": persisted.status.value,
            }
        )
        return persisted

    def record_signup_verification(
        self,
        account_id: str,
        *,
        email_verified: bool,
        phone_verified: bool,
    ) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Recording signup verification",
            event="account_service_signup_verification_start",
            context={"account_id": account_id},
        )
        current = self.get_account(account_id)
        try:
            changed = current.record_verification(
                email_verified=email_verified,
                phone_verified=phone_verified,
                verified_at=self.clock.now(),
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="record_signup_verification",
                message="Signup verification cannot be applied to this account.",
                field="verification",
            ) from exc
        return self._persist_change(current, changed, operation="record_signup_verification")

    def update_profile(
        self,
        account_id: str,
        *,
        name: str | None = None,
        surname: str | None = None,
        occupation: str | None = None,
        business: str | None = None,
    ) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Updating account profile",
            event="account_service_profile_update_start",
            context={"account_id": account_id},
        )
        current = self.get_account(account_id)
        try:
            changed = current.with_profile(
                name=name,
                surname=surname,
                occupation=occupation,
                business=business,
                changed_at=self.clock.now(),
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="update_profile",
                message="Account profile update is invalid.",
            ) from exc
        return self._persist_change(current, changed, operation="update_profile")

    def set_avatar_url(self, account_id: str, avatar_url: str | None) -> Account:
        """
        Bind an already trusted/stored avatar URL.

        Raw multipart upload processing and object storage intentionally remain
        outside this service.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Updating account avatar",
            event="account_service_avatar_update_start",
            context={"account_id": account_id, "has_avatar": avatar_url is not None},
        )
        current = self.get_account(account_id)
        try:
            changed = current.with_avatar_url(
                avatar_url,
                changed_at=self.clock.now(),
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="set_avatar_url",
                message="Avatar reference is invalid.",
                field="avatar_url",
            ) from exc
        return self._persist_change(current, changed, operation="set_avatar_url")

    def assign_plan(
        self,
        account_id: str,
        plan_code: AccountPlanCode | str,
    ) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Assigning account plan",
            event="account_service_plan_assign_start",
            context={"account_id": account_id},
        )
        try:
            plan = AccountPlanCode.parse(plan_code)
            self.plan_catalog.get(plan)
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="assign_plan",
                message="Requested account plan is not configured.",
                field="plan_code",
            ) from exc

        current = self.get_account(account_id)
        try:
            changed = current.with_plan(plan, changed_at=self.clock.now())
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="assign_plan",
                message="Account plan cannot be changed in the current lifecycle state.",
                field="plan_code",
            ) from exc
        return self._persist_change(current, changed, operation="assign_plan")

    def suspend_account(self, account_id: str) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Suspending account",
            event="account_service_suspend_start",
            context={"account_id": account_id},
        )
        current = self.get_account(account_id)
        try:
            changed = current.suspend(changed_at=self.clock.now())
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="suspend_account",
                message="Account cannot be suspended in its current state.",
                field="status",
            ) from exc
        return self._persist_change(current, changed, operation="suspend_account")

    def reactivate_account(self, account_id: str) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Reactivating account",
            event="account_service_reactivate_start",
            context={"account_id": account_id},
        )
        current = self.get_account(account_id)
        try:
            changed = current.reactivate(changed_at=self.clock.now())
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="reactivate_account",
                message="Account cannot be reactivated in its current state.",
                field="status",
            ) from exc
        return self._persist_change(current, changed, operation="reactivate_account")

    def close_account(self, account_id: str) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Closing account",
            event="account_service_close_start",
            context={"account_id": account_id},
        )
        current = self.get_account(account_id)
        try:
            changed = current.close(changed_at=self.clock.now())
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation="close_account",
                message="Account cannot be closed.",
                field="status",
            ) from exc
        return self._persist_change(current, changed, operation="close_account")

    def profile(self, account_id: str) -> AccountProfileView:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading account profile projection",
            event="account_service_profile_start",
            context={"account_id": account_id},
        )
        return AccountProfileView.from_account(self.get_account(account_id))

    def _persist_change(
        self,
        current: Account,
        changed: Account,
        *,
        operation: str,
    ) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Persisting account change",
            event="account_service_persist_change_start",
            context={"account_id": current.account_id, "operation": operation},
        )
        if changed is current:
            return current

        if changed.account_id != current.account_id:
            raise AppIntegrityError(
                "Account mutation changed aggregate identity.",
                component=_COMPONENT,
                operation=operation,
                field="account_id",
            )
        if changed.version != current.version + 1:
            raise AppIntegrityError(
                "Account mutation must increment version exactly once.",
                component=_COMPONENT,
                operation=operation,
                field="version",
                context={
                    "current_version": current.version,
                    "changed_version": changed.version,
                },
            )

        persisted = self.accounts.save_account(
            changed,
            expected_version=current.version,
        )
        self._require_persisted_account(
            persisted,
            expected=changed,
            operation=operation,
        )
        return persisted

    def _require_persisted_account(
        self,
        persisted: Account,
        *,
        expected: Account,
        operation: str,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating persisted account",
            event="account_service_persisted_validate_start",
            context={"account_id": expected.account_id, "operation": operation},
        )
        if not isinstance(persisted, Account):
            raise AppIntegrityError(
                "Account repository returned an unsupported aggregate.",
                component=_COMPONENT,
                operation=operation,
                field="persisted_account",
                context={"received_type": type(persisted).__name__},
            )

        immutable_identity = (
            persisted.account_id == expected.account_id
            and persisted.auth_user_id == expected.auth_user_id
            and persisted.username == expected.username
            and persisted.email == expected.email
            and persisted.phone_e164 == expected.phone_e164
        )
        if not immutable_identity or persisted.version != expected.version:
            raise AppIntegrityError(
                "Account repository write-back changed identity or revision.",
                component=_COMPONENT,
                operation=operation,
                field="persisted_account",
                context={
                    "expected_version": expected.version,
                    "returned_version": persisted.version,
                },
            )


__all__ = [
    "AccountProfileView",
    "AccountService",
]
