"""
Application service for BIMAP recurring account entitlements.

Purpose
-------
``EntitlementService`` is the Level-5 application owner of commercial usage
admission for account-backed BIMAP capabilities:

- audits;
- model conversions; and
- data extractions.

It coordinates already-defined account-plan policy with deployment-owned
persistence and renewal-window policy.

The service deliberately does not own:

- audit-engine product scope limits;
- product pricing;
- payment settlement;
- authentication;
- account creation;
- points earning/redemption policy;
- bonus-credit creation;
- persistence implementation;
- database transactions;
- HTTP/API behavior;
- order lifecycle transitions; or
- SLAI behavior.

Those concerns remain owned by their existing architectural layers.

Consumption precedence
----------------------
For a finite quota, one entitlement request is resolved in this exact order::

    recurring plan quota
        ↓
    redeemed bonus credit
        ↓
    quota exhausted

For an unlimited quota::

    unlimited plan entitlement

Recurring quota is always consumed before a bonus credit. Redeemed credits
therefore supplement the plan allowance rather than replacing unused recurring
capacity.

Idempotency
-----------
Every entitlement consumption has two independent identities:

``source_id``
    Stable identity of the business operation being admitted. For audit orders
    this is ``Order.order_id``. Persistence MUST guarantee that one
    ``(account_id, usage_kind, source_id)`` cannot consume more than one
    entitlement even when a caller retries with a different idempotency key.

``idempotency_key``
    Stable identity of one request attempt. Persistence MUST guarantee that a
    key cannot be rebound to another source operation.

The service checks both identities before mutation. These reads improve replay
behavior and diagnostics, but they are not sufficient for concurrency safety.
The persistence implementation MUST repeat those guarantees atomically during
the write operation.

Concurrency
-----------
Quota enforcement must never use the unsafe pattern::

    count current usage
    if count < limit:
        insert usage event

Two concurrent requests could both observe capacity and oversubscribe a quota.

Instead, ``try_consume_recurring()`` is required to atomically:

1. resolve/replay an existing source/idempotency record;
2. check consumption within the supplied renewal window;
3. compare usage with the configured limit; and
4. persist exactly one consumption if capacity remains.

Likewise, ``try_consume_bonus()`` must atomically decrement exactly one
available redeemed bonus credit or return ``None``.

Renewal policy
--------------
The domain defines whether a quota renews WEEKLY, MONTHLY, or not at all.
It does not currently define whether those windows are:

- calendar aligned;
- ISO-week aligned;
- subscription-anniversary aligned; or
- another configured contractual interpretation.

``EntitlementService`` therefore does not invent that policy. A deployment-owned
``renewal_window_resolver`` is injected and must return the authoritative
window for finite recurring quotas.

This keeps "weekly" and "monthly" semantics deterministic without silently
introducing a commercial rule absent from the current BIMAP policy.

Dependency direction
--------------------
domain.accounts
       ↑
app.ports / structural persistence boundary
       ↑
app/services/entitlement_service.py
       ↑
app/commands/grant_entitlement.py
       ↑
api / composition / workers

SLAI has no role in commercial entitlement decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from ..ports.clock import Clock
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.accounts.plans import *
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Entitlement Service")
printer = PrettyPrinter()

_COMPONENT = "entitlement_service"

_MAX_IDEMPOTENCY_KEY_LENGTH = 512
_MAX_SOURCE_ID_LENGTH = 512


# ---------------------------------------------------------------------------
# Result / policy values
# ---------------------------------------------------------------------------


class EntitlementSource(str, Enum):
    """
    Authoritative source from which one usage entitlement was consumed.

    ``RECURRING_QUOTA``
        One unit from the active plan's current recurring allowance.

    ``BONUS_CREDIT``
        One previously redeemed extra-use credit. Bonus credits are used only
        after the current recurring allowance is exhausted.

    ``UNLIMITED``
        Usage admitted by a plan whose quota for the requested usage kind is
        explicitly unlimited.
    """

    RECURRING_QUOTA = "recurring_quota"
    BONUS_CREDIT = "bonus_credit"
    UNLIMITED = "unlimited"

    @classmethod
    def parse(cls, value: Any, *, field: str = "source") -> "EntitlementSource":
        """Normalize a persistence/service value into EntitlementSource."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Parsing entitlement source",
            event="entitlement_source_parse_start",
        )

        if isinstance(value, cls):
            return value

        normalized = require_app_text(
            value,
            field=field,
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="parse_entitlement_source",
            max_length=128,
        ).lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            raise AppIntegrityError(
                "Entitlement persistence returned an unsupported "
                "consumption source.",
                component=_COMPONENT,
                operation="parse_entitlement_source",
                field=field,
                context={
                    "received": normalized,
                    "allowed": tuple(
                        item.value
                        for item in cls
                    ),
                },
                cause=exc,
            ) from exc


