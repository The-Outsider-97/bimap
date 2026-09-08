"""
Canonical BIMAP customer-account aggregate.

This module owns customer account identity/profile state that is specific to
BIMAP. Password hashes, access/refresh tokens, verification-code generation,
provider delivery, persistence, HTTP cookies, and authentication-provider
implementation details belong to higher architectural layers.

Authoritative plan ownership
----------------------------
``Account.plan_code`` is the canonical account-level plan assignment. Higher
layers that need a plan resolver should read this field through the account
persistence port rather than maintaining a second mutable plan mapping.

Security and privacy
--------------------
The aggregate stores only profile/contact values required by BIMAP. It never
stores plaintext credentials, verification codes, session tokens, provider
secrets, or password hashes.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .plans import AccountPlanCode
from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Accounts Models")
printer = PrettyPrinter()

_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_EMAIL_LOCAL_RE = re.compile(r"^[^\s@]{1,64}$")
_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class AccountStatus(str, Enum):
    """Canonical BIMAP customer-account lifecycle state."""

    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"

    @classmethod
    def parse(cls, value: Any) -> "AccountStatus":
        _announce("Parsing account status")

        if isinstance(value, cls):
            return value

        normalized = require_text(
            value,
            field="status",
            max_length=64).lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise DomainValidationError(
                "Unsupported BIMAP account status.",
                field="status",
                context={
                    "received": normalized,
                    "allowed": tuple(item.value for item in cls),
                },
            ) from exc


def _announce(action: str) -> None:
    printer.status("ACCOUNTS", action, "info")
    logger.debug(action)


def _normalize_username(value: Any) -> str:
    username = require_text(value, field="username", max_length=64)

    if not _USERNAME_RE.fullmatch(username):
        raise DomainValidationError(
            "Username must be 3-64 characters and contain only letters, "
            "numbers, periods, underscores, or hyphens.",
            field="username",
        )

    return username


def _normalize_email(value: Any) -> str:
    raw = require_text(value, field="email", max_length=254)

    if raw.count("@") != 1:
        raise DomainValidationError(
            "Email address must contain exactly one @ separator.",
            field="email",
        )

    local, domain = raw.rsplit("@", 1)

    if not _EMAIL_LOCAL_RE.fullmatch(local):
        raise DomainValidationError(
            "Email local-part is malformed.",
            field="email",
        )

    domain = domain.rstrip(".")
    if not domain or len(domain) > 253:
        raise DomainValidationError(
            "Email domain is malformed.",
            field="email",
        )

    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainValidationError(
            "Email domain cannot be normalized using IDNA.",
            field="email",
        ) from exc

    labels = ascii_domain.split(".")
    if any(not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
        raise DomainValidationError(
            "Email domain contains an invalid DNS label.",
            field="email",
        )

    # Account lookup is deliberately case-insensitive. Preserving local-part
    # case would create duplicate-account ambiguity for the application layer.
    normalized = f"{local}@{ascii_domain}".casefold()

    if len(normalized) > 254:
        raise DomainValidationError(
            "Email address exceeds the permitted length.",
            field="email",
        )

    return normalized


def _normalize_phone_e164(value: Any) -> str:
    phone = require_text(value, field="phone_e164", max_length=16)

    if not _E164_RE.fullmatch(phone):
        raise DomainValidationError(
            "Phone number must use canonical E.164 form.",
            field="phone_e164",
        )

    return phone


def _normalize_country(value: Any) -> str:
    country = require_text(value, field="country", max_length=2)

    if not _COUNTRY_RE.fullmatch(country):
        raise DomainValidationError(
            "Country must be an ISO 3166-1 alpha-2 code.",
            field="country",
        )

    return country.upper()


def _normalize_avatar_url(value: Any) -> str | None:
    avatar_url = optional_text(value, field="avatar_url", max_length=2048)
    if avatar_url is None:
        return None

    parsed = urlparse(avatar_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DomainValidationError(
            "Avatar URL must be an absolute HTTP(S) URL.",
            field="avatar_url",
        )

    if parsed.username or parsed.password:
        raise DomainValidationError(
            "Avatar URL must not contain credentials.",
            field="avatar_url",
        )

    return avatar_url


@dataclass(frozen=True, slots=True)
class Account:
    """
    Canonical BIMAP customer account.

    ``auth_user_id`` binds the BIMAP account to the external/reusable
    authentication subsystem. It is an identifier only, never a credential.

    ``version`` is the optimistic-concurrency revision used by ``Accounts``.
    Every semantic mutation increments it exactly once.
    """

    account_id: str
    auth_user_id: str
    username: str
    email: str
    phone_e164: str
    country: str
    name: str
    surname: str
    plan_code: AccountPlanCode = AccountPlanCode.BASIC
    status: AccountStatus = AccountStatus.PENDING_VERIFICATION
    created_at: datetime = dataclass_field(default_factory=utc_now)
    updated_at: datetime = dataclass_field(default_factory=utc_now)
    email_verified_at: datetime | None = None
    phone_verified_at: datetime | None = None
    occupation: str | None = None
    business: str | None = None
    avatar_url: str | None = None
    version: int = 0

    def __post_init__(self) -> None:
        _announce("Validating account aggregate")

        account_id = require_text(self.account_id, field="account_id", max_length=512)
        auth_user_id = require_text(self.auth_user_id, field="auth_user_id", max_length=512)
        username = _normalize_username(self.username)
        email = _normalize_email(self.email)
        phone_e164 = _normalize_phone_e164(self.phone_e164)
        country = _normalize_country(self.country)
        name = require_text(self.name, field="name", max_length=128)
        surname = require_text(self.surname, field="surname", max_length=128)

        try:
            plan_code = AccountPlanCode.parse(self.plan_code)
        except Exception as exc:
            if isinstance(exc, DomainValidationError):
                raise
            raise DomainValidationError(
                "Account plan code is invalid.",
                field="plan_code",
            ) from exc

        status = AccountStatus.parse(self.status)
        created_at = ensure_utc_datetime(self.created_at, field="created_at")
        updated_at = ensure_utc_datetime(self.updated_at, field="updated_at")

        if updated_at < created_at:
            raise DomainInvariantError(
                "Account updated_at cannot precede created_at.",
                field="updated_at",
                context={"account_id": account_id},
            )

        email_verified_at = self._normalize_verification_time(
            self.email_verified_at,
            field="email_verified_at",
            created_at=created_at,
            updated_at=updated_at,
        )
        phone_verified_at = self._normalize_verification_time(
            self.phone_verified_at,
            field="phone_verified_at",
            created_at=created_at,
            updated_at=updated_at,
        )

        occupation = optional_text(self.occupation, field="occupation", max_length=256)
        business = optional_text(self.business, field="business", max_length=256)
        avatar_url = _normalize_avatar_url(self.avatar_url)

        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError(
                "Account version must be an integer.",
                field="version",
                context={"received_type": type(self.version).__name__},
            )

        if self.version < 0:
            raise DomainValidationError(
                "Account version must be non-negative.",
                field="version",
                context={"received": self.version},
            )

        fully_verified = (
            email_verified_at is not None
            and phone_verified_at is not None
        )

        if status in {AccountStatus.ACTIVE, AccountStatus.SUSPENDED} and not fully_verified:
            raise DomainInvariantError(
                "Active or suspended account requires verified email and phone channels.",
                field="status",
                context={"account_id": account_id},
            )

        if status is AccountStatus.PENDING_VERIFICATION and fully_verified:
            raise DomainInvariantError(
                "Fully verified account cannot remain pending verification.",
                field="status",
                context={"account_id": account_id},
            )

        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "auth_user_id", auth_user_id)
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "phone_e164", phone_e164)
        object.__setattr__(self, "country", country)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "surname", surname)
        object.__setattr__(self, "plan_code", plan_code)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "email_verified_at", email_verified_at)
        object.__setattr__(self, "phone_verified_at", phone_verified_at)
        object.__setattr__(self, "occupation", occupation)
        object.__setattr__(self, "business", business)
        object.__setattr__(self, "avatar_url", avatar_url)

    @staticmethod
    def _normalize_verification_time(
        value: datetime | str | None,
        *,
        field: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> datetime | None:
        _announce(f"Normalizing {field}")

        if value is None:
            return None

        timestamp = ensure_utc_datetime(value, field=field)

        if timestamp < created_at:
            raise DomainInvariantError(
                "Verification timestamp cannot precede account creation.",
                field=field,
            )

        if timestamp > updated_at:
            raise DomainInvariantError(
                "Verification timestamp cannot exceed account updated_at.",
                field=field,
            )

        return timestamp

    @classmethod
    def create_pending(
        cls,
        *,
        auth_user_id: str,
        username: str,
        email: str,
        phone_e164: str,
        country: str,
        name: str,
        surname: str,
        plan_code: AccountPlanCode | str = AccountPlanCode.BASIC,
        account_id: str | None = None,
        occupation: str | None = None,
        business: str | None = None,
        created_at: datetime | str | None = None,
    ) -> "Account":
        """Create a new, unverified account without inventing verification state."""
        _announce("Creating pending account")

        timestamp = (
            utc_now()
            if created_at is None
            else ensure_utc_datetime(created_at, field="created_at")
        )

        return cls(
            account_id=account_id or uuid4().hex,
            auth_user_id=auth_user_id,
            username=username,
            email=email,
            phone_e164=phone_e164,
            country=country,
            name=name,
            surname=surname,
            plan_code=AccountPlanCode.parse(plan_code),
            status=AccountStatus.PENDING_VERIFICATION,
            created_at=timestamp,
            updated_at=timestamp,
            occupation=occupation,
            business=business,
            version=0,
        )

    @staticmethod
    def normalize_username(value: Any) -> str:
        _announce("Normalizing account username")
        return _normalize_username(value)

    @staticmethod
    def username_lookup_key(value: Any) -> str:
        _announce("Creating username lookup key")
        return _normalize_username(value).casefold()

    @staticmethod
    def normalize_email(value: Any) -> str:
        _announce("Normalizing account email")
        return _normalize_email(value)

    @staticmethod
    def normalize_phone_e164(value: Any) -> str:
        _announce("Normalizing account phone number")
        return _normalize_phone_e164(value)

    @staticmethod
    def normalize_country(value: Any) -> str:
        _announce("Normalizing account country")
        return _normalize_country(value)

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_phone_verified(self) -> bool:
        return self.phone_verified_at is not None

    @property
    def is_fully_verified(self) -> bool:
        return self.is_email_verified and self.is_phone_verified

    @property
    def can_authenticate(self) -> bool:
        return self.status is AccountStatus.ACTIVE and self.is_fully_verified

    def record_verification(
        self,
        *,
        email_verified: bool = False,
        phone_verified: bool = False,
        verified_at: datetime | str | None = None,
    ) -> "Account":
        """
        Record successful verification channels and activate when both exist.

        A verification channel is monotonic: once verified, this method never
        removes its timestamp. Contact-address changes should be modeled as a
        separate future operation that explicitly resets affected verification.
        """
        _announce("Recording account verification")

        if not isinstance(email_verified, bool) or not isinstance(phone_verified, bool):
            raise DomainValidationError(
                "Verification flags must be boolean.",
                field="verification",
            )

        if self.status is AccountStatus.CLOSED:
            raise DomainInvariantError(
                "Closed account cannot receive verification updates.",
                field="status",
                context={"account_id": self.account_id},
            )

        if not email_verified and not phone_verified:
            return self

        timestamp = (
            utc_now()
            if verified_at is None
            else ensure_utc_datetime(verified_at, field="verified_at")
        )
        self._require_change_time(timestamp)

        new_email_verified_at = (
            self.email_verified_at
            if self.email_verified_at is not None or not email_verified
            else timestamp
        )
        new_phone_verified_at = (
            self.phone_verified_at
            if self.phone_verified_at is not None or not phone_verified
            else timestamp
        )

        new_status = self.status
        if (
            new_email_verified_at is not None
            and new_phone_verified_at is not None
            and self.status is AccountStatus.PENDING_VERIFICATION
        ):
            new_status = AccountStatus.ACTIVE

        if (
            new_email_verified_at == self.email_verified_at
            and new_phone_verified_at == self.phone_verified_at
            and new_status is self.status
        ):
            return self

        return replace(
            self,
            email_verified_at=new_email_verified_at,
            phone_verified_at=new_phone_verified_at,
            status=new_status,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def with_profile(
        self,
        *,
        name: str | None = None,
        surname: str | None = None,
        occupation: str | None = None,
        business: str | None = None,
        changed_at: datetime | str | None = None,
    ) -> "Account":
        """Return an account with editable non-authentication profile fields."""
        _announce("Updating account profile")

        self._require_mutable()
        timestamp = self._change_time(changed_at)

        new_name = self.name if name is None else require_text(name, field="name", max_length=128)
        new_surname = (
            self.surname
            if surname is None
            else require_text(surname, field="surname", max_length=128)
        )
        new_occupation = (
            self.occupation
            if occupation is None
            else optional_text(occupation, field="occupation", max_length=256)
        )
        new_business = (
            self.business
            if business is None
            else optional_text(business, field="business", max_length=256)
        )

        if (
            new_name == self.name
            and new_surname == self.surname
            and new_occupation == self.occupation
            and new_business == self.business
        ):
            return self

        return replace(
            self,
            name=new_name,
            surname=new_surname,
            occupation=new_occupation,
            business=new_business,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def with_avatar_url(
        self,
        avatar_url: str | None,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Account":
        """Bind a trusted application-issued avatar URL to the account."""
        _announce("Updating account avatar URL")

        self._require_mutable()
        normalized = _normalize_avatar_url(avatar_url)

        if normalized == self.avatar_url:
            return self

        timestamp = self._change_time(changed_at)
        return replace(
            self,
            avatar_url=normalized,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def with_plan(
        self,
        plan_code: AccountPlanCode | str,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Account":
        """Return an account with a new canonical plan assignment."""
        _announce("Updating account plan")

        self._require_mutable()
        plan = AccountPlanCode.parse(plan_code)

        if plan is self.plan_code:
            return self

        timestamp = self._change_time(changed_at)
        return replace(
            self,
            plan_code=plan,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def suspend(
        self,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Account":
        """Suspend an active account without discarding verification state."""
        _announce("Suspending account")

        if self.status is AccountStatus.SUSPENDED:
            return self

        if self.status is not AccountStatus.ACTIVE:
            raise DomainInvariantError(
                "Only active accounts can be suspended.",
                field="status",
                context={"status": self.status.value},
            )

        timestamp = self._change_time(changed_at)
        return replace(
            self,
            status=AccountStatus.SUSPENDED,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def reactivate(
        self,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Account":
        """Reactivate a previously suspended, fully verified account."""
        _announce("Reactivating account")

        if self.status is AccountStatus.ACTIVE:
            return self

        if self.status is not AccountStatus.SUSPENDED:
            raise DomainInvariantError(
                "Only suspended accounts can be reactivated.",
                field="status",
                context={"status": self.status.value},
            )

        if not self.is_fully_verified:
            raise DomainInvariantError(
                "Suspended account must remain fully verified before reactivation.",
                field="status",
            )

        timestamp = self._change_time(changed_at)
        return replace(
            self,
            status=AccountStatus.ACTIVE,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def close(
        self,
        *,
        changed_at: datetime | str | None = None,
    ) -> "Account":
        """Close the account. Closed is terminal for this aggregate."""
        _announce("Closing account")

        if self.status is AccountStatus.CLOSED:
            return self

        timestamp = self._change_time(changed_at)
        return replace(
            self,
            status=AccountStatus.CLOSED,
            updated_at=timestamp,
            version=self.version + 1,
        )

    def _require_mutable(self) -> None:
        _announce("Validating account mutability")

        if self.status is AccountStatus.CLOSED:
            raise DomainInvariantError(
                "Closed account cannot be modified.",
                field="status",
                context={"account_id": self.account_id},
            )

    def _change_time(self, value: datetime | str | None) -> datetime:
        _announce("Resolving account change timestamp")

        timestamp = (
            utc_now()
            if value is None
            else ensure_utc_datetime(value, field="changed_at")
        )
        self._require_change_time(timestamp)
        return timestamp

    def _require_change_time(self, timestamp: datetime) -> None:
        _announce("Validating account change timestamp")

        if timestamp < self.updated_at:
            raise DomainInvariantError(
                "Account change timestamp cannot precede updated_at.",
                field="changed_at",
                context={"account_id": self.account_id},
            )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-ready account state without credentials."""
        _announce("Serializing account aggregate")

        return {
            "account_id": self.account_id,
            "auth_user_id": self.auth_user_id,
            "username": self.username,
            "email": self.email,
            "phone_e164": self.phone_e164,
            "country": self.country,
            "name": self.name,
            "surname": self.surname,
            "occupation": self.occupation,
            "business": self.business,
            "avatar_url": self.avatar_url,
            "plan_code": self.plan_code.value,
            "status": self.status.value,
            "email_verified_at": (
                None
                if self.email_verified_at is None
                else format_utc_datetime(self.email_verified_at)
            ),
            "phone_verified_at": (
                None
                if self.phone_verified_at is None
                else format_utc_datetime(self.phone_verified_at)
            ),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
            "version": self.version,
        }


__all__ = [
    "AccountStatus",
    "Account",
]


if __name__ == "__main__":
    print("\n=== Running Account Models Self-Test ===\n")
    printer.status("TEST", "Account models module initialized", "info")

    account = Account.create_pending(
        auth_user_id="auth-1",
        username="Jean.Remy",
        email="Jean@example.com",
        phone_e164="+31612345678",
        country="nl",
        name="Jean",
        surname="Remy",
        created_at="2026-09-08T12:00:00Z",
    )
    assert account.status is AccountStatus.PENDING_VERIFICATION
    assert account.email == "jean@example.com"
    account = account.record_verification(
        email_verified=True,
        verified_at="2026-09-08T12:01:00Z",
    )
    assert account.status is AccountStatus.PENDING_VERIFICATION
    account = account.record_verification(
        phone_verified=True,
        verified_at="2026-09-08T12:02:00Z",
    )
    assert account.status is AccountStatus.ACTIVE
    assert account.can_authenticate is True
    assert account.version == 2

    printer.status("PASS", "Account lifecycle and verification invariants", "success")
    print("\n=== Test ran successfully ===\n")
