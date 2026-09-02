"""
Sequential deterministic rule executor for BIMAP.

The executor consumes a frozen ``RulesRegistry`` and normalized ``AuditContext``
objects. It deliberately executes rules in a stable sequence, returns structured
``RuleResult`` values, preserves historical version replay, and distinguishes
insufficient evidence (``unknown`` from the BaseRules wrapper) from software
execution failures (structured EngineError exceptions).

No finding construction, SLAI reasoning, report rendering, file I/O, dynamic
plugin discovery, or application/worker orchestration belongs here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from ..context import AuditContext
from ..utils.engine_errors import (
    EngineError,
    EngineIntegrityError,
    EngineValidationError,
)
from ..utils.engine_helpers import (
    announce_engine_action,
    require_engine_mapping,
    require_engine_text,
)
from .base import BaseRules, RuleResult
from .registry import RulesRegistry
from .versions import RuleVersion, VersionRule
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Rules Executor")
printer = PrettyPrinter()


def _status_value(result: RuleResult) -> str:
    """Return the normalized string value of a rule result status."""
    status = result.status
    return str(getattr(status, "value", status))


def _normalize_rule_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize requested rule identifiers with deterministic de-duplication."""
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise EngineValidationError(
            "rule_ids must be an iterable of rule identifiers.",
            component="rules_executor",
            operation="normalize_rule_ids",
            field="rule_ids",
            context={"received_type": type(values).__name__},
        )

    try:
        raw_values = tuple(values)
    except TypeError as exc:
        raise EngineValidationError(
            "rule_ids must be iterable.",
            component="rules_executor",
            operation="normalize_rule_ids",
            field="rule_ids",
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    # Sets/frozensets have no caller-significant order. Sorting them prevents
    # hash-order changes from changing deterministic execution sequence.
    if isinstance(values, (set, frozenset)):
        raw_values = tuple(sorted(raw_values, key=str))

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw_values):
        rule_id = require_engine_text(
            value,
            field=f"rule_ids[{index}]",
            error_type=EngineValidationError,
        )
        if rule_id not in seen:
            seen.add(rule_id)
            normalized.append(rule_id)
    return tuple(normalized)