@dataclass(frozen=True, slots=True)
class EntitlementWindow:
    """
    One authoritative half-open recurring quota window.

    The interval is represented as::

        start <= timestamp < end

    Half-open intervals avoid double-counting an event exactly on the boundary
    between two consecutive renewal periods.
    """

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating entitlement renewal window",
            event="entitlement_window_validate_start",
        )

        start = ensure_app_utc_datetime(
            self.start,
            field="start",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_entitlement_window",
        )
        end = ensure_app_utc_datetime(
            self.end,
            field="end",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_entitlement_window",
        )

        if end <= start:
            raise AppIntegrityError(
                "Entitlement renewal window must have a positive duration.",
                component=_COMPONENT,
                operation="validate_entitlement_window",
                field="end",
                context={
                    "start":
                        format_app_utc_datetime(
                            start,
                            field="start",
                        ),
                    "end":
                        format_app_utc_datetime(
                            end,
                            field="end",
                        ),
                },
            )

        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def contains(self, value: datetime | str) -> bool:
        """Return whether a timestamp lies inside this half-open window."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Checking entitlement renewal window",
            event="entitlement_window_contains_start",
        )

        timestamp = ensure_app_utc_datetime(
            value,
            field="timestamp",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="entitlement_window_contains",
        )

        return (
            self.start
            <= timestamp
            < self.end
        )

    def to_dict(self) -> dict[str, str]:
        """Serialize the renewal window to deterministic UTC values."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing entitlement renewal window",
            event="entitlement_window_to_dict_start",
        )

        return {
            "start":
                format_app_utc_datetime(self.start, field="start"),
            "end":
                format_app_utc_datetime(self.end, field="end"),
        }


