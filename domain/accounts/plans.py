"""
Canonical BIMAP account-plan and recurring-entitlement models.

This module owns customer account-plan semantics only.

It must not define:
- audit product scope limits;
- prices of individual audit products;
- payment-provider behavior;
- persistence;
- authentication;
- points-ledger state.

Product/order scope remains under domain.products.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Accounts Plans")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    printer.status("ACCOUNTS", action, "info")
    logger.debug(action)


class AccountPlanCode(str, Enum):
    BASIC = "basic"
    PRO = "pro"
    PLUS = "plus"
    BUSINESS = "business"

    @classmethod
    def parse(cls, value: Any) -> "AccountPlanCode":
        _announce("Parsing account plan code")

        if isinstance(value, cls):
            return value

        normalized = require_text(
            value,
            field="plan_code",
        ).lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise DomainValidationError(
                "Unsupported BIMAP account plan.",
                field="plan_code",
                context={
                    "received": normalized,
                    "allowed": tuple(
                        item.value for item in cls
                    ),
                },
            ) from exc


class UsageKind(str, Enum):
    AUDIT = "audit"
    CONVERSION = "conversion"
    DATA_EXTRACTION = "data_extraction"

    @classmethod
    def parse(cls, value: Any) -> "UsageKind":
        _announce("Parsing usage kind")

        if isinstance(value, cls):
            return value

        normalized = require_text(
            value,
            field="usage_kind",
        ).lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise DomainValidationError(
                "Unsupported account usage kind.",
                field="usage_kind",
                context={
                    "received": normalized,
                    "allowed": tuple(
                        item.value for item in cls
                    ),
                },
            ) from exc


class RenewalCadence(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    NONE = "none"

    @classmethod
    def parse(cls, value: Any) -> "RenewalCadence":
        _announce("Parsing renewal cadence")

        if isinstance(value, cls):
            return value

        normalized = require_text(
            value,
            field="renewal",
        ).lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise DomainValidationError(
                "Unsupported entitlement renewal cadence.",
                field="renewal",
                context={"received": normalized},
            ) from exc


class QuotaMode(str, Enum):
    FINITE = "finite"
    UNLIMITED = "unlimited"

    @classmethod
    def parse(cls, value: Any) -> "QuotaMode":
        _announce("Parsing quota mode")

        if isinstance(value, cls):
            return value

        normalized = require_text(
            value,
            field="mode",
        ).lower()

        if normalized == "unconfigured":
            raise DomainValidationError(
                "Account-plan quota is not configured.",
                field="mode",
            )

        try:
            return cls(normalized)
        except ValueError as exc:
            raise DomainValidationError(
                "Unsupported quota mode.",
                field="mode",
                context={"received": normalized},
            ) from exc


def _percent(
    value: Any,
    *,
    field: str,
) -> Decimal:
    _announce(f"Normalizing {field}")

    if isinstance(value, bool):
        raise DomainValidationError(
            "Percentage cannot be boolean.",
            field=field,
        )

    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DomainValidationError(
            "Percentage must be numeric.",
            field=field,
        ) from exc

    if (
        not result.is_finite()
        or result < 0
        or result > 100
    ):
        raise DomainValidationError(
            "Percentage must be between 0 and 100.",
            field=field,
            context={"received": str(result)},
        )

    return result


@dataclass(frozen=True, slots=True)
class UsageQuota:
    kind: UsageKind
    mode: QuotaMode
    renewal: RenewalCadence
    limit: int | None = None

    def __post_init__(self) -> None:
        _announce("Validating usage quota")

        kind = UsageKind.parse(self.kind)
        mode = QuotaMode.parse(self.mode)
        renewal = RenewalCadence.parse(
            self.renewal
        )

        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self,
            "renewal",
            renewal,
        )

        if mode is QuotaMode.FINITE:
            if (
                isinstance(self.limit, bool)
                or not isinstance(self.limit, int)
                or self.limit <= 0
            ):
                raise DomainValidationError(
                    "Finite usage quota requires a "
                    "positive integer limit.",
                    field="limit",
                    context={
                        "usage_kind": kind.value,
                    },
                )

            if renewal is RenewalCadence.NONE:
                raise DomainInvariantError(
                    "Finite recurring quota requires "
                    "a renewal cadence.",
                    field="renewal",
                    context={
                        "usage_kind": kind.value,
                    },
                )

        else:
            if self.limit is not None:
                raise DomainInvariantError(
                    "Unlimited quota cannot define "
                    "a finite limit.",
                    field="limit",
                    context={
                        "usage_kind": kind.value,
                    },
                )

            if renewal is not RenewalCadence.NONE:
                raise DomainInvariantError(
                    "Unlimited quota does not require "
                    "a renewal window.",
                    field="renewal",
                    context={
                        "usage_kind": kind.value,
                    },
                )

    @property
    def unlimited(self) -> bool:
        return self.mode is QuotaMode.UNLIMITED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "mode": self.mode.value,
            "renewal": self.renewal.value,
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class AccountPlan:
    code: AccountPlanCode
    display_name: str
    base_purchase_discount_percent: Decimal
    max_effective_purchase_discount_percent: Decimal
    quotas: Mapping[UsageKind, UsageQuota]

    def __post_init__(self) -> None:
        _announce("Validating account plan")

        code = AccountPlanCode.parse(self.code)

        base_discount = _percent(
            self.base_purchase_discount_percent,
            field="base_purchase_discount_percent",
        )
        discount_cap = _percent(
            self.max_effective_purchase_discount_percent,
            field="max_effective_purchase_discount_percent",
        )

        if base_discount > discount_cap:
            raise DomainInvariantError(
                "Plan base purchase discount exceeds "
                "its effective discount cap.",
                field="base_purchase_discount_percent",
                context={
                    "plan_code": code.value,
                    "base_discount": str(base_discount),
                    "discount_cap": str(discount_cap),
                },
            )

        source = require_mapping(
            self.quotas,
            field="quotas",
        )

        normalized: dict[
            UsageKind,
            UsageQuota,
        ] = {}

        for raw_kind, quota in source.items():
            kind = UsageKind.parse(raw_kind)

            if not isinstance(quota, UsageQuota):
                raise DomainValidationError(
                    "quotas must contain UsageQuota objects.",
                    field=f"quotas.{kind.value}",
                    context={
                        "received_type":
                            type(quota).__name__,
                    },
                )

            if quota.kind is not kind:
                raise DomainInvariantError(
                    "Quota mapping key does not match "
                    "the quota's usage kind.",
                    field=f"quotas.{kind.value}",
                )

            normalized[kind] = quota

        missing = set(UsageKind) - set(normalized)

        if missing:
            raise DomainInvariantError(
                "Account plan does not configure every "
                "supported usage kind.",
                field="quotas",
                context={
                    "missing": tuple(
                        sorted(
                            item.value
                            for item in missing
                        )
                    ),
                },
            )

        object.__setattr__(self, "code", code)
        object.__setattr__(
            self,
            "display_name",
            require_text(
                self.display_name,
                field="display_name",
            ),
        )
        object.__setattr__(
            self,
            "base_purchase_discount_percent",
            base_discount,
        )
        object.__setattr__(
            self,
            "max_effective_purchase_discount_percent",
            discount_cap,
        )
        object.__setattr__(
            self,
            "quotas",
            MappingProxyType(normalized),
        )

    @property
    def max_reward_discount_percent(
        self,
    ) -> Decimal:
        return (
            self.max_effective_purchase_discount_percent
            - self.base_purchase_discount_percent
        )

    def quota_for(
        self,
        kind: UsageKind | str,
    ) -> UsageQuota:
        _announce("Resolving plan usage quota")
        return self.quotas[UsageKind.parse(kind)]

    def effective_purchase_discount(
        self,
        reward_discount_percent: Decimal | int | str = 0,
    ) -> Decimal:
        _announce("Calculating effective purchase discount")

        reward = _percent(
            reward_discount_percent,
            field="reward_discount_percent",
        )

        return min(
            self.base_purchase_discount_percent + reward,
            self.max_effective_purchase_discount_percent,
        )


@dataclass(frozen=True, slots=True)
class AccountPlanCatalog:
    plans: tuple[AccountPlan, ...]

    def __post_init__(self) -> None:
        _announce("Validating account plan catalog")

        seen: set[AccountPlanCode] = set()

        for plan in self.plans:
            if not isinstance(plan, AccountPlan):
                raise DomainValidationError(
                    "plans must contain AccountPlan objects.",
                    field="plans",
                )

            if plan.code in seen:
                raise DomainInvariantError(
                    "Duplicate account plan.",
                    field="plans",
                    context={
                        "plan_code": plan.code.value,
                    },
                )

            seen.add(plan.code)

        missing = set(AccountPlanCode) - seen

        if missing:
            raise DomainInvariantError(
                "Account plan catalog is incomplete.",
                field="plans",
                context={
                    "missing": tuple(
                        sorted(
                            item.value
                            for item in missing
                        )
                    ),
                },
            )

    def get(
        self,
        code: AccountPlanCode | str,
    ) -> AccountPlan:
        _announce("Looking up account plan")

        target = AccountPlanCode.parse(code)

        for plan in self.plans:
            if plan.code is target:
                return plan

        raise DomainValidationError(
            "Account plan is not configured.",
            field="plan_code",
            context={
                "plan_code": target.value,
            },
        )


__all__ = [
    "AccountPlanCode",
    "UsageKind",
    "RenewalCadence",
    "QuotaMode",
    "UsageQuota",
    "AccountPlan",
    "AccountPlanCatalog",
]