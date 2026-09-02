"""
Version-aware registry for BIMAP deterministic audit rules.

The registry is the single runtime owner of rule discovery inside the
``audit_engine.rules`` package. It stores concrete, already-constructed
``BaseRules`` instances by stable ``rule_id`` and exact rule version, records one
explicit current revision per rule, supports historical revisions for replay,
and can be frozen before execution so customer-facing behavior cannot mutate
mid-process.

The registry performs no filesystem scanning, plugin loading, YAML reading,
network access, SLAI discovery, or product auditing. Concrete product packages or
the composition root construct rules and register them explicitly.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from threading import RLock
from typing import Any

from ..context import AuditContext
from ..utils.engine_errors import (
    EngineConfigurationError,
    EngineIntegrityError,
    EngineValidationError,
)
from ..utils.engine_helpers import (
    announce_engine_action,
    require_engine_mapping,
    require_engine_text,
)
from .base import BaseRules
from .versions import RuleVersion, RuleVersionRecord, VersionRule
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Rules Registry")
printer = PrettyPrinter()


class RulesRegistry:
    """Controlled registry of current and historical deterministic rule revisions."""

    def __init__(
        self,
        rules: Iterable[BaseRules] | None = None,
        *,
        current_versions: Mapping[str, RuleVersion | str] | None = None,
        freeze: bool = False,
    ) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Initializing deterministic rule registry",
            event="rules_registry_init_start",
        )
        self._lock = RLock()
        self._rules: dict[str, dict[RuleVersion, BaseRules]] = {}
        self._current: dict[str, RuleVersion] = {}
        self._frozen = False

        if not isinstance(freeze, bool):
            raise EngineConfigurationError(
                "freeze must be a boolean.",
                component="rules_registry",
                operation="initialize",
                field="freeze",
                context={"received_type": type(freeze).__name__},
            )

        if rules is not None:
            self.register_many(rules)

        if current_versions is not None:
            mapping = require_engine_mapping(
                current_versions,
                field="current_versions",
                error_type=EngineConfigurationError,
            )
            for rule_id, version in mapping.items():
                self.set_current(rule_id, version)

        self.validate()
        if freeze:
            self.freeze()

        logger.info(
            {
                "event": "rules_registry_initialized",
                "rule_count": len(self._rules),
                "revision_count": sum(len(bucket) for bucket in self._rules.values()),
                "frozen": self._frozen,
            }
        )

    @property
    def is_frozen(self) -> bool:
        """Return whether registry mutation has been disabled."""
        return self._frozen

    def _require_mutable(self, *, operation: str) -> None:
        if self._frozen:
            raise EngineConfigurationError(
                "Deterministic rule registry is frozen and cannot be mutated.",
                component="rules_registry",
                operation=operation,
            )

    def register(self, rule: BaseRules, *, make_current: bool = False) -> None:
        """Register one exact rule revision without silently replacing another.

        The first revision registered for a new ``rule_id`` becomes current.
        Adding later revisions preserves the existing current revision unless
        ``make_current=True`` is explicit. This prevents a registration-order
        change from silently changing customer-facing behavior.
        """
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Registering deterministic rule revision",
            event="rules_registry_register_start",
        )
        with self._lock:
            self._require_mutable(operation="register")
            if not isinstance(make_current, bool):
                raise EngineConfigurationError(
                    "make_current must be a boolean.",
                    component="rules_registry",
                    operation="register",
                    field="make_current",
                    context={"received_type": type(make_current).__name__},
                )
            if not isinstance(rule, BaseRules):
                raise EngineConfigurationError(
                    "RulesRegistry accepts BaseRules instances only.",
                    component="rules_registry",
                    operation="register",
                    field="rule",
                    context={"received_type": type(rule).__name__},
                )

            definition = rule.definition
            rule_id = definition.rule_id
            version = VersionRule.parse(definition.version)
            bucket = self._rules.setdefault(rule_id, {})

            if version in bucket:
                raise EngineConfigurationError(
                    "Duplicate deterministic rule revision registration.",
                    component="rules_registry",
                    operation="register",
                    field="rule_version",
                    context={"rule_id": rule_id, "rule_version": str(version)},
                )

            bucket[version] = rule
            if rule_id not in self._current:
                self._current[rule_id] = version
            elif make_current:
                self._current[rule_id] = version

            logger.debug(
                {
                    "event": "rules_registry_revision_registered",
                    "rule_id": rule_id,
                    "rule_version": str(version),
                    "is_current": self._current[rule_id] == version,
                    "rule_class": f"{type(rule).__module__}.{type(rule).__qualname__}",
                }
            )

    def register_many(self, rules: Iterable[BaseRules]) -> None:
        """Register multiple rule instances in caller-supplied order."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Registering deterministic rule revisions",
            event="rules_registry_register_many_start",
        )
        if isinstance(rules, (str, bytes, bytearray, Mapping)):
            raise EngineConfigurationError(
                "rules must be an iterable of BaseRules instances.",
                component="rules_registry",
                operation="register_many",
                field="rules",
                context={"received_type": type(rules).__name__},
            )
        try:
            items = tuple(rules)
        except TypeError as exc:
            raise EngineConfigurationError(
                "rules must be iterable.",
                component="rules_registry",
                operation="register_many",
                field="rules",
                context={"received_type": type(rules).__name__},
                cause=exc,
            ) from exc

        with self._lock:
            self._require_mutable(operation="register_many")

            # Preflight the full batch before mutating registry state. This keeps
            # a caller error in a later item from leaving a partially registered
            # batch behind.
            batch_keys: set[tuple[str, RuleVersion]] = set()
            for index, rule in enumerate(items):
                if not isinstance(rule, BaseRules):
                    raise EngineConfigurationError(
                        "rules must contain BaseRules instances only.",
                        component="rules_registry",
                        operation="register_many",
                        field=f"rules[{index}]",
                        context={"received_type": type(rule).__name__},
                    )
                definition = rule.definition
                version = VersionRule.parse(definition.version)
                key = (definition.rule_id, version)
                if key in batch_keys:
                    raise EngineConfigurationError(
                        "Batch contains a duplicate deterministic rule revision.",
                        component="rules_registry",
                        operation="register_many",
                        field=f"rules[{index}]",
                        context={
                            "rule_id": definition.rule_id,
                            "rule_version": str(definition.version),
                        },
                    )
                batch_keys.add(key)

                existing = self._rules.get(definition.rule_id, {})
                if version in existing:
                    raise EngineConfigurationError(
                        "Batch conflicts with an already registered rule revision.",
                        component="rules_registry",
                        operation="register_many",
                        field=f"rules[{index}]",
                        context={
                            "rule_id": definition.rule_id,
                            "rule_version": str(version),
                        },
                    )

            for rule in items:
                definition = rule.definition
                version = VersionRule.parse(definition.version)
                bucket = self._rules.setdefault(definition.rule_id, {})
                bucket[version] = rule
                if definition.rule_id not in self._current:
                    self._current[definition.rule_id] = version
                logger.debug(
                    {
                        "event": "rules_registry_revision_registered",
                        "rule_id": definition.rule_id,
                        "rule_version": str(version),
                        "is_current": (
                            self._current[definition.rule_id] == definition.version
                        ),
                        "rule_class": (
                            f"{type(rule).__module__}.{type(rule).__qualname__}"
                        ),
                    }
                )

    def set_current(self, rule_id: str, version: RuleVersion | str) -> None:
        """Explicitly select the current registered revision for one rule."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Selecting current deterministic rule revision",
            event="rules_registry_set_current_start",
        )
        with self._lock:
            self._require_mutable(operation="set_current")
            key = require_engine_text(
                rule_id,
                field="rule_id",
                error_type=EngineValidationError,
            )
            candidate = VersionRule.parse(version)
            bucket = self._rules.get(key)
            if bucket is None:
                raise EngineValidationError(
                    "Cannot select a current version for an unregistered rule.",
                    component="rules_registry",
                    operation="set_current",
                    field="rule_id",
                    context={"rule_id": key},
                )
            if candidate not in bucket:
                raise EngineValidationError(
                    "Requested current rule version is not registered.",
                    component="rules_registry",
                    operation="set_current",
                    field="rule_version",
                    context={
                        "rule_id": key,
                        "requested": str(candidate),
                        "registered": tuple(str(item) for item in sorted(bucket)),
                    },
                )
            self._current[key] = candidate
            logger.debug(
                {
                    "event": "rules_registry_current_revision_selected",
                    "rule_id": key,
                    "rule_version": str(candidate),
                }
            )

    def freeze(self) -> None:
        """Validate and permanently disable runtime registry mutation."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Freezing deterministic rule registry",
            event="rules_registry_freeze_start",
        )
        with self._lock:
            if self._frozen:
                return
            self.validate()
            self._frozen = True
            logger.info(
                {
                    "event": "rules_registry_frozen",
                    "rule_count": len(self._rules),
                    "revision_count": sum(len(bucket) for bucket in self._rules.values()),
                }
            )

    def require_frozen(self) -> None:
        """Fail closed if execution is attempted against a mutable registry."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Requiring frozen deterministic rule registry",
            event="rules_registry_require_frozen_start",
        )
        if not self._frozen:
            raise EngineConfigurationError(
                "Deterministic rule execution requires a frozen registry.",
                component="rules_registry",
                operation="require_frozen",
            )

    def get(
        self,
        rule_id: str,
        version: RuleVersion | str | None = None,
    ) -> BaseRules:
        """Resolve the current or an exact historical revision of one rule."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Resolving deterministic rule revision",
            event="rules_registry_get_start",
        )
        key = require_engine_text(
            rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        with self._lock:
            bucket = self._rules.get(key)
            if bucket is None:
                raise EngineValidationError(
                    "Unknown deterministic rule identifier.",
                    component="rules_registry",
                    operation="get",
                    field="rule_id",
                    context={"rule_id": key},
                )

            resolved_version = (
                self._current[key]
                if version is None
                else VersionRule.parse(version)
            )
            try:
                return bucket[resolved_version]
            except KeyError as exc:
                raise EngineValidationError(
                    "Requested deterministic rule revision is not registered.",
                    component="rules_registry",
                    operation="get",
                    field="rule_version",
                    context={
                        "rule_id": key,
                        "requested": str(resolved_version),
                        "registered": tuple(str(item) for item in sorted(bucket)),
                    },
                    cause=exc,
                ) from exc

    def current_version(self, rule_id: str) -> RuleVersion:
        """Return the current revision selected for one registered rule."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Resolving current deterministic rule version",
            event="rules_registry_current_version_start",
        )
        key = require_engine_text(
            rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        with self._lock:
            try:
                return self._current[key]
            except KeyError as exc:
                raise EngineValidationError(
                    "Unknown deterministic rule identifier.",
                    component="rules_registry",
                    operation="current_version",
                    field="rule_id",
                    context={"rule_id": key},
                    cause=exc,
                ) from exc

    def versions(self, rule_id: str) -> tuple[RuleVersion, ...]:
        """Return all registered revisions for one rule in version order."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Listing deterministic rule versions",
            event="rules_registry_versions_start",
        )
        key = require_engine_text(
            rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        with self._lock:
            bucket = self._rules.get(key)
            if bucket is None:
                raise EngineValidationError(
                    "Unknown deterministic rule identifier.",
                    component="rules_registry",
                    operation="versions",
                    field="rule_id",
                    context={"rule_id": key},
                )
            return tuple(sorted(bucket))

    def version_record(self, rule_id: str) -> RuleVersionRecord:
        """Return immutable current/supported version metadata for one rule."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Building deterministic rule version record",
            event="rules_registry_version_record_start",
        )
        key = require_engine_text(
            rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        return RuleVersionRecord(
            rule_id=key,
            current=self.current_version(key),
            supported=self.versions(key),
        )

    def rule_ids(self) -> tuple[str, ...]:
        """Return all rule identifiers in deterministic lexical order."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Listing deterministic rule identifiers",
            event="rules_registry_rule_ids_start",
        )
        with self._lock:
            return tuple(sorted(self._rules))

    def select(self, context: AuditContext) -> tuple[BaseRules, ...]:
        """Select current rule revisions applicable to the context product."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Selecting applicable deterministic rules",
            event="rules_registry_select_start",
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "Rule selection requires an AuditContext.",
                component="rules_registry",
                operation="select",
                field="context",
                context={"received_type": type(context).__name__},
            )

        selected: list[BaseRules] = []
        for rule_id in self.rule_ids():
            rule = self.get(rule_id)
            if rule.applies_to(context):
                selected.append(rule)
        return tuple(selected)

    def validate(self) -> None:
        """Validate registry identity/version invariants and fail closed on drift."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Validating deterministic rule registry",
            event="rules_registry_validate_start",
        )
        with self._lock:
            if set(self._rules) != set(self._current):
                raise EngineIntegrityError(
                    "Rule registry current-version index does not match registered rule identifiers.",
                    component="rules_registry",
                    operation="validate",
                    context={
                        "registered_rule_count": len(self._rules),
                        "current_rule_count": len(self._current),
                    },
                )

            for rule_id, bucket in self._rules.items():
                if not bucket:
                    raise EngineIntegrityError(
                        "Registered rule identifier has no revisions.",
                        component="rules_registry",
                        operation="validate",
                        context={"rule_id": rule_id},
                    )
                current = self._current[rule_id]
                if current not in bucket:
                    raise EngineIntegrityError(
                        "Current rule version is absent from its revision bucket.",
                        component="rules_registry",
                        operation="validate",
                        context={"rule_id": rule_id, "current": str(current)},
                    )

                for version, rule in bucket.items():
                    definition = rule.definition
                    if definition.rule_id != rule_id:
                        raise EngineIntegrityError(
                            "Rule registry key does not match rule definition identity.",
                            component="rules_registry",
                            operation="validate",
                            context={
                                "registry_rule_id": rule_id,
                                "definition_rule_id": definition.rule_id,
                            },
                        )
                    if definition.version != version:
                        raise EngineIntegrityError(
                            "Rule registry version key does not match rule definition revision.",
                            component="rules_registry",
                            operation="validate",
                            context={
                                "rule_id": rule_id,
                                "registry_version": str(version),
                                "definition_version": str(definition.version),
                            },
                        )

    def snapshot(self) -> dict[str, Any]:
        """Return deterministic evidence-free metadata for manifests/diagnostics."""
        announce_engine_action(
            printer,
            logger,
            component="rules_registry",
            action="Creating deterministic rule registry snapshot",
            event="rules_registry_snapshot_start",
        )
        with self._lock:
            rules_payload: list[dict[str, Any]] = []
            for rule_id in sorted(self._rules):
                current_version = self._current[rule_id]
                current_rule = self._rules[rule_id][current_version]
                definition_payload = current_rule.definition.to_dict()
                definition_payload.update(
                    {
                        "current_version": str(current_version),
                        "supported_versions": [
                            str(item) for item in sorted(self._rules[rule_id])
                        ],
                        "rule_class": (
                            f"{type(current_rule).__module__}."
                            f"{type(current_rule).__qualname__}"
                        ),
                    }
                )
                rules_payload.append(definition_payload)

            return {
                "frozen": self._frozen,
                "rule_count": len(self._rules),
                "revision_count": sum(len(bucket) for bucket in self._rules.values()),
                "rules": rules_payload,
            }

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, rule_id: object) -> bool:
        return isinstance(rule_id, str) and rule_id in self._rules

    def __iter__(self) -> Iterator[BaseRules]:
        for rule_id in self.rule_ids():
            yield self.get(rule_id)


__all__ = ["RulesRegistry"]