@dataclass(frozen=True, slots=True)
class EntitlementConsumption:
    """
    Canonical application result for one admitted usage operation.

    This is a projection of the authoritative persisted consumption record. It
    does not contain a mutable quota balance. Account-summary queries may derive
    balances separately from authoritative usage/bonus-credit persistence.
    """

    account_id: str
    kind: UsageKind
    source_id: str
    idempotency_key: str

    source: EntitlementSource
    plan_code: AccountPlanCode
    occurred_at: datetime

    period_start: datetime | None = None
    period_end: datetime | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating entitlement consumption",
            event="entitlement_consumption_validate_start",
            context={
                "source_id":
                    getattr(
                        self,
                        "source_id",
                        None,
                    ),
            },
        )

        account_id = require_app_text(
            self.account_id,
            field="account_id",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_consumption",
            max_length=512,
        )

        source_id = require_app_text(
            self.source_id,
            field="source_id",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_consumption",
            max_length=_MAX_SOURCE_ID_LENGTH,
        )

        idempotency_key = require_app_text(
            self.idempotency_key,
            field="idempotency_key",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_consumption",
            max_length=_MAX_IDEMPOTENCY_KEY_LENGTH,
        )

        try:
            kind = UsageKind.parse(self.kind)
            plan_code = AccountPlanCode.parse(self.plan_code)
        except DomainError as exc:
            raise AppIntegrityError(
                "Persisted entitlement consumption contains "
                "invalid account-plan data.",
                component=_COMPONENT,
                operation="validate_consumption",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        source = EntitlementSource.parse(
            self.source
        )

        occurred_at = ensure_app_utc_datetime(
            self.occurred_at,
            field="occurred_at",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="validate_consumption",
        )

        period_start = (
            None
            if self.period_start is None
            else ensure_app_utc_datetime(
                self.period_start,
                field="period_start",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_consumption",
            )
        )

        period_end = (
            None
            if self.period_end is None
            else ensure_app_utc_datetime(
                self.period_end,
                field="period_end",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="validate_consumption",
            )
        )

        if source is EntitlementSource.RECURRING_QUOTA:
            if (
                period_start is None
                or period_end is None
            ):
                raise AppIntegrityError(
                    "Recurring entitlement consumption requires "
                    "its authoritative renewal window.",
                    component=_COMPONENT,
                    operation="validate_consumption",
                    field="period_start",
                    context={
                        "source":
                            source.value,
                    },
                )

            if period_end <= period_start:
                raise AppIntegrityError(
                    "Recurring entitlement consumption has "
                    "an invalid renewal window.",
                    component=_COMPONENT,
                    operation="validate_consumption",
                    field="period_end",
                )

            if not (
                period_start
                <= occurred_at
                < period_end
            ):
                raise AppIntegrityError(
                    "Recurring entitlement consumption timestamp "
                    "lies outside its persisted renewal window.",
                    component=_COMPONENT,
                    operation="validate_consumption",
                    field="occurred_at",
                    context={
                        "source":
                            source.value,
                    },
                )

        else:
            if (
                period_start is not None
                or period_end is not None
            ):
                raise AppIntegrityError(
                    "Only recurring-quota consumption may carry "
                    "a renewal-period window.",
                    component=_COMPONENT,
                    operation="validate_consumption",
                    field="period_start",
                    context={
                        "source":
                            source.value,
                    },
                )

        object.__setattr__(
            self,
            "account_id",
            account_id,
        )
        object.__setattr__(
            self,
            "kind",
            kind,
        )
        object.__setattr__(
            self,
            "source_id",
            source_id,
        )
        object.__setattr__(
            self,
            "idempotency_key",
            idempotency_key,
        )
        object.__setattr__(
            self,
            "source",
            source,
        )
        object.__setattr__(
            self,
            "plan_code",
            plan_code,
        )
        object.__setattr__(
            self,
            "occurred_at",
            occurred_at,
        )
        object.__setattr__(
            self,
            "period_start",
            period_start,
        )
        object.__setattr__(
            self,
            "period_end",
            period_end,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this consumption into deterministic JSON-ready data."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing entitlement consumption",
            event="entitlement_consumption_to_dict_start",
            context={
                "source_id":
                    self.source_id,
            },
        )

        return {
            "account_id":
                self.account_id,
            "kind":
                self.kind.value,
            "source_id":
                self.source_id,
            "idempotency_key":
                self.idempotency_key,
            "source":
                self.source.value,
            "plan_code":
                self.plan_code.value,
            "occurred_at":
                format_app_utc_datetime(
                    self.occurred_at,
                    field="occurred_at",
                ),
            "period_start": (
                None
                if self.period_start is None
                else format_app_utc_datetime(
                    self.period_start,
                    field="period_start",
                )
            ),
            "period_end": (
                None
                if self.period_end is None
                else format_app_utc_datetime(
                    self.period_end,
                    field="period_end",
                )
            ),
        }


# ---------------------------------------------------------------------------
# Structural application dependencies
# ---------------------------------------------------------------------------


class _AccountPlanResolver(Protocol):
    """
    Resolve the active account plan code.

    Absence/suspension of an account plan should be expressed through an
    explicit ``AppError`` rather than by returning an invented fallback plan.
    """

    def resolve_plan_code(
        self,
        account_id: str,
    ) -> AccountPlanCode | str:
        ...


class _RenewalWindowResolver(Protocol):
    """
    Resolve one authoritative finite recurring quota window.

    Implementations may use subscription anchors, calendar policy, or another
    configured contractual rule. This service intentionally does not choose.
    """

    def resolve(
        self,
        *,
        account_id: str,
        plan: AccountPlan,
        quota: UsageQuota,
        at: datetime,
    ) -> EntitlementWindow:
        ...


class _EntitlementStore(Protocol):
    """
    Persistence boundary required by EntitlementService.

    The methods below are deliberately operation-oriented because the required
    compare-and-consume behavior must be atomic. Exposing only generic
    ``count()`` and ``save()`` methods would permit race-prone application code.

    A future ``app/ports/entitlements.py`` may formalize this exact contract
    without changing EntitlementService behavior.
    """

    def find_by_source(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        source_id: str,
    ) -> Any | None:
        ...

    def find_by_idempotency_key(
        self,
        *,
        account_id: str,
        idempotency_key: str,
    ) -> Any | None:
        ...

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
    ) -> Any | None:
        """
        Atomically consume recurring capacity.

        Return:
        - persisted/replayed consumption when successful;
        - ``None`` only when recurring capacity is exhausted.
        """
        ...

    def try_consume_bonus(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        plan_code: AccountPlanCode,
        source_id: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> Any | None:
        """
        Atomically consume one redeemed bonus credit.

        Return:
        - persisted/replayed consumption when successful;
        - ``None`` only when no bonus credit is available.
        """
        ...

    def record_unlimited(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        plan_code: AccountPlanCode,
        source_id: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> Any:
        """Persist or idempotently replay one unlimited-plan usage event."""
        ...


# ---------------------------------------------------------------------------
# Dependency validation
# ---------------------------------------------------------------------------


def _require_methods(
    value: Any,
    *,
    field: str,
    methods: tuple[str, ...],
) -> Any:
    """Require a structural dependency with the specified callable methods."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Validating entitlement dependency: {field}",
        event="entitlement_dependency_validate_start",
        context={
            "field":
                field,
        },
    )

    missing = tuple(
        name
        for name in methods
        if not callable(
            getattr(
                value,
                name,
                None,
            )
        )
    )

    if missing:
        raise AppConfigurationError(
            "Entitlement dependency does not implement "
            "the required application contract.",
            component=_COMPONENT,
            operation="initialize",
            field=field,
            context={
                "received_type":
                    type(value).__name__,
                "missing_methods":
                    missing,
            },
        )

    return value


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_usage_kind(
    value: UsageKind | str,
) -> UsageKind:
    """Translate account-domain usage validation into application errors."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing entitlement usage kind",
        event="entitlement_usage_kind_normalize_start",
    )

    try:
        return UsageKind.parse(
            value
        )
    except DomainError as exc:
        raise AppValidationError(
            "Unsupported entitlement usage kind.",
            component=_COMPONENT,
            operation="consume",
            field="kind",
            context=lower_error_context(exc),
            cause=exc,
        ) from exc


def _record_field(
    record: Any,
    name: str,
) -> Any:
    """Read one required persistence-result field from mapping/object data."""
    if isinstance(record, Mapping):
        if name not in record:
            raise AppIntegrityError(
                "Entitlement persistence result is missing "
                "a required field.",
                component=_COMPONENT,
                operation="normalize_persisted_consumption",
                field=name,
                context={
                    "result_type":
                        type(record).__name__,
                },
            )

        return record[name]

    if not hasattr(record, name):
        raise AppIntegrityError(
            "Entitlement persistence result is missing "
            "a required attribute.",
            component=_COMPONENT,
            operation="normalize_persisted_consumption",
            field=name,
            context={
                "result_type":
                    type(record).__name__,
            },
        )

    return getattr(
        record,
        name,
    )


def _optional_record_field(
    record: Any,
    name: str,
) -> Any:
    """Read one optional persistence-result field."""
    if isinstance(record, Mapping):
        return record.get(name)

    return getattr(
        record,
        name,
        None,
    )


def _normalize_persisted_consumption(
    record: Any,
) -> EntitlementConsumption:
    """
    Normalize one persistence result into the canonical application projection.

    Concrete adapters are free to return their own record type or a mapping.
    The service does not leak either representation upward.
    """
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing persisted entitlement consumption",
        event="entitlement_persisted_consumption_normalize_start",
    )

    if isinstance(
        record,
        EntitlementConsumption,
    ):
        return record

    if record is None:
        raise AppIntegrityError(
            "Entitlement persistence returned no record "
            "for a successful operation.",
            component=_COMPONENT,
            operation="normalize_persisted_consumption",
            field="result",
        )

    return EntitlementConsumption(
        account_id=_record_field(
            record,
            "account_id",
        ),
        kind=_record_field(
            record,
            "kind",
        ),
        source_id=_record_field(
            record,
            "source_id",
        ),
        idempotency_key=_record_field(
            record,
            "idempotency_key",
        ),
        source=_record_field(
            record,
            "source",
        ),
        plan_code=_record_field(
            record,
            "plan_code",
        ),
        occurred_at=_record_field(
            record,
            "occurred_at",
        ),
        period_start=_optional_record_field(
            record,
            "period_start",
        ),
        period_end=_optional_record_field(
            record,
            "period_end",
        ),
    )


