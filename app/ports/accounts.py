"""
BIMAP customer-account persistence port.

This port owns the stable application-facing contract for storing and resolving
canonical ``Account`` aggregates. Concrete implementations may use an in-memory
store, SQL database, document database, or another durable backend.

Concurrency and uniqueness
--------------------------
``save_account`` is an optimistic compare-and-save operation:

* ``expected_version is None`` means create-only; an existing account identity
  must cause ``RepositoryConflictError``.
* an integer ``expected_version`` means update only when the currently stored
  aggregate has exactly that revision.

Concrete adapters MUST atomically enforce uniqueness for:

* account_id;
* auth_user_id;
* username lookup key (case-insensitive);
* normalized email; and
* normalized E.164 phone number.

Preflight reads by application services improve diagnostics but are not a
substitute for these persistence constraints.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, TypeVar

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.accounts.models import Account
from ...domain.accounts.plans import AccountPlanCode
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Accounts Port")
printer = PrettyPrinter()

_COMPONENT = "accounts"
T = TypeVar("T")


def _run_account_operation(
    operation: str,
    callback: Callable[[], T],
    *,
    context: dict[str, Any],
) -> T:
    try:
        return callback()
    except AppError:
        raise
    except TimeoutError as exc:
        raise RepositoryTimeoutError(
            "Account repository operation timed out.",
            component=_COMPONENT,
            operation=operation,
            context=context,
            cause=exc,
        ) from exc
    except ConnectionError as exc:
        raise RepositoryUnavailableError(
            "Account repository backend is unavailable.",
            component=_COMPONENT,
            operation=operation,
            context=context,
            cause=exc,
        ) from exc
    except Exception as exc:
        raise RepositoryOperationError(
            "Account repository adapter failed to complete an operation.",
            component=_COMPONENT,
            operation=operation,
            context={
                **context,
                **lower_error_context(exc),
            },
            cause=exc,
        ) from exc


def _require_loaded_account(
    value: object | None,
    *,
    operation: str,
    identity_field: str,
    identity_value: str,
) -> Account | None:
    if value is None:
        return None

    if not isinstance(value, Account):
        raise RepositoryIntegrityError(
            "Account repository returned an unsupported record type.",
            component=_COMPONENT,
            operation=operation,
            field="result",
            context={"received_type": type(value).__name__},
        )

    if identity_field == "account_id":
        returned = value.account_id
        expected = identity_value
    elif identity_field == "auth_user_id":
        returned = value.auth_user_id
        expected = identity_value
    elif identity_field == "username":
        returned = Account.username_lookup_key(value.username)
        expected = Account.username_lookup_key(identity_value)
    elif identity_field == "email":
        returned = value.email
        expected = Account.normalize_email(identity_value)
    elif identity_field == "phone_e164":
        returned = value.phone_e164
        expected = Account.normalize_phone_e164(identity_value)
    else:
        raise RepositoryValidationError(
            "Unsupported account identity binding field.",
            component=_COMPONENT,
            operation=operation,
            field="identity_field",
            context={"identity_field": identity_field},
        )

    if returned != expected:
        raise RepositoryIntegrityError(
            "Account repository result identity does not match the lookup.",
            component=_COMPONENT,
            operation=operation,
            field=f"result.{identity_field}",
            context={"identity_field": identity_field},
        )

    return value


class Accounts(ABC):
    """Abstract persistence boundary for canonical BIMAP accounts."""

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing accounts repository port",
            event="accounts_init_start",
        )
        logger.debug(
            {
                "event": "accounts_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
    def _get_account(self, account_id: str) -> Account | None:
        raise NotImplementedError

    @abstractmethod
    def _get_by_auth_user_id(self, auth_user_id: str) -> Account | None:
        raise NotImplementedError

    @abstractmethod
    def _get_by_username(self, username_key: str) -> Account | None:
        raise NotImplementedError

    @abstractmethod
    def _get_by_email(self, normalized_email: str) -> Account | None:
        raise NotImplementedError

    @abstractmethod
    def _get_by_phone(self, phone_e164: str) -> Account | None:
        raise NotImplementedError

    @abstractmethod
    def _save_account(
        self,
        account: Account,
        *,
        expected_version: int | None,
    ) -> Account:
        raise NotImplementedError

    def get_account(self, account_id: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading account by identifier",
            event="accounts_get_start",
            context={"account_id": account_id},
        )
        target = require_app_text(
            account_id,
            field="account_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_account",
            max_length=512,
        )
        value = _run_account_operation(
            "get_account",
            lambda: self._get_account(target),
            context={"account_id": target},
        )
        return _require_loaded_account(
            value,
            operation="get_account",
            identity_field="account_id",
            identity_value=target,
        )

    def get_by_auth_user_id(self, auth_user_id: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading account by authentication identity",
            event="accounts_get_by_auth_user_id_start",
        )
        target = require_app_text(
            auth_user_id,
            field="auth_user_id",
            error_type=RepositoryValidationError,
            component=_COMPONENT,
            operation="get_by_auth_user_id",
            max_length=512,
        )
        value = _run_account_operation(
            "get_by_auth_user_id",
            lambda: self._get_by_auth_user_id(target),
            context={"auth_user_id": target},
        )
        return _require_loaded_account(
            value,
            operation="get_by_auth_user_id",
            identity_field="auth_user_id",
            identity_value=target,
        )

    def get_by_username(self, username: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading account by username",
            event="accounts_get_by_username_start",
        )
        try:
            lookup_key = Account.username_lookup_key(username)
        except Exception as exc:
            raise RepositoryValidationError(
                "Username lookup value is invalid.",
                component=_COMPONENT,
                operation="get_by_username",
                field="username",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        value = _run_account_operation(
            "get_by_username",
            lambda: self._get_by_username(lookup_key),
            context={"username_key": lookup_key},
        )
        return _require_loaded_account(
            value,
            operation="get_by_username",
            identity_field="username",
            identity_value=username,
        )

    def get_by_email(self, email: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading account by email",
            event="accounts_get_by_email_start",
        )
        try:
            normalized = Account.normalize_email(email)
        except Exception as exc:
            raise RepositoryValidationError(
                "Email lookup value is invalid.",
                component=_COMPONENT,
                operation="get_by_email",
                field="email",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        value = _run_account_operation(
            "get_by_email",
            lambda: self._get_by_email(normalized),
            context={"lookup_kind": "email"},
        )
        return _require_loaded_account(
            value,
            operation="get_by_email",
            identity_field="email",
            identity_value=normalized,
        )

    def get_by_phone(self, phone_e164: str) -> Account | None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading account by phone number",
            event="accounts_get_by_phone_start",
        )
        try:
            normalized = Account.normalize_phone_e164(phone_e164)
        except Exception as exc:
            raise RepositoryValidationError(
                "Phone lookup value is invalid.",
                component=_COMPONENT,
                operation="get_by_phone",
                field="phone_e164",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        value = _run_account_operation(
            "get_by_phone",
            lambda: self._get_by_phone(normalized),
            context={"lookup_kind": "phone_e164"},
        )
        return _require_loaded_account(
            value,
            operation="get_by_phone",
            identity_field="phone_e164",
            identity_value=normalized,
        )

    def save_account(
        self,
        account: Account,
        *,
        expected_version: int | None,
    ) -> Account:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Saving account aggregate",
            event="accounts_save_start",
            context={
                "account_id": getattr(account, "account_id", None),
                "expected_version": expected_version,
            },
        )
        if not isinstance(account, Account):
            raise RepositoryValidationError(
                "save_account requires a canonical Account aggregate.",
                component=_COMPONENT,
                operation="save_account",
                field="account",
                context={"received_type": type(account).__name__},
            )

        normalized_expected = (
            None
            if expected_version is None
            else require_non_negative_int(
                expected_version,
                field="expected_version",
                error_type=RepositoryValidationError,
                component=_COMPONENT,
                operation="save_account",
            )
        )

        value = _run_account_operation(
            "save_account",
            lambda: self._save_account(
                account,
                expected_version=normalized_expected,
            ),
            context={
                "account_id": account.account_id,
                "expected_version": normalized_expected,
            },
        )

        if not isinstance(value, Account):
            raise RepositoryIntegrityError(
                "Account repository save returned an unsupported record type.",
                component=_COMPONENT,
                operation="save_account",
                field="result",
                context={"received_type": type(value).__name__},
            )

        if value.account_id != account.account_id:
            raise RepositoryIntegrityError(
                "Account repository save changed account identity.",
                component=_COMPONENT,
                operation="save_account",
                field="result.account_id",
            )

        if value.version != account.version:
            raise RepositoryIntegrityError(
                "Account repository save changed aggregate revision.",
                component=_COMPONENT,
                operation="save_account",
                field="result.version",
                context={
                    "expected_version": account.version,
                    "returned_version": value.version,
                },
            )

        return value

    def resolve_plan_code(self, account_id: str) -> AccountPlanCode:
        """
        Resolve the authoritative account plan for entitlement consumption.

        This method intentionally satisfies the structural resolver contract
        consumed by ``EntitlementService`` and prevents a second plan mapping
        from becoming a competing source of truth.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving account plan code",
            event="accounts_plan_resolve_start",
            context={"account_id": account_id},
        )
        account = self.get_account(account_id)
        if account is None:
            raise RepositoryValidationError(
                "Account does not exist.",
                component=_COMPONENT,
                operation="resolve_plan_code",
                field="account_id",
                context={"account_id": account_id},
            )
        return account.plan_code


