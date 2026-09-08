"""
Validated account-plan configuration loading for BIMAP.

Configuration files are translated into canonical domain models before they
reach bootstrap. The loader does not own entitlement persistence, account
state, reward balances, authentication, or commercial execution.
"""

from __future__ import annotations

import yaml

from decimal import Decimal
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from ..domain.accounts.plans import *
from ..domain.utils.domain_errors import DomainValidationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Plan Configuration")
printer = PrettyPrinter()

_DEFAULT_PATH = Path(__file__).with_name("configs/plans.yaml")
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


def load_account_plan_catalog(path: str | Path | None = None) -> AccountPlanCatalog:
    """Load and validate the configured BIMAP account plans."""

    _announce("Loading BIMAP account plan catalog")

    target = (
        _DEFAULT_PATH
        if path is None
        else Path(path)
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
        with target.open("r", encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise DomainValidationError("Account-plan configuration is not valid YAML.", field="plans.yaml") from exc

    root = _mapping(payload, field="configuration")
    schema_version = root.get("schema_version")

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
                "received": schema_version,
                "supported":
                    _SUPPORTED_SCHEMA_VERSION,
            },
        )

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
            field=f"plans.{code.value}.usage",
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
                mode=cast(QuotaMode, raw_quota.get("mode")),
                limit=raw_quota.get(
                    "limit"
                ),
                renewal=cast(RenewalCadence, raw_quota.get("renewal")),
            )

        plans.append(
            AccountPlan(
                code=code,
                display_name=cast(str, raw_plan.get("display_name")),
                base_purchase_discount_percent=cast(
                    Decimal,
                    raw_plan.get("base_purchase_discount_percent"),
                ),
                max_effective_purchase_discount_percent=cast(
                    Decimal,
                    raw_plan.get("max_effective_purchase_discount_percent"),
                ),
                quotas=quotas,
            )
        )

    catalog = AccountPlanCatalog(
        plans=tuple(plans)
    )

    logger.info(
        {
            "event":
                "account_plan_catalog_loaded",
            "plan_count":
                len(catalog.plans),
            "schema_version":
                schema_version,
        }
    )

    return catalog


__all__ = [
    "load_account_plan_catalog",
]