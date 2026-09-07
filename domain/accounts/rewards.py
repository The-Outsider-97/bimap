"""
BIMAP account reward policy and point-ledger domain values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .plans import UsageKind
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Accounts Rewards")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    printer.status("REWARDS", action, "info")
    logger.debug(action)


class RewardEventType(str, Enum):
    AUDIT_COMPLETED = "audit_completed"
    CONVERSION_COMPLETED = "conversion_completed"
    DATA_EXTRACTION_COMPLETED = (
        "data_extraction_completed"
    )
    PURCHASE_COMPLETED = "purchase_completed"


class RedemptionType(str, Enum):
    EXTRA_AUDIT = "extra_audit"
    EXTRA_CONVERSION = "extra_conversion"
    EXTRA_DATA_EXTRACTION = (
        "extra_data_extraction"
    )
    PURCHASE_DISCOUNT = "purchase_discount"


REDEMPTION_USAGE_KIND = {
    RedemptionType.EXTRA_AUDIT:
        UsageKind.AUDIT,
    RedemptionType.EXTRA_CONVERSION:
        UsageKind.CONVERSION,
    RedemptionType.EXTRA_DATA_EXTRACTION:
        UsageKind.DATA_EXTRACTION,
}


def require_points(
    value: Any,
    *,
    field: str = "points",
    allow_zero: bool = False,
) -> int:
    _announce("Validating points quantity")

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
    ):
        raise DomainValidationError(
            "Points must be an integer.",
            field=field,
        )

    minimum = 0 if allow_zero else 1

    if value < minimum:
        raise DomainValidationError(
            "Points quantity is below the permitted minimum.",
            field=field,
            context={
                "minimum": minimum,
                "received": value,
            },
        )

    return value


@dataclass(frozen=True, slots=True)
class PointsLedgerEntry:
    entry_id: str
    account_id: str
    points_delta: int
    reason: str
    source_id: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        _announce("Validating points ledger entry")

        object.__setattr__(
            self,
            "entry_id",
            require_text(
                self.entry_id,
                field="entry_id",
            ),
        )
        object.__setattr__(
            self,
            "account_id",
            require_text(
                self.account_id,
                field="account_id",
            ),
        )
        object.__setattr__(
            self,
            "reason",
            require_text(
                self.reason,
                field="reason",
            ),
        )
        object.__setattr__(
            self,
            "source_id",
            require_text(
                self.source_id,
                field="source_id",
            ),
        )
        object.__setattr__(
            self,
            "occurred_at",
            ensure_utc_datetime(
                self.occurred_at,
                field="occurred_at",
            ),
        )

        if (
            isinstance(self.points_delta, bool)
            or not isinstance(self.points_delta, int)
            or self.points_delta == 0
        ):
            raise DomainValidationError(
                "points_delta must be a non-zero integer.",
                field="points_delta",
            )


@dataclass(frozen=True, slots=True)
class RewardPolicy:
    audit_completed_points: int
    conversion_completed_points: int
    data_extraction_completed_points: int
    purchase_completed_points: int

    extra_audit_cost: int
    extra_conversion_cost: int
    extra_data_extraction_cost: int

    points_per_discount_percentage_point: int

    def __post_init__(self) -> None:
        _announce("Validating reward policy")

        for field in (
            "audit_completed_points",
            "conversion_completed_points",
            "data_extraction_completed_points",
            "purchase_completed_points",
            "extra_audit_cost",
            "extra_conversion_cost",
            "extra_data_extraction_cost",
            "points_per_discount_percentage_point",
        ):
            object.__setattr__(
                self,
                field,
                require_points(
                    getattr(self, field),
                    field=field,
                ),
            )

    def award_for(
        self,
        event: RewardEventType | str,
    ) -> int:
        _announce("Resolving reward award")

        event = (
            event
            if isinstance(event, RewardEventType)
            else RewardEventType(
                require_text(
                    event,
                    field="reward_event",
                ).lower()
            )
        )

        return {
            RewardEventType.AUDIT_COMPLETED:
                self.audit_completed_points,
            RewardEventType.CONVERSION_COMPLETED:
                self.conversion_completed_points,
            RewardEventType.DATA_EXTRACTION_COMPLETED:
                self.data_extraction_completed_points,
            RewardEventType.PURCHASE_COMPLETED:
                self.purchase_completed_points,
        }[event]

    def cost_for_extra_usage(
        self,
        kind: UsageKind | str,
    ) -> int:
        _announce("Resolving extra-usage reward cost")

        kind = UsageKind.parse(kind)

        return {
            UsageKind.AUDIT:
                self.extra_audit_cost,
            UsageKind.CONVERSION:
                self.extra_conversion_cost,
            UsageKind.DATA_EXTRACTION:
                self.extra_data_extraction_cost,
        }[kind]

    def discount_cost(
        self,
        percentage_points: int,
    ) -> int:
        _announce("Calculating discount redemption cost")

        if (
            isinstance(percentage_points, bool)
            or not isinstance(percentage_points, int)
            or percentage_points <= 0
        ):
            raise DomainValidationError(
                "Discount percentage points must be "
                "a positive integer.",
                field="percentage_points",
            )

        return (
            percentage_points
            * self.points_per_discount_percentage_point
        )


__all__ = [
    "RewardEventType",
    "RedemptionType",
    "REDEMPTION_USAGE_KIND",
    "PointsLedgerEntry",
    "RewardPolicy",
    "require_points",
]