__all__ = ["Accounts"]


if __name__ == "__main__":
    print("\n=== Running Accounts Port Self-Test ===\n")
    printer.status("TEST", "Accounts port module initialized", "info")

    class _MemoryAccounts(Accounts):
        def __init__(self) -> None:
            self.items: dict[str, Account] = {}
            super().__init__()

        def _get_account(self, account_id: str) -> Account | None:
            return self.items.get(account_id)

        def _get_by_auth_user_id(self, auth_user_id: str) -> Account | None:
            return next((a for a in self.items.values() if a.auth_user_id == auth_user_id), None)

        def _get_by_username(self, username_key: str) -> Account | None:
            return next(
                (a for a in self.items.values() if Account.username_lookup_key(a.username) == username_key),
                None,
            )

        def _get_by_email(self, normalized_email: str) -> Account | None:
            return next((a for a in self.items.values() if a.email == normalized_email), None)

        def _get_by_phone(self, phone_e164: str) -> Account | None:
            return next((a for a in self.items.values() if a.phone_e164 == phone_e164), None)

        def _save_account(self, account: Account, *, expected_version: int | None) -> Account:
            existing = self.items.get(account.account_id)
            if expected_version is None:
                if existing is not None:
                    raise RepositoryConflictError("Account already exists.")
            elif existing is None or existing.version != expected_version:
                raise RepositoryConflictError("Account revision conflict.")
            self.items[account.account_id] = account
            return account

    repository = _MemoryAccounts()
    account = Account.create_pending(
        account_id="account-1",
        auth_user_id="auth-1",
        username="sample-user",
        email="sample@example.com",
        phone_e164="+31612345678",
        country="NL",
        name="Sample",
        surname="User",
        created_at="2026-09-08T12:00:00Z",
    )
    repository.save_account(account, expected_version=None)
    assert repository.get_by_username("SAMPLE-USER") == account
    assert repository.resolve_plan_code("account-1") is AccountPlanCode.BASIC
    printer.status("PASS", "Account persistence and plan resolution", "success")
    print("\n=== Test ran successfully ===\n")
