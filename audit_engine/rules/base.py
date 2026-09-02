"""
Canonical protocol and structured result types for deterministic BIMAP rules.

The rule layer consumes a normalized :class:`AuditContext`; it does not parse
uploads, call SLAI, render reports, or manufacture domain findings directly.
Each concrete rule declares immutable metadata and implements one protected
``_evaluate`` method.  The public ``run`` wrapper centralizes applicability,
missing-evidence handling, result identity, provenance-reference validation,
and safe error boundaries needed by the executor.

This keeps the dependency direction required by BIMAP's import architecture:

    audit_engine.context / domain evidence
                ↓
          rules/base.py
                ↓
          rules/registry.py
                ↓
          rules/executor.py

Grounded findings are created later by product-specific auditors/result mapping;
this module therefore owns *rule results*, not Finding lifecycle semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, ClassVar

from ...domain.products.models import ProductCode
from ...domain.utils.domain_errors import DomainError
from ...domain.utils.domain_helpers import *
from ..context import AuditContext
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from .versions import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Base Deterministic Rule")
printer = PrettyPrinter()


def _normalize_unique_texts(
    values: Iterable[str],
    *,
    field: str,
) -> tuple[str, ...]:
    """Normalize an iterable of non-empty strings with stable de-duplication."""
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise EngineValidationError(
            f"{field} must be an iterable of strings, not a scalar or mapping.",
            component="rules_base",
            operation="normalize_texts",
            field=field,
            context={"received_type": type(values).__name__},
        )

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise EngineValidationError(
            f"{field} must be iterable.",
            component="rules_base",
            operation="normalize_texts",
            field=field,
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(iterator):
        text = require_engine_text(
            value,
            field=f"{field}[{index}]",
            error_type=EngineValidationError,
        )
        if text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


class RuleStatus(str, Enum):
    """Canonical structured status returned by a deterministic BIMAP rule."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"

    @classmethod
    def parse(cls, value: "str | RuleStatus") -> "RuleStatus":
        """Normalize a rule-result status without inventing aliases."""
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Parsing deterministic rule status",
            event="rule_status_parse_start",
        )
        if isinstance(value, cls):
            return value

        text = require_engine_text(
            value,
            field="status",
            error_type=EngineValidationError,
        ).casefold()
        try:
            return cls(text)
        except ValueError as exc:
            raise EngineValidationError(
                "Unsupported deterministic rule status.",
                component="rules_base",
                operation="parse_status",
                field="status",
                context={
                    "received": text,
                    "supported": tuple(item.value for item in cls),
                },
                cause=exc,
            ) from exc


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """Immutable metadata that makes one deterministic rule revision auditable.

    ``applicable_products`` is explicit rather than inferred from class names or
    package paths. ``severity_policy`` is an optional stable policy identifier;
    the rule layer does not redefine the domain's Severity semantics.  Benchmark
    cases and known limitations are traceability metadata only: this module does
    not execute benchmark suites at runtime.
    """

    rule_id: str
    version: RuleVersion | str
    applicable_products: tuple[ProductCode | str, ...]
    required_evidence_groups: tuple[str, ...] = ()
    severity_policy: str | None = None
    benchmark_case_ids: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Validating deterministic rule definition",
            event="rule_definition_validate_start",
        )
        rule_id = require_engine_text(
            self.rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        version = VersionRule.parse(self.version)

        if isinstance(self.applicable_products, (str, bytes, bytearray, Mapping)):
            raise EngineValidationError(
                "applicable_products must be an iterable of ProductCode values.",
                component="rules_base",
                operation="validate_definition",
                field="applicable_products",
                context={"received_type": type(self.applicable_products).__name__},
            )

        try:
            raw_products = tuple(self.applicable_products)
        except TypeError as exc:
            raise EngineValidationError(
                "applicable_products must be iterable.",
                component="rules_base",
                operation="validate_definition",
                field="applicable_products",
                context={"received_type": type(self.applicable_products).__name__},
                cause=exc,
            ) from exc

        if not raw_products:
            raise EngineConfigurationError(
                "A deterministic rule must explicitly declare at least one applicable BIMAP product.",
                component="rules_base",
                operation="validate_definition",
                field="applicable_products",
                context={"rule_id": rule_id},
            )

        products: list[ProductCode] = []
        seen_products: set[ProductCode] = set()
        for index, raw_product in enumerate(raw_products):
            try:
                product = ProductCode.parse(raw_product)
            except DomainError as exc:
                raise EngineValidationError(
                    "Rule definition contains an invalid BIMAP product code.",
                    component="rules_base",
                    operation="validate_definition",
                    field=f"applicable_products[{index}]",
                    context={"rule_id": rule_id, **lower_error_context(exc)},
                    cause=exc,
                ) from exc
            if product not in seen_products:
                seen_products.add(product)
                products.append(product)

        required_groups = _normalize_unique_texts(
            self.required_evidence_groups,
            field="required_evidence_groups",
        )
        benchmark_case_ids = _normalize_unique_texts(
            self.benchmark_case_ids,
            field="benchmark_case_ids",
        )
        known_limitations = _normalize_unique_texts(
            self.known_limitations,
            field="known_limitations",
        )
        severity_policy = (
            None
            if self.severity_policy is None
            else require_engine_text(
                self.severity_policy,
                field="severity_policy",
                error_type=EngineValidationError,
            )
        )

        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "applicable_products", tuple(products))
        object.__setattr__(self, "required_evidence_groups", required_groups)
        object.__setattr__(self, "severity_policy", severity_policy)
        object.__setattr__(self, "benchmark_case_ids", benchmark_case_ids)
        object.__setattr__(self, "known_limitations", known_limitations)

    @property
    def automation_type(self) -> str:
        """Return the fixed classification owned by this deterministic package."""
        return "deterministic"

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic, evidence-free registry metadata."""
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Serializing deterministic rule definition",
            event="rule_definition_to_dict_start",
            context={"rule_id": self.rule_id, "rule_version": str(self.version)},
        )
        return {
            "rule_id": self.rule_id,
            "version": str(self.version),
            "audit_scope": [ProductCode.parse(item).value for item in self.applicable_products],
            "required_inputs": list(self.required_evidence_groups),
            "severity_policy": self.severity_policy,
            "automation_type": self.automation_type,
            "benchmark_case_ids": list(self.benchmark_case_ids),
            "known_limitations": list(self.known_limitations),
        }


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Structured deterministic result produced before Finding construction.

    The value intentionally contains only rule identity, observed/expected state,
    stable evidence references, canonical status, and deterministic metrics.
    Explanatory prose, remediation, domain Severity/Confidence, lifecycle state,
    and contextual reasoning belong to later layers.
    """

    rule_id: str
    rule_version: RuleVersion | str
    status: RuleStatus | str
    evidence_refs: tuple[str, ...] = ()
    observed_value: Any = None
    expected_value: Any = None
    metrics: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Validating deterministic rule result",
            event="rule_result_validate_start",
        )
        rule_id = require_engine_text(
            self.rule_id,
            field="rule_id",
            error_type=EngineValidationError,
        )
        version = VersionRule.parse(self.rule_version)
        status = RuleStatus.parse(self.status)
        evidence_refs = _normalize_unique_texts(
            self.evidence_refs,
            field="evidence_refs",
        )

        observed_primitive = to_engine_primitive(
            self.observed_value,
            field="observed_value",
        )
        expected_primitive = to_engine_primitive(
            self.expected_value,
            field="expected_value",
        )
        metrics_mapping = require_engine_mapping(
            self.metrics,
            field="metrics",
            error_type=EngineValidationError,
        )
        metrics_primitive = to_engine_primitive(metrics_mapping, field="metrics")
        if not isinstance(metrics_primitive, dict):
            raise EngineSerializationError(
                "Rule metrics did not normalize to a JSON object.",
                component="rules_base",
                operation="validate_result",
                field="metrics",
                context={"rule_id": rule_id},
            )

        try:
            observed_value = freeze_json_value(
                observed_primitive,
                field="observed_value",
            )
            expected_value = freeze_json_value(
                expected_primitive,
                field="expected_value",
            )
            metrics = freeze_json_value(metrics_primitive, field="metrics")
        except DomainError as exc:
            raise EngineSerializationError(
                "Rule result values could not be frozen deterministically.",
                component="rules_base",
                operation="validate_result",
                context={"rule_id": rule_id, **lower_error_context(exc)},
                cause=exc,
            ) from exc

        if not isinstance(metrics, Mapping):
            raise EngineSerializationError(
                "Frozen rule metrics must remain a mapping.",
                component="rules_base",
                operation="validate_result",
                field="metrics",
                context={"rule_id": rule_id},
            )

        object.__setattr__(self, "rule_id", rule_id)
        object.__setattr__(self, "rule_version", version)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "observed_value", observed_value)
        object.__setattr__(self, "expected_value", expected_value)
        object.__setattr__(self, "metrics", metrics)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical primitive representation of this rule result."""
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Serializing deterministic rule result",
            event="rule_result_to_dict_start",
            context={
                "rule_id": self.rule_id,
                "rule_version": str(self.rule_version),
                "status": RuleStatus.parse(self.status).value,
            },
        )
        return {
            "rule_id": self.rule_id,
            "rule_version": str(self.rule_version),
            "status": RuleStatus.parse(self.status).value,
            "observed_value": thaw_json_value(self.observed_value),
            "expected_value": thaw_json_value(self.expected_value),
            "evidence_refs": list(self.evidence_refs),
            "metrics": thaw_json_value(self.metrics),
        }


class BaseRules(ABC):
    """Base class for one deterministic, versioned BIMAP rule revision.

    Subclasses must provide a class-level ``DEFINITION`` and implement only
    ``_evaluate(context)``.  Callers use :meth:`run`; this wrapper enforces the
    common deterministic rule contract and prevents each product-specific rule
    from reimplementing applicability/evidence/result validation.
    """

    DEFINITION: ClassVar[RuleDefinition | None] = None

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Initializing deterministic audit rule",
            event="base_rule_init_start",
            context={"rule_class": f"{type(self).__module__}.{type(self).__qualname__}"},
        )
        definition = type(self).DEFINITION
        if not isinstance(definition, RuleDefinition):
            raise EngineConfigurationError(
                "Concrete deterministic rules must declare a RuleDefinition in DEFINITION.",
                component="rules_base",
                operation="initialize",
                field="DEFINITION",
                context={"rule_class": f"{type(self).__module__}.{type(self).__qualname__}"},
            )
        self._definition = definition

    @property
    def definition(self) -> RuleDefinition:
        """Return this concrete rule revision's immutable definition."""
        return self._definition

    def applies_to(self, context: AuditContext) -> bool:
        """Return whether this rule is applicable to the context's product."""
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Checking deterministic rule applicability",
            event="base_rule_applies_to_start",
            context={"rule_id": self.definition.rule_id},
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "Rule applicability requires an AuditContext.",
                component="rules_base",
                operation="applies_to",
                field="context",
                context={"received_type": type(context).__name__},
            )
        return context.product_code in self.definition.applicable_products

    def missing_required_groups(self, context: AuditContext) -> tuple[str, ...]:
        """Return required evidence groups that are absent or empty."""
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Checking required deterministic rule evidence",
            event="base_rule_missing_groups_start",
            context={"rule_id": self.definition.rule_id},
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "Required-evidence checks require an AuditContext.",
                component="rules_base",
                operation="missing_required_groups",
                field="context",
                context={"received_type": type(context).__name__},
            )

        missing: list[str] = []
        for group_name in self.definition.required_evidence_groups:
            evidence_ids = context.evidence_groups.get(group_name)
            if not evidence_ids:
                missing.append(group_name)
        return tuple(missing)

    def result(
        self,
        status: RuleStatus | str,
        *,
        evidence_refs: Iterable[str] = (),
        observed_value: Any = None,
        expected_value: Any = None,
        metrics: Mapping[str, Any] | None = None,
    ) -> RuleResult:
        """Create a RuleResult bound to this rule's immutable identity."""
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Creating deterministic rule result",
            event="base_rule_result_start",
            context={"rule_id": self.definition.rule_id},
        )
        return RuleResult(
            rule_id=self.definition.rule_id,
            rule_version=self.definition.version,
            status=status,
            evidence_refs=_normalize_unique_texts(
                evidence_refs,
                field="evidence_refs",
            ),
            observed_value=observed_value,
            expected_value=expected_value,
            metrics={} if metrics is None else metrics,
        )

    def run(self, context: AuditContext) -> RuleResult:
        """Execute this rule through the canonical deterministic wrapper.

        Product mismatch becomes ``not_applicable``. Missing declared evidence
        groups become ``unknown`` rather than a false pass/fail. Implementation
        errors are deliberately *not* converted to ``unknown``; they propagate
        to :class:`RulesExecutor`, because uncertainty in evidence and a software
        failure are materially different audit states.
        """
        announce_engine_action(
            printer,
            logger,
            component="rules_base",
            action="Running deterministic audit rule",
            event="base_rule_run_start",
            context={
                "rule_id": self.definition.rule_id,
                "rule_version": str(self.definition.version),
            },
        )
        if not isinstance(context, AuditContext):
            raise EngineValidationError(
                "Deterministic rule execution requires an AuditContext.",
                component="rules_base",
                operation="run",
                field="context",
                context={"received_type": type(context).__name__},
            )

        if not self.applies_to(context):
            return self.result(RuleStatus.NOT_APPLICABLE)

        missing_groups = self.missing_required_groups(context)
        if missing_groups:
            return self.result(
                RuleStatus.UNKNOWN,
                metrics={"missing_required_evidence_groups": list(missing_groups)},
            )

        result = self._evaluate(context)
        if not isinstance(result, RuleResult):
            raise EngineIntegrityError(
                "Concrete deterministic rule returned an unsupported result type.",
                component="rules_base",
                operation="run",
                field="result",
                context={
                    "rule_id": self.definition.rule_id,
                    "received_type": type(result).__name__,
                },
            )

        if result.rule_id != self.definition.rule_id:
            raise EngineIntegrityError(
                "Rule result identity does not match the executing rule definition.",
                component="rules_base",
                operation="run",
                field="rule_id",
                context={
                    "expected_rule_id": self.definition.rule_id,
                    "received_rule_id": result.rule_id,
                },
            )
        if result.rule_version != self.definition.version:
            raise EngineIntegrityError(
                "Rule result version does not match the executing rule definition.",
                component="rules_base",
                operation="run",
                field="rule_version",
                context={
                    "rule_id": self.definition.rule_id,
                    "expected": str(self.definition.version),
                    "received": str(result.rule_version),
                },
            )

        context_evidence_ids = set(context.evidence_ids)
        unresolved = tuple(
            evidence_id
            for evidence_id in result.evidence_refs
            if evidence_id not in context_evidence_ids
        )
        if unresolved:
            raise EngineIntegrityError(
                "Rule result references evidence absent from the AuditContext.",
                component="rules_base",
                operation="run",
                field="evidence_refs",
                context={
                    "rule_id": self.definition.rule_id,
                    "unresolved_evidence_refs": unresolved,
                },
            )

        return result

    @abstractmethod
    def _evaluate(self, context: AuditContext) -> RuleResult:
        """Implement deterministic rule logic against normalized evidence only."""
        raise NotImplementedError


__all__ = [
    "RuleStatus",
    "RuleDefinition",
    "RuleResult",
    "BaseRules",
]