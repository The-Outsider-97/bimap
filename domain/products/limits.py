"""
BIMAP product limit definitions and deterministic limit evaluation.

The implementation report requires bounded commercial scopes and identifies
examples such as family count/export size and document/evidence-matrix size.
It does not establish final numeric thresholds. Consequently this module models
validated limits without hard-coding unverified values; concrete limits belong
in product configuration once selected and validated.

Dependency direction
--------------------
domain.utils
    ↑
products/models.py
    ↑
products/limits.py

``limits.py`` may import product identity/catalog models. ``models.py`` must not
import this module, which prevents a circular dependency.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from .models import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP.Domain.Products.Limits")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("PRODUCTS", action, "info")
    logger.debug(action)


class LimitUnit(str, Enum):
    """
    Measurement family for a product constraint.

    ``COUNT`` supports bounded item/document/family/evidence quantities.
    ``BYTES`` supports file/package size constraints. ``CUSTOM`` is retained
    for future configured measures without embedding speculative semantics in
    the domain model.
    """

    COUNT = "count"
    BYTES = "bytes"
    CUSTOM = "custom"

    @classmethod
    def parse(cls, value: Any) -> "LimitUnit":
        """Normalize a supported value into ``LimitUnit``."""
        _announce("Parsing limit unit")

        if isinstance(value, cls):
            return value

        normalized = require_text(value, field="unit").lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            logger.warning("Unsupported BIMAP product-limit unit: %s", normalized)
            raise DomainValidationError(
                "Unsupported product-limit unit.",
                field="unit",
                context={
                    "received": normalized,
                    "allowed": tuple(unit.value for unit in cls),
                },
            ) from exc

    def __str__(self) -> str:
        return self.value


def _normalize_non_negative_int(value: Any, *, field: str) -> int:
    """Validate an integer quantity without accepting booleans."""
    _announce("Normalizing product-limit quantity")

    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(
            "Limit quantity must be an integer.",
            field=field,
            context={"received_type": type(value).__name__},
        )

    if value < 0:
        raise DomainValidationError(
            "Limit quantity must be non-negative.",
            field=field,
            context={"received": value},
        )

    return value


@dataclass(frozen=True, slots=True)
class LimitConstraint:
    """
    One configured upper bound for a measurable product resource.

    ``key`` is deliberately configuration-defined rather than an enum. The
    report identifies several scope-control examples but does not freeze a
    complete metric registry. This preserves future compatibility without
    inventing unsupported product semantics.
    """

    key: str
    maximum: int
    unit: LimitUnit

    description: str | None = None
    custom_unit: str | None = None
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        _announce("Validating product limit constraint")

        object.__setattr__(
            self,
            "key",
            require_text(self.key, field="key"),
        )
        object.__setattr__(
            self,
            "maximum",
            _normalize_non_negative_int(self.maximum, field="maximum"),
        )

        unit = LimitUnit.parse(self.unit)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, field="description"),
        )
        object.__setattr__(
            self,
            "custom_unit",
            optional_text(self.custom_unit, field="custom_unit"),
        )

        if unit is LimitUnit.CUSTOM and self.custom_unit is None:
            raise DomainInvariantError(
                "custom_unit is required when unit='custom'.",
                field="custom_unit",
                context={"key": self.key},
            )

        if unit is not LimitUnit.CUSTOM and self.custom_unit is not None:
            raise DomainInvariantError(
                "custom_unit may only be set when unit='custom'.",
                field="custom_unit",
                context={
                    "key": self.key,
                    "unit": unit.value,
                },
            )

        source_metadata = require_mapping(self.metadata, field="metadata")
        frozen_metadata = freeze_json_value(source_metadata, field="metadata")

        if not isinstance(frozen_metadata, Mapping):
            raise DomainValidationError(
                "Limit metadata normalization did not produce a mapping.",
                field="metadata",
            )

        object.__setattr__(self, "metadata", frozen_metadata)

    def evaluate(self, observed: int) -> "LimitEvaluation":
        """Evaluate one observed quantity against this upper bound."""
        _announce("Evaluating product limit constraint")

        normalized_observed = _normalize_non_negative_int(
            observed,
            field="observed",
        )

        exceeded_by = max(0, normalized_observed - self.maximum)
        remaining = max(0, self.maximum - normalized_observed)

        return LimitEvaluation(
            key=self.key,
            observed=normalized_observed,
            maximum=self.maximum,
            unit=self.unit,
            custom_unit=self.custom_unit,
            allowed=normalized_observed <= self.maximum,
            remaining=remaining,
            exceeded_by=exceeded_by,
        )

    def assert_allows(self, observed: int) -> None:
        """Raise when an observed quantity exceeds this configured limit."""
        _announce("Enforcing product limit constraint")

        evaluation = self.evaluate(observed)
        if evaluation.allowed:
            return

        raise DomainInvariantError(
            "Configured product limit exceeded.",
            field=self.key,
            context={
                "observed": evaluation.observed,
                "maximum": evaluation.maximum,
                "unit": evaluation.unit.value,
                "custom_unit": evaluation.custom_unit,
                "exceeded_by": evaluation.exceeded_by,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the constraint to deterministic JSON-ready data."""
        _announce("Serializing product limit constraint")

        return {
            "key": self.key,
            "maximum": self.maximum,
            "unit": self.unit.value,
            "description": self.description,
            "custom_unit": self.custom_unit,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "LimitConstraint":
        """Reconstruct a limit constraint from canonical mapping data."""
        _announce("Rehydrating product limit constraint")

        data = require_mapping(payload, field="limit_constraint")

        return cls(
            key=data.get("key"),
            maximum=data.get("maximum"),
            unit=LimitUnit.parse(data.get("unit")),
            description=data.get("description"),
            custom_unit=data.get("custom_unit"),
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True, slots=True)
class LimitEvaluation:
    """Immutable result of checking one observed quantity against a limit."""

    key: str
    observed: int
    maximum: int
    unit: LimitUnit
    allowed: bool
    remaining: int
    exceeded_by: int
    custom_unit: str | None = None

    def __post_init__(self) -> None:
        _announce("Validating product limit evaluation")

        object.__setattr__(self, "key", require_text(self.key, field="key"))
        object.__setattr__(
            self,
            "observed",
            _normalize_non_negative_int(self.observed, field="observed"),
        )
        object.__setattr__(
            self,
            "maximum",
            _normalize_non_negative_int(self.maximum, field="maximum"),
        )
        object.__setattr__(self, "unit", LimitUnit.parse(self.unit))
        object.__setattr__(
            self,
            "remaining",
            _normalize_non_negative_int(self.remaining, field="remaining"),
        )
        object.__setattr__(
            self,
            "exceeded_by",
            _normalize_non_negative_int(self.exceeded_by, field="exceeded_by"),
        )
        object.__setattr__(
            self,
            "custom_unit",
            optional_text(self.custom_unit, field="custom_unit"),
        )

        if self.unit is LimitUnit.CUSTOM and self.custom_unit is None:
            raise DomainInvariantError(
                "custom_unit is required when unit='custom'.",
                field="custom_unit",
                context={"key": self.key},
            )

        if self.unit is not LimitUnit.CUSTOM and self.custom_unit is not None:
            raise DomainInvariantError(
                "custom_unit may only be set when unit='custom'.",
                field="custom_unit",
                context={
                    "key": self.key,
                    "unit": self.unit.value,
                },
            )

        if not isinstance(self.allowed, bool):
            raise DomainValidationError(
                "allowed must be a boolean.",
                field="allowed",
                context={"received_type": type(self.allowed).__name__},
            )

        expected_allowed = self.observed <= self.maximum
        expected_remaining = max(0, self.maximum - self.observed)
        expected_exceeded_by = max(0, self.observed - self.maximum)

        if (
            self.allowed != expected_allowed
            or self.remaining != expected_remaining
            or self.exceeded_by != expected_exceeded_by
        ):
            raise DomainInvariantError(
                "Limit evaluation fields are internally inconsistent.",
                field="limit_evaluation",
                context={"key": self.key},
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this evaluation to JSON-ready data."""
        _announce("Serializing product limit evaluation")

        return {
            "key": self.key,
            "observed": self.observed,
            "maximum": self.maximum,
            "unit": self.unit.value,
            "custom_unit": self.custom_unit,
            "allowed": self.allowed,
            "remaining": self.remaining,
            "exceeded_by": self.exceeded_by,
        }


@dataclass(frozen=True, slots=True)
class ProductLimits:
    """
    Immutable collection of configured constraints for one product/tier.

    A ``tier_code`` of ``None`` denotes product-wide limits. Tier-specific
    values may be represented by a separate ``ProductLimits`` instance and can
    be selected by the application layer after catalog/tier resolution.
    """

    product_code: ProductCode
    constraints: tuple[LimitConstraint, ...]
    tier_code: str | None = None

    def __post_init__(self) -> None:
        _announce("Validating product limits")

        object.__setattr__(
            self,
            "product_code",
            ProductCode.parse(self.product_code),
        )
        object.__setattr__(
            self,
            "tier_code",
            optional_text(self.tier_code, field="tier_code"),
        )

        constraints = self._normalize_constraints(self.constraints)
        seen: set[str] = set()

        for constraint in constraints:
            if constraint.key in seen:
                raise DomainInvariantError(
                    "Product limits contain a duplicate constraint key.",
                    field="constraints",
                    context={
                        "product_code": self.product_code.value,
                        "tier_code": self.tier_code,
                        "key": constraint.key,
                    },
                )
            seen.add(constraint.key)

        object.__setattr__(self, "constraints", constraints)

    @staticmethod
    def _normalize_constraints(
        values: Iterable[LimitConstraint] | None,
    ) -> tuple[LimitConstraint, ...]:
        _announce("Normalizing product limit constraints")

        if values is None:
            return ()

        if isinstance(values, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError(
                "constraints must be an iterable of LimitConstraint objects.",
                field="constraints",
                context={"received_type": type(values).__name__},
            )

        try:
            iterator = iter(values)
        except TypeError as exc:
            raise DomainValidationError(
                "constraints must be iterable.",
                field="constraints",
                context={"received_type": type(values).__name__},
            ) from exc

        result: list[LimitConstraint] = []
        for index, constraint in enumerate(iterator):
            if not isinstance(constraint, LimitConstraint):
                raise DomainValidationError(
                    "constraints must contain only LimitConstraint objects.",
                    field=f"constraints[{index}]",
                    context={"received_type": type(constraint).__name__},
                )
            result.append(constraint)

        return tuple(result)

    def assert_catalog_membership(self, catalog: ProductCatalog) -> None:
        """Validate that this product/tier exists in the supplied catalog."""
        _announce("Validating product-limit catalog membership")

        if not isinstance(catalog, ProductCatalog):
            raise DomainValidationError(
                "catalog must be a ProductCatalog.",
                field="catalog",
                context={"received_type": type(catalog).__name__},
            )

        catalog.get_product(self.product_code)

        if self.tier_code is not None:
            catalog.get_tier(self.product_code, self.tier_code)

    def get(self, key: str) -> LimitConstraint:
        """Return one configured limit constraint by key."""
        _announce("Looking up product limit constraint")

        normalized_key = require_text(key, field="key")

        for constraint in self.constraints:
            if constraint.key == normalized_key:
                return constraint

        raise DomainValidationError(
            "Product limit is not configured.",
            field="key",
            context={
                "product_code": self.product_code.value,
                "tier_code": self.tier_code,
                "key": normalized_key,
            },
        )

    def evaluate(self, usage: Mapping[str, int]) -> tuple[LimitEvaluation, ...]:
        """
        Evaluate supplied usage against configured constraints.

        Every configured constraint must have an observed value. Unknown usage
        keys are rejected so caller typos cannot silently bypass or distort
        commercial scope checks.
        """
        _announce("Evaluating product usage against limits")

        values = require_mapping(usage, field="usage")
        configured = {constraint.key: constraint for constraint in self.constraints}
        supplied_keys = set(values)
        configured_keys = set(configured)

        unknown = sorted(supplied_keys - configured_keys)
        missing = sorted(configured_keys - supplied_keys)

        if unknown:
            raise DomainValidationError(
                "Usage contains unconfigured limit keys.",
                field="usage",
                context={"unknown_keys": tuple(unknown)},
            )

        if missing:
            raise DomainValidationError(
                "Usage is missing configured limit keys.",
                field="usage",
                context={"missing_keys": tuple(missing)},
            )

        return tuple(
            constraint.evaluate(values[constraint.key])
            for constraint in self.constraints
        )

    def assert_within(self, usage: Mapping[str, int]) -> None:
        """Raise when any supplied usage quantity exceeds its configured limit."""
        _announce("Enforcing product usage limits")

        evaluations = self.evaluate(usage)
        failures = tuple(item for item in evaluations if not item.allowed)

        if not failures:
            return

        raise DomainInvariantError(
            "One or more configured product limits were exceeded.",
            field="usage",
            context={
                "product_code": self.product_code.value,
                "tier_code": self.tier_code,
                "violations": tuple(
                    {
                        "key": item.key,
                        "observed": item.observed,
                        "maximum": item.maximum,
                        "unit": item.unit.value,
                        "custom_unit": item.custom_unit,
                        "exceeded_by": item.exceeded_by,
                    }
                    for item in failures
                ),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize product limits into deterministic JSON-ready data."""
        _announce("Serializing product limits")

        return {
            "product_code": self.product_code.value,
            "tier_code": self.tier_code,
            "constraints": [
                constraint.to_dict() for constraint in self.constraints
            ],
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ProductLimits":
        """Reconstruct configured product limits from mapping data."""
        _announce("Rehydrating product limits")

        data = require_mapping(payload, field="product_limits")
        raw_constraints = data.get("constraints") or ()

        if isinstance(raw_constraints, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError(
                "constraints must be a sequence.",
                field="constraints",
            )

        try:
            constraints = tuple(
                item
                if isinstance(item, LimitConstraint)
                else LimitConstraint.from_dict(item)
                for item in raw_constraints
            )
        except TypeError as exc:
            raise DomainValidationError(
                "constraints must be iterable.",
                field="constraints",
            ) from exc

        return cls(
            product_code=ProductCode.parse(data.get("product_code")),
            tier_code=data.get("tier_code"),
            constraints=constraints,
        )


# Backward-compatible alias for the original scaffold placeholder. There is no
# duplicate wrapper class or second limit implementation.
Limits = ProductLimits


__all__ = [
    "LimitUnit",
    "LimitConstraint",
    "LimitEvaluation",
    "ProductLimits",
    "Limits",
]