def _assert_consumption_source_binding(
    consumption: EntitlementConsumption,
    *,
    account_id: str,
    kind: UsageKind,
    source_id: str,
    operation: str,
) -> None:
    """Require a persisted result to belong to the requested business source."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating entitlement source binding",
        event="entitlement_source_binding_validate_start",
        context={
            "operation":
                operation,
            "source_id":
                source_id,
        },
    )

    if (
        consumption.account_id
        != account_id
    ):
        raise AppIntegrityError(
            "Entitlement persistence changed account identity.",
            component=_COMPONENT,
            operation=operation,
            field="result.account_id",
            context={
                "expected_account_id":
                    account_id,
                "returned_account_id":
                    consumption.account_id,
            },
        )

    if consumption.kind is not kind:
        raise AppIntegrityError(
            "Entitlement persistence changed usage kind.",
            component=_COMPONENT,
            operation=operation,
            field="result.kind",
            context={
                "expected_kind":
                    kind.value,
                "returned_kind":
                    consumption.kind.value,
            },
        )

    if (
        consumption.source_id
        != source_id
    ):
        raise AppIntegrityError(
            "Entitlement persistence changed source identity.",
            component=_COMPONENT,
            operation=operation,
            field="result.source_id",
            context={
                "expected_source_id":
                    source_id,
                "returned_source_id":
                    consumption.source_id,
            },
        )


# ---------------------------------------------------------------------------
# Entitlement Service
# ---------------------------------------------------------------------------


class EntitlementService:
    """
    Coordinate deterministic BIMAP account-entitlement consumption.

    ``EntitlementService`` is deliberately narrower than an account service.
    It does not own profile data, subscriptions, points balances, purchases, or
    account-summary rendering.

    The service requires:

    ``store``
        Atomic usage/bonus-credit persistence.

    ``clock``
        Authoritative current UTC time.

    ``catalog``
        Canonical configured AccountPlan objects.

    ``plan_resolver``
        Deployment/account subsystem capable of resolving the active plan code
        for one account.

    ``renewal_window_resolver``
        Deterministic commercial policy for translating WEEKLY/MONTHLY cadence
        into concrete UTC boundaries.
    """

    __slots__ = (
        "_store",
        "_clock",
        "_catalog",
        "_plan_resolver",
        "_renewal_window_resolver",
    )

    def __init__(
        self,
        store: _EntitlementStore,
        clock: Clock,
        *,
        catalog: AccountPlanCatalog,
        plan_resolver: _AccountPlanResolver,
        renewal_window_resolver: _RenewalWindowResolver,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing entitlement service",
            event="entitlement_service_init_start",
        )

        self._store = _require_methods(
            store,
            field="store",
            methods=(
                "find_by_source",
                "find_by_idempotency_key",
                "try_consume_recurring",
                "try_consume_bonus",
                "record_unlimited",
            ),
        )

        if not isinstance(
            clock,
            Clock,
        ):
            raise AppConfigurationError(
                "clock must implement the BIMAP Clock port.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
                context={
                    "received_type":
                        type(clock).__name__,
                },
            )

        if not isinstance(
            catalog,
            AccountPlanCatalog,
        ):
            raise AppConfigurationError(
                "catalog must be an AccountPlanCatalog.",
                component=_COMPONENT,
                operation="initialize",
                field="catalog",
                context={
                    "received_type":
                        type(catalog).__name__,
                },
            )

        self._plan_resolver = _require_methods(
            plan_resolver,
            field="plan_resolver",
            methods=(
                "resolve_plan_code",
            ),
        )

        self._renewal_window_resolver = _require_methods(
            renewal_window_resolver,
            field="renewal_window_resolver",
            methods=(
                "resolve",
            ),
        )

        self._clock = clock
        self._catalog = catalog

        logger.info(
            {
                "event":
                    "entitlement_service_initialized",
                "configured_plan_count":
                    len(catalog.plans),
                "store_type":
                    type(store).__name__,
                "plan_resolver_type":
                    type(
                        plan_resolver
                    ).__name__,
                "renewal_window_resolver_type":
                    type(
                        renewal_window_resolver
                    ).__name__,
            }
        )

    def _resolve_plan(
        self,
        account_id: str,
    ) -> AccountPlan:
        """Resolve and validate the account's current configured plan."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving account entitlement plan",
            event="entitlement_plan_resolve_start",
            context={
                "account_id":
                    account_id,
            },
        )

        try:
            raw_plan_code = (
                self._plan_resolver
                .resolve_plan_code(
                    account_id
                )
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Account plan resolver failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="resolve_plan",
                context={
                    "account_id":
                        account_id,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        try:
            plan_code = AccountPlanCode.parse(
                raw_plan_code
            )
            plan = self._catalog.get(
                plan_code
            )
        except DomainError as exc:
            raise AppIntegrityError(
                "Account plan resolver returned a plan "
                "that is not valid in the configured catalog.",
                component=_COMPONENT,
                operation="resolve_plan",
                field="plan_code",
                context={
                    "account_id":
                        account_id,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        return plan

    def _resolve_window(
        self,
        *,
        account_id: str,
        plan: AccountPlan,
        quota: UsageQuota,
        at: datetime,
    ) -> EntitlementWindow:
        """Resolve and verify one finite recurring entitlement window."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving entitlement renewal window",
            event="entitlement_window_resolve_start",
            context={
                "account_id":
                    account_id,
                "plan_code":
                    plan.code.value,
                "usage_kind":
                    quota.kind.value,
                "renewal":
                    quota.renewal.value,
            },
        )

        if quota.mode is not QuotaMode.FINITE:
            raise AppIntegrityError(
                "Renewal window was requested for a non-finite quota.",
                component=_COMPONENT,
                operation="resolve_window",
                field="quota.mode",
                context={
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        quota.kind.value,
                    "mode":
                        quota.mode.value,
                },
            )

        if quota.renewal is RenewalCadence.NONE:
            raise AppIntegrityError(
                "Finite quota has no renewal cadence.",
                component=_COMPONENT,
                operation="resolve_window",
                field="quota.renewal",
                context={
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        quota.kind.value,
                },
            )

        try:
            window = (
                self._renewal_window_resolver
                .resolve(
                    account_id=account_id,
                    plan=plan,
                    quota=quota,
                    at=at,
                )
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Renewal-window resolver failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="resolve_window",
                context={
                    "account_id":
                        account_id,
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        quota.kind.value,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        if not isinstance(
            window,
            EntitlementWindow,
        ):
            raise AppIntegrityError(
                "Renewal-window resolver returned an unsupported result.",
                component=_COMPONENT,
                operation="resolve_window",
                field="result",
                context={
                    "received_type":
                        type(window).__name__,
                },
            )

        if not window.contains(at):
            raise AppIntegrityError(
                "Resolved entitlement renewal window does not contain "
                "the authoritative current time.",
                component=_COMPONENT,
                operation="resolve_window",
                field="result",
                context={
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        quota.kind.value,
                    "renewal":
                        quota.renewal.value,
                },
            )

        return window

    def _find_existing(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        source_id: str,
        idempotency_key: str,
    ) -> EntitlementConsumption | None:
        """
        Resolve an idempotent replay before performing commercial mutation.

        The request key is checked first. Reusing a key for another source is
        rejected even if the supplied source itself already has a consumption
        record.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Checking existing entitlement consumption",
            event="entitlement_existing_lookup_start",
            context={
                "account_id":
                    account_id,
                "usage_kind":
                    kind.value,
                "source_id":
                    source_id,
            },
        )

        try:
            by_key = (
                self._store
                .find_by_idempotency_key(
                    account_id=account_id,
                    idempotency_key=(
                        idempotency_key
                    ),
                )
            )

            if by_key is not None:
                consumption = (
                    _normalize_persisted_consumption(
                        by_key
                    )
                )

                if (
                    consumption.account_id
                    != account_id
                ):
                    raise AppIntegrityError(
                        "Entitlement idempotency lookup returned "
                        "another account's consumption.",
                        component=_COMPONENT,
                        operation="find_existing",
                        field="result.account_id",
                    )

                if (
                    consumption.idempotency_key
                    != idempotency_key
                ):
                    raise AppIntegrityError(
                        "Entitlement idempotency lookup returned "
                        "a mismatched idempotency key.",
                        component=_COMPONENT,
                        operation="find_existing",
                        field="result.idempotency_key",
                    )

                if (
                    consumption.kind is not kind
                    or consumption.source_id
                    != source_id
                ):
                    raise AppValidationError(
                        "Idempotency key is already bound to "
                        "another entitlement consumption.",
                        component=_COMPONENT,
                        operation="consume",
                        field="idempotency_key",
                        context={
                            "existing_kind":
                                consumption.kind.value,
                            "requested_kind":
                                kind.value,
                            "existing_source_id":
                                consumption.source_id,
                            "requested_source_id":
                                source_id,
                        },
                    )

                return consumption

            by_source = (
                self._store
                .find_by_source(
                    account_id=account_id,
                    kind=kind,
                    source_id=source_id,
                )
            )

        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Entitlement persistence lookup failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="find_existing",
                context={
                    "account_id":
                        account_id,
                    "usage_kind":
                        kind.value,
                    "source_id":
                        source_id,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        if by_source is None:
            return None

        consumption = (
            _normalize_persisted_consumption(
                by_source
            )
        )

        _assert_consumption_source_binding(
            consumption,
            account_id=account_id,
            kind=kind,
            source_id=source_id,
            operation="find_existing",
        )

        # A different request key is intentionally permitted here. The stable
        # business source has already consumed exactly one entitlement, so this
        # is a safe source-level replay rather than another charge.
        return consumption

    def _record_unlimited(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        source_id: str,
        idempotency_key: str,
        plan: AccountPlan,
        occurred_at: datetime,
    ) -> EntitlementConsumption:
        """Persist one usage event admitted by an unlimited plan quota."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Recording unlimited entitlement usage",
            event="entitlement_unlimited_record_start",
            context={
                "account_id":
                    account_id,
                "usage_kind":
                    kind.value,
                "plan_code":
                    plan.code.value,
                "source_id":
                    source_id,
            },
        )

        try:
            stored = (
                self._store
                .record_unlimited(
                    account_id=account_id,
                    kind=kind,
                    plan_code=plan.code,
                    source_id=source_id,
                    idempotency_key=(
                        idempotency_key
                    ),
                    occurred_at=occurred_at,
                )
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Unlimited entitlement persistence failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="record_unlimited",
                context={
                    "account_id":
                        account_id,
                    "usage_kind":
                        kind.value,
                    "plan_code":
                        plan.code.value,
                    "source_id":
                        source_id,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        result = (
            _normalize_persisted_consumption(
                stored
            )
        )

        _assert_consumption_source_binding(
            result,
            account_id=account_id,
            kind=kind,
            source_id=source_id,
            operation="record_unlimited",
        )

        if (
            result.source
            is not EntitlementSource.UNLIMITED
        ):
            # A source-level replay may theoretically have been inserted by
            # another concurrent request before the write. Such a record is
            # acceptable only if it is the same business source. The earlier
            # identity validation establishes that condition.
            logger.info(
                {
                    "event":
                        "entitlement_unlimited_source_replayed",
                    "source_id":
                        source_id,
                    "persisted_source":
                        result.source.value,
                }
            )

        return result

    def _consume_finite(
        self,
        *,
        account_id: str,
        kind: UsageKind,
        source_id: str,
        idempotency_key: str,
        plan: AccountPlan,
        quota: UsageQuota,
        occurred_at: datetime,
    ) -> EntitlementConsumption:
        """
        Consume recurring quota first, then one bonus credit if necessary.

        All capacity checks are delegated to atomic store operations.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Consuming finite entitlement",
            event="entitlement_finite_consume_start",
            context={
                "account_id":
                    account_id,
                "usage_kind":
                    kind.value,
                "plan_code":
                    plan.code.value,
                "source_id":
                    source_id,
            },
        )

        if (
            quota.mode
            is not QuotaMode.FINITE
        ):
            raise AppIntegrityError(
                "Finite entitlement path received a non-finite quota.",
                component=_COMPONENT,
                operation="consume_finite",
                field="quota.mode",
                context={
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        kind.value,
                    "mode":
                        quota.mode.value,
                },
            )

        if quota.limit is None:
            raise AppIntegrityError(
                "Finite entitlement quota has no configured limit.",
                component=_COMPONENT,
                operation="consume_finite",
                field="quota.limit",
                context={
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        kind.value,
                },
            )

        window = self._resolve_window(
            account_id=account_id,
            plan=plan,
            quota=quota,
            at=occurred_at,
        )

        try:
            recurring = (
                self._store
                .try_consume_recurring(
                    account_id=account_id,
                    kind=kind,
                    plan_code=plan.code,
                    source_id=source_id,
                    idempotency_key=(
                        idempotency_key
                    ),
                    occurred_at=occurred_at,
                    period_start=window.start,
                    period_end=window.end,
                    limit=quota.limit,
                )
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Recurring entitlement persistence failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="consume_recurring",
                context={
                    "account_id":
                        account_id,
                    "usage_kind":
                        kind.value,
                    "plan_code":
                        plan.code.value,
                    "source_id":
                        source_id,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        if recurring is not None:
            result = (
                _normalize_persisted_consumption(
                    recurring
                )
            )

            _assert_consumption_source_binding(
                result,
                account_id=account_id,
                kind=kind,
                source_id=source_id,
                operation="consume_recurring",
            )

            return result

        # Recurring capacity has been atomically confirmed exhausted.
        # Only now may a redeemed bonus credit be consumed.
        try:
            bonus = (
                self._store
                .try_consume_bonus(
                    account_id=account_id,
                    kind=kind,
                    plan_code=plan.code,
                    source_id=source_id,
                    idempotency_key=(
                        idempotency_key
                    ),
                    occurred_at=occurred_at,
                )
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Bonus-credit entitlement persistence failed outside "
                "the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="consume_bonus",
                context={
                    "account_id":
                        account_id,
                    "usage_kind":
                        kind.value,
                    "plan_code":
                        plan.code.value,
                    "source_id":
                        source_id,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        if bonus is not None:
            result = (
                _normalize_persisted_consumption(
                    bonus
                )
            )

            _assert_consumption_source_binding(
                result,
                account_id=account_id,
                kind=kind,
                source_id=source_id,
                operation="consume_bonus",
            )

            return result

        logger.info(
            {
                "event":
                    "entitlement_quota_exhausted",
                "account_id":
                    account_id,
                "usage_kind":
                    kind.value,
                "plan_code":
                    plan.code.value,
                "renewal":
                    quota.renewal.value,
                "configured_limit":
                    quota.limit,
            }
        )

        raise AppValidationError(
            "Account usage entitlement is exhausted "
            "for the current renewal period and no "
            "bonus credit is available.",
            component=_COMPONENT,
            operation="consume",
            field="entitlement",
            context={
                "plan_code":
                    plan.code.value,
                "usage_kind":
                    kind.value,
                "renewal":
                    quota.renewal.value,
                "configured_limit":
                    quota.limit,
                "period_start":
                    format_app_utc_datetime(
                        window.start,
                        field="period_start",
                    ),
                "period_end":
                    format_app_utc_datetime(
                        window.end,
                        field="period_end",
                    ),
            },
        )

    def consume(
        self,
        *,
        account_id: str,
        kind: UsageKind | str,
        source_id: str,
        idempotency_key: str,
    ) -> EntitlementConsumption:
        """
        Consume or idempotently resolve one BIMAP account entitlement.

        Algorithm
        ---------
        1. validate request identities;
        2. resolve prior source/request replay;
        3. resolve the account's current plan;
        4. resolve the configured quota for the requested usage kind;
        5. read authoritative current time;
        6. if unlimited, record the usage without decrementing capacity;
        7. if finite, atomically try recurring allowance;
        8. only when recurring capacity is exhausted, atomically try one
           redeemed bonus credit;
        9. reject the request if neither source is available.

        No points are awarded by this method. Points belong to successful
        completion events, not entitlement admission.

        Parameters
        ----------
        account_id:
            Account that owns the requested capability.

        kind:
            ``audit``, ``conversion``, or ``data_extraction``.

        source_id:
            Stable business identity of the operation. For audit orders this
            must be the authoritative ``Order.order_id``.

        idempotency_key:
            Request-level idempotency identity.

        Returns
        -------
        EntitlementConsumption
            Canonical persisted entitlement consumption.

        Notes
        -----
        The current plan is intentionally resolved only after replay lookup.
        Therefore a retry of a previously admitted operation remains stable even
        if the account's plan changed after the original consumption.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Consuming account entitlement",
            event="entitlement_service_consume_start",
            context={
                "source_id":
                    source_id,
            },
        )

        normalized_account_id = (
            require_app_text(
                account_id,
                field="account_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="consume",
                max_length=512,
            )
        )

        normalized_kind = (
            _normalize_usage_kind(
                kind
            )
        )

        normalized_source_id = (
            require_app_text(
                source_id,
                field="source_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="consume",
                max_length=_MAX_SOURCE_ID_LENGTH,
            )
        )

        normalized_key = (
            require_app_text(
                idempotency_key,
                field="idempotency_key",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="consume",
                max_length=(
                    _MAX_IDEMPOTENCY_KEY_LENGTH
                ),
            )
        )

        existing = self._find_existing(
            account_id=(
                normalized_account_id
            ),
            kind=normalized_kind,
            source_id=(
                normalized_source_id
            ),
            idempotency_key=(
                normalized_key
            ),
        )

        if existing is not None:
            logger.info(
                {
                    "event":
                        "entitlement_consumption_replayed",
                    "account_id":
                        normalized_account_id,
                    "usage_kind":
                        normalized_kind.value,
                    "source_id":
                        normalized_source_id,
                    "source":
                        existing.source.value,
                    "plan_code":
                        existing.plan_code.value,
                }
            )

            return existing

        plan = self._resolve_plan(
            normalized_account_id
        )

        try:
            quota = plan.quota_for(
                normalized_kind
            )
        except DomainError as exc:
            raise AppIntegrityError(
                "Resolved account plan does not contain "
                "a valid quota for the requested usage kind.",
                component=_COMPONENT,
                operation="consume",
                field="plan.quotas",
                context={
                    "account_id":
                        normalized_account_id,
                    "plan_code":
                        plan.code.value,
                    "usage_kind":
                        normalized_kind.value,
                    **lower_error_context(
                        exc
                    ),
                },
                cause=exc,
            ) from exc

        if quota.kind is not normalized_kind:
            raise AppIntegrityError(
                "Resolved plan quota does not match "
                "the requested usage kind.",
                component=_COMPONENT,
                operation="consume",
                field="quota.kind",
                context={
                    "plan_code":
                        plan.code.value,
                    "requested_kind":
                        normalized_kind.value,
                    "quota_kind":
                        quota.kind.value,
                },
            )

        occurred_at = self._clock.now()

        if quota.unlimited:
            result = self._record_unlimited(
                account_id=(
                    normalized_account_id
                ),
                kind=normalized_kind,
                source_id=(
                    normalized_source_id
                ),
                idempotency_key=(
                    normalized_key
                ),
                plan=plan,
                occurred_at=occurred_at,
            )

        else:
            result = self._consume_finite(
                account_id=(
                    normalized_account_id
                ),
                kind=normalized_kind,
                source_id=(
                    normalized_source_id
                ),
                idempotency_key=(
                    normalized_key
                ),
                plan=plan,
                quota=quota,
                occurred_at=occurred_at,
            )

        _assert_consumption_source_binding(
            result,
            account_id=(
                normalized_account_id
            ),
            kind=normalized_kind,
            source_id=(
                normalized_source_id
            ),
            operation="consume",
        )

        logger.info(
            {
                "event":
                    "entitlement_service_consume_completed",
                "account_id":
                    result.account_id,
                "usage_kind":
                    result.kind.value,
                "source_id":
                    result.source_id,
                "source":
                    result.source.value,
                "plan_code":
                    result.plan_code.value,
            }
        )

        return result


__all__ = [
    "EntitlementSource",
    "EntitlementWindow",
    "EntitlementConsumption",
    "EntitlementService",
]


if __name__ == "__main__":
    from datetime import (
        timedelta,
        timezone,
    )

    print(
        "\n=== Running BIMAP Entitlement Service Self-Test ===\n"
    )

    printer.status(
        "TEST",
        "Entitlement Service module initialized",
        "info",
    )

    now = datetime(2026, 9, 7, 20, 0,  tzinfo=timezone.utc)
    window = EntitlementWindow(start=now, end=(now + timedelta(days=7)))

    assert window.contains(now)
    assert window.contains(now + timedelta(days=6))
    assert not window.contains(window.end)

    printer.status("PASS", "Half-open renewal-window semantics", "success")

    recurring = EntitlementConsumption(
        account_id="ACCOUNT-1",
        kind=UsageKind.AUDIT,
        source_id="ORDER-1",
        idempotency_key="ENTITLE-1",
        source=(
            EntitlementSource
            .RECURRING_QUOTA
        ),
        plan_code=(
            AccountPlanCode.BASIC
        ),
        occurred_at=now,
        period_start=window.start,
        period_end=window.end,
    )

    assert (
        recurring.source
        is EntitlementSource.RECURRING_QUOTA
    )
    assert (
        recurring.kind
        is UsageKind.AUDIT
    )
    assert (recurring.to_dict()["source"] == "recurring_quota")

    printer.status("PASS", "Recurring consumption validation", "success")

    bonus = EntitlementConsumption(
        account_id="ACCOUNT-1",
        kind=UsageKind.AUDIT,
        source_id="ORDER-2",
        idempotency_key="ENTITLE-2",
        source=(
            EntitlementSource
            .BONUS_CREDIT
        ),
        plan_code=(
            AccountPlanCode.BASIC
        ),
        occurred_at=now,
    )

    assert (
        bonus.period_start
        is None
    )
    assert (
        bonus.period_end
        is None
    )

    printer.status(
        "PASS",
        "Bonus-credit consumption validation",
        "success",
    )

    unlimited = EntitlementConsumption(
        account_id="ACCOUNT-2",
        kind=(
            UsageKind
            .DATA_EXTRACTION
        ),
        source_id="EXTRACTION-1",
        idempotency_key="ENTITLE-3",
        source=(
            EntitlementSource.UNLIMITED
        ),
        plan_code=(
            AccountPlanCode.BUSINESS
        ),
        occurred_at=now,
    )

    assert (
        unlimited.source
        is EntitlementSource.UNLIMITED
    )

    printer.status(
        "PASS",
        "Unlimited consumption validation",
        "success",
    )

    try:
        EntitlementConsumption(
            account_id="ACCOUNT-1",
            kind=UsageKind.AUDIT,
            source_id="ORDER-BAD",
            idempotency_key="ENTITLE-BAD",
            source=(
                EntitlementSource
                .RECURRING_QUOTA
            ),
            plan_code=(
                AccountPlanCode.BASIC
            ),
            occurred_at=now,
        )

    except AppIntegrityError:
        printer.status(
            "PASS",
            "Recurring consumption without window rejected",
            "success",
        )

    else:
        raise AssertionError(
            "Recurring consumption without "
            "a renewal window was accepted."
        )

    print(
        "\n=== Entitlement Service self-test ran successfully ===\n"
    )