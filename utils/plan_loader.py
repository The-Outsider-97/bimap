"""
Validated account-plan and reward-policy configuration loading for BIMAP.

Configuration files are translated into canonical domain models before they
reach bootstrap. This module owns configuration parsing only; entitlement
consumption, reward balances, authentication and persistence remain application
or infrastructure concerns.
"""

from __future__ import annotations

import yaml

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from ..domain.accounts.plans import *
from ..domain.accounts.rewards import RewardPolicy
from ..domain.utils.domain_errors import DomainValidationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Plan Configuration")
printer = PrettyPrinter()

_DEFAULT_PATH = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "plans.yaml"
)

_SUPPORTED_SCHEMA_VERSION = 1


def _announce(action: str) -> None:
    printer.status("CONFIG", action, "info")
    logger.debug(action)


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(
            f"{field} must be a mapping.",
            field=field,
            context={
                "received_type":
                    type(value).__name__,
            },
        )

    return value


def _load_configuration(
    path: str | Path | None = None,
) -> Mapping[str, Any]:
    """Load and validate the shared plans/rewards configuration root."""

    _announce("Loading BIMAP commercial configuration")

    target = (
        _DEFAULT_PATH
        if path is None
        else Path(path).expanduser().resolve()
    )

    if not target.is_file():
        raise DomainValidationError(
            "Account-plan configuration file does not exist.",
            field="path",
            context={
                "path": str(target),
            },
        )

    try:
        with target.open(
            "r",
            encoding="utf-8",
        ) as stream:
            payload = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise DomainValidationError(
            "Account-plan configuration is not valid YAML.",
            field="plans.yaml",
        ) from exc
    except OSError as exc:
        raise DomainValidationError(
            "Account-plan configuration could not be read.",
            field="path",
            context={
                "path": str(target),
                "error_type":
                    type(exc).__name__,
            },
        ) from exc

    root = _mapping(
        payload,
        field="configuration",
    )

    schema_version = root.get(
        "schema_version"
    )

    if (
        isinstance(schema_version, bool)
        or not isinstance(
            schema_version,
            int,
        )
        or schema_version
        != _SUPPORTED_SCHEMA_VERSION
    ):
        raise DomainValidationError(
            "Unsupported account-plan configuration schema.",
            field="schema_version",
            context={
                "received":
                    schema_version,
                "supported":
                    _SUPPORTED_SCHEMA_VERSION,
            },
        )

    return root


def load_account_plan_catalog(
    path: str | Path | None = None,
) -> AccountPlanCatalog:
    """Load and validate the configured BIMAP account plans."""

    _announce("Loading BIMAP account plan catalog")

    root = _load_configuration(path)

    configured_plans = _mapping(
        root.get("plans"),
        field="plans",
    )

    plans: list[AccountPlan] = []

    for code in AccountPlanCode:
        raw_plan = _mapping(
            configured_plans.get(
                code.value
            ),
            field=f"plans.{code.value}",
        )

        usage = _mapping(
            raw_plan.get("usage"),
            field=(
                f"plans.{code.value}.usage"
            ),
        )

        quotas: dict[
            UsageKind,
            UsageQuota,
        ] = {}

        for kind in UsageKind:
            raw_quota = _mapping(
                usage.get(kind.value),
                field=(
                    f"plans.{code.value}"
                    f".usage.{kind.value}"
                ),
            )

            quotas[kind] = UsageQuota(
                kind=kind,
                mode=cast(
                    QuotaMode,
                    raw_quota.get("mode"),
                ),
                limit=raw_quota.get(
                    "limit"
                ),
                renewal=cast(
                    RenewalCadence,
                    raw_quota.get(
                        "renewal"
                    ),
                ),
            )

        plans.append(
            AccountPlan(
                code=code,
                display_name=cast(
                    str,
                    raw_plan.get(
                        "display_name"
                    ),
                ),
                base_purchase_discount_percent=cast(
                    Decimal,
                    raw_plan.get(
                        "base_purchase_discount_percent"
                    ),
                ),
                max_effective_purchase_discount_percent=cast(
                    Decimal,
                    raw_plan.get(
                        "max_effective_purchase_discount_percent"
                    ),
                ),
                quotas=quotas,
            )
        )

    catalog = AccountPlanCatalog(plans=tuple(plans))

    logger.info(
        {
            "event":
                "account_plan_catalog_loaded",
            "plan_count":
                len(catalog.plans),
            "schema_version":
                root["schema_version"],
        }
    )

    return catalog


def load_reward_policy(path: str | Path | None = None) -> RewardPolicy:
    """
    Load the configured BIMAP reward policy.

    RewardPolicy itself rejects absent, zero, negative or malformed point
    quantities, so an incomplete reward configuration fails closed.
    """

    _announce("Loading BIMAP reward policy")

    root = _load_configuration(path)

    rewards = _mapping(
        root.get("rewards"),
        field="rewards",
    )
    earn = _mapping(
        rewards.get("earn"),
        field="rewards.earn",
    )
    redeem = _mapping(
        rewards.get("redeem"),
        field="rewards.redeem",
    )
    purchase_discount = _mapping(rewards.get("purchase_discount"), field=("rewards.purchase_discount"))

    policy = RewardPolicy(
        audit_completed_points=cast(int, earn.get("audit_completed")),
        conversion_completed_points=cast(int, earn.get("conversion_completed")),
        data_extraction_completed_points=cast(int, earn.get("data_extraction_completed")),
        purchase_completed_points=cast(int, earn.get("purchase_completed")),
        extra_audit_cost=cast(int, redeem.get("extra_audit")),
        extra_conversion_cost=cast(int, redeem.get("extra_conversion")),
        extra_data_extraction_cost=cast(int, redeem.get("extra_data_extraction")),
        points_per_discount_percentage_point=cast(int, purchase_discount.get("points_per_percentage_point")))

    logger.info(
        {
            "event":
                "reward_policy_loaded",
            "schema_version":
                root["schema_version"],
        }
    )

    return policy


__all__ = [
    "load_account_plan_catalog",
    "load_reward_policy",
]