class RulesExecutor:
    """Execute registered deterministic rules against canonical audit context."""

    def __init__(self, registry: RulesRegistry) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_executor",
            action="Initializing deterministic rules executor",
            event="rules_executor_init_start",
        )
        if not isinstance(registry, RulesRegistry):
            raise EngineValidationError(
                "RulesExecutor requires a RulesRegistry.",
                component="rules_executor",
                operation="initialize",
                field="registry",
                context={"received_type": type(registry).__name__},
            )

        # Execution must observe an immutable rule selection. Freezing here is
        # intentional: once an executor is composed, a running service must not
        # silently change rule revisions underneath in-flight audit jobs.
        registry.freeze()
        registry.require_frozen()
        self._registry = registry

        logger.info(
            {
                "event": "rules_executor_initialized",
                "rule_count": len(registry),
                "revision_count": registry.snapshot()["revision_count"],
            }
        )

    @property
    def registry(self) -> RulesRegistry:
        """Return the frozen registry used by this executor."""
        return self._registry

    def _execute_rule(self, rule: BaseRules, context: AuditContext) -> RuleResult:
        """Execute one resolved rule and translate implementation failures safely."""
        announce_engine_action(
            printer,
            logger,
            component="rules_executor",
            action="Executing deterministic rule revision",
            event="rules_executor_execute_rule_start",
            context={
                "rule_id": rule.definition.rule_id,
                "rule_version": str(rule.definition.version),
            },
        )
        try:
            result = rule.run(context)
        except EngineError:
            # Expected engine failures already carry safe structured metadata.
            logger.error(
                {
                    "event": "rules_executor_engine_failure",
                    "rule_id": rule.definition.rule_id,
                    "rule_version": str(rule.definition.version),
                }
            )
            raise
        except Exception as exc:
            logger.error(
                {
                    "event": "rules_executor_unhandled_rule_failure",
                    "rule_id": rule.definition.rule_id,
                    "rule_version": str(rule.definition.version),
                    "error_type": type(exc).__name__,
                }
            )
            raise EngineError(
                "Deterministic rule execution failed with an unhandled implementation error.",
                component="rules_executor",
                operation="execute_rule",
                context={
                    "rule_id": rule.definition.rule_id,
                    "rule_version": str(rule.definition.version),
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(result, RuleResult):  # defensive; BaseRules.run already enforces this
            raise EngineIntegrityError(
                "Deterministic rule executor received an invalid rule result.",
                component="rules_executor",
                operation="execute_rule",
                field="result",
                context={
                    "rule_id": rule.definition.rule_id,
                    "received_type": type(result).__name__,
                },
            )
        return result

    def execute_one(
        self,
        context: AuditContext,
        rule_id: str,
        *,
        version: RuleVersion | str | None = None,
    ) -> RuleResult:
        """Execute one current or explicitly requested historical rule revision."""
        announce_engine_action(
            printer,
            logger,
            component="rules_executor",
            action="Executing one deterministic rule",
            event="rules_executor_execute_one_start",
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "RulesExecutor requires an AuditContext.",
                component="rules_executor",
                operation="execute_one",
                field="context",
                context={"received_type": type(context).__name__},
            )
        rule = self._registry.get(rule_id, version)
        return self._execute_rule(rule, context)

    def execute(
        self,
        context: AuditContext,
        *,
        rule_ids: Iterable[str] | None = None,
    ) -> tuple[RuleResult, ...]:
        """Execute current deterministic rules in a stable sequence.

        With ``rule_ids=None`` the registry selects current revisions applicable
        to the context product and orders them lexically by stable rule ID.  With
        explicit ``rule_ids``, the caller controls the requested set/order and a
        product-incompatible rule returns ``not_applicable`` through BaseRules.
        """
        announce_engine_action(
            printer,
            logger,
            component="rules_executor",
            action="Executing deterministic ruleset",
            event="rules_executor_execute_start",
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "RulesExecutor requires an AuditContext.",
                component="rules_executor",
                operation="execute",
                field="context",
                context={"received_type": type(context).__name__},
            )

        if rule_ids is None:
            rules = self._registry.select(context)
        else:
            requested = _normalize_rule_ids(rule_ids)
            rules = tuple(self._registry.get(rule_id) for rule_id in requested)

        results = tuple(self._execute_rule(rule, context) for rule in rules)
        counts = Counter(_status_value(result) for result in results)
        logger.info(
            {
                "event": "rules_executor_completed",
                "product_code": str(
                    getattr(context.product_code, "value", context.product_code)
                ),
                "rule_count": len(results),
                "status_counts": dict(sorted(counts.items())),
            }
        )
        return results

    def execute_versioned(
        self,
        context: AuditContext,
        versions: Mapping[str, RuleVersion | str],
    ) -> tuple[RuleResult, ...]:
        """Replay an exact recorded rule-id -> version selection.

        This method is intentionally explicit: it executes exactly the supplied
        registered revisions and does not silently add current rules.  Sorting by
        rule ID makes replay order independent of mapping insertion order.
        """
        announce_engine_action(
            printer,
            logger,
            component="rules_executor",
            action="Executing version-pinned deterministic ruleset",
            event="rules_executor_execute_versioned_start",
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "RulesExecutor requires an AuditContext.",
                component="rules_executor",
                operation="execute_versioned",
                field="context",
                context={"received_type": type(context).__name__},
            )

        mapping = require_engine_mapping(
            versions,
            field="versions",
            error_type=EngineValidationError,
        )
        resolved: list[BaseRules] = []
        for rule_id in sorted(mapping):
            version = VersionRule.parse(
                mapping[rule_id],
                field=f"versions.{rule_id}",
            )
            resolved.append(self._registry.get(rule_id, version))

        results = tuple(self._execute_rule(rule, context) for rule in resolved)
        counts = Counter(_status_value(result) for result in results)
        logger.info(
            {
                "event": "rules_executor_versioned_replay_completed",
                "product_code": str(
                    getattr(context.product_code, "value", context.product_code)
                ),
                "rule_count": len(results),
                "status_counts": dict(sorted(counts.items())),
            }
        )
        return results

    def status_counts(self, results: Iterable[RuleResult]) -> dict[str, int]:
        """Return deterministic status counts for already-produced rule results."""
        announce_engine_action(
            printer,
            logger,
            component="rules_executor",
            action="Summarizing deterministic rule statuses",
            event="rules_executor_status_counts_start",
        )
        if isinstance(results, (str, bytes, bytearray, Mapping)):
            raise EngineValidationError(
                "results must be an iterable of RuleResult values.",
                component="rules_executor",
                operation="status_counts",
                field="results",
                context={"received_type": type(results).__name__},
            )
        try:
            items = tuple(results)
        except TypeError as exc:
            raise EngineValidationError(
                "results must be iterable.",
                component="rules_executor",
                operation="status_counts",
                field="results",
                context={"received_type": type(results).__name__},
                cause=exc,
            ) from exc

        for index, result in enumerate(items):
            if not isinstance(result, RuleResult):
                raise EngineValidationError(
                    "status_counts accepts RuleResult values only.",
                    component="rules_executor",
                    operation="status_counts",
                    field=f"results[{index}]",
                    context={"received_type": type(result).__name__},
                )
        counts = Counter(_status_value(result) for result in items)
        return dict(sorted(counts.items()))


__all__ = ["RulesExecutor"]