"""
Canonical BIMAP product-domain models.

This module defines product identity, product scope, configured commercial
offerings, tiers, and catalog integrity. It deliberately contains no hard-coded
prices, file-count limits, or upload-size limits: the current product
configuration is not yet populated, and the implementation report describes
launch pricing as proposed test bands rather than final contractual values.

Dependency direction
--------------------
domain.utils
    ↑
products/models.py
    ↑
products/limits.py

``models.py`` must never import ``limits.py``. Product-limit definitions depend
on product identity, not the reverse.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from ..utils.domain_errors import *
from ..utils.domain_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Domain Products Models")
printer = PrettyPrinter()


def _announce(action: str) -> None:
    """Emit a lightweight method-start diagnostic without customer content."""
    printer.status("PRODUCTS", action, "info")
    logger.debug(action)


class ProductCode(str, Enum):
    """
    Stable internal identifiers for the three BIMAP customer-facing products.

    The values intentionally remain short implementation identifiers while the
    customer-facing names are separately configurable through
    :class:`ProductDefinition`.
    """

    FAMILY_AUDIT = "family_audit"
    BIM_QA = "bim_qa"
    COMBINED_AUDIT = "combined_audit"

    @classmethod
    def parse(cls, value: Any) -> "ProductCode":
        """Normalize a supported value into ``ProductCode``."""
        _announce("Parsing product code")

        if isinstance(value, cls):
            return value

        normalized = require_text(value, field="product_code").lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            logger.warning("Unsupported BIMAP product code received: %s", normalized)
            raise DomainValidationError(
                "Unsupported BIMAP product code.",
                field="product_code",
                context={
                    "received": normalized,
                    "allowed": tuple(code.value for code in cls),
                },
            ) from exc

    def __str__(self) -> str:
        return self.value


class ProductScope(str, Enum):
    """Primary evidence scope associated with a BIMAP product."""

    FAMILY = "family"
    PROJECT = "project"
    COMBINED = "combined"

    @classmethod
    def parse(cls, value: Any) -> "ProductScope":
        """Normalize a supported value into ``ProductScope``."""
        _announce("Parsing product scope")

        if isinstance(value, cls):
            return value

        normalized = require_text(value, field="scope").lower()

        try:
            return cls(normalized)
        except ValueError as exc:
            logger.warning("Unsupported BIMAP product scope received: %s", normalized)
            raise DomainValidationError(
                "Unsupported BIMAP product scope.",
                field="scope",
                context={
                    "received": normalized,
                    "allowed": tuple(scope.value for scope in cls),
                },
            ) from exc

    def __str__(self) -> str:
        return self.value


EXPECTED_SCOPE_BY_PRODUCT: Mapping[ProductCode, ProductScope] = {
    ProductCode.FAMILY_AUDIT: ProductScope.FAMILY,
    ProductCode.BIM_QA: ProductScope.PROJECT,
    ProductCode.COMBINED_AUDIT: ProductScope.COMBINED,
}

CANONICAL_PRODUCT_NAMES: Mapping[ProductCode, str] = {
    ProductCode.FAMILY_AUDIT: "R3D Family Audit",
    ProductCode.BIM_QA: "R3D BIM QA",
    ProductCode.COMBINED_AUDIT: "R3D Combined Audit",
}


def _normalize_price(value: Any, *, field: str) -> Decimal | None:
    """
    Normalize an optional non-negative finite decimal price.

    Boolean values are rejected explicitly because ``bool`` subclasses ``int``.
    The function does not impose currency-specific decimal-place rules;
    provider-specific settlement belongs to the payment layer.
    """
    _announce("Normalizing product price")

    if value is None:
        return None

    if isinstance(value, bool):
        raise DomainValidationError(
            "Price must be numeric, not boolean.",
            field=field,
        )

    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DomainValidationError(
            "Price must be a valid decimal number.",
            field=field,
            context={"received_type": type(value).__name__},
        ) from exc

    if not decimal_value.is_finite():
        raise DomainValidationError("Price must be finite.", field=field)

    if decimal_value < 0:
        raise DomainValidationError(
            "Price must not be negative.",
            field=field,
            context={"received": str(decimal_value)},
        )

    return decimal_value


def _normalize_currency(value: Any, *, field: str, required: bool) -> str | None:
    """
    Normalize an optional three-letter currency code.

    This validates shape only; it deliberately does not duplicate an ISO
    currency registry inside the product domain.
    """
    _announce("Normalizing product currency")

    if value is None:
        if required:
            raise DomainValidationError(
                "Currency is required when a price is configured.",
                field=field,
            )
        return None

    normalized = require_text(value, field=field, max_length=3).upper()

    if len(normalized) != 3 or not normalized.isalpha():
        raise DomainValidationError(
            "Currency must be a three-letter alphabetic code.",
            field=field,
            context={"received": normalized},
        )

    return normalized


@dataclass(frozen=True, slots=True)
class ProductDefinition:
    """
    Canonical definition of one BIMAP product.

    ``input_groups`` and ``output_artifacts`` are descriptive/configured
    identifiers. This model does not perform file-format validation; accepted
    formats and upload security remain concerns of upload/infrastructure layers.
    """

    code: ProductCode
    display_name: str
    scope: ProductScope

    description: str | None = None
    input_groups: tuple[str, ...] = ()
    output_artifacts: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        _announce("Validating product definition")

        code = ProductCode.parse(self.code)
        scope = ProductScope.parse(self.scope)
        expected_scope = EXPECTED_SCOPE_BY_PRODUCT[code]

        if scope is not expected_scope:
            raise DomainInvariantError(
                "Product scope does not match the canonical BIMAP product.",
                field="scope",
                context={
                    "product_code": code.value,
                    "expected_scope": expected_scope.value,
                    "received_scope": scope.value,
                },
            )

        object.__setattr__(self, "code", code)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(
            self,
            "display_name",
            require_text(self.display_name, field="display_name"),
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, field="description"),
        )
        object.__setattr__(
            self,
            "input_groups",
            stable_unique_text(self.input_groups, field="input_groups"),
        )
        object.__setattr__(
            self,
            "output_artifacts",
            stable_unique_text(self.output_artifacts, field="output_artifacts"),
        )

        source_metadata = require_mapping(self.metadata, field="metadata")
        frozen_metadata = freeze_json_value(source_metadata, field="metadata")

        if not isinstance(frozen_metadata, Mapping):
            raise DomainValidationError(
                "Product metadata normalization did not produce a mapping.",
                field="metadata",
            )

        object.__setattr__(self, "metadata", frozen_metadata)

    @classmethod
    def canonical(
        cls,
        code: ProductCode | str,
        *,
        description: str | None = None,
        input_groups: Iterable[str] | None = None,
        output_artifacts: Iterable[str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ProductDefinition":
        """
        Create a product using the report-defined customer-facing name/scope.

        This supplies only identity and naming established by the implementation
        report. It does not invent price, tier, or limit values.
        """
        _announce("Creating canonical product definition")

        normalized_code = ProductCode.parse(code)

        return cls(
            code=normalized_code,
            display_name=CANONICAL_PRODUCT_NAMES[normalized_code],
            scope=EXPECTED_SCOPE_BY_PRODUCT[normalized_code],
            description=description,
            input_groups=input_groups or (),
            output_artifacts=output_artifacts or (),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the product definition to deterministic JSON-ready data."""
        _announce("Serializing product definition")

        return {
            "code": self.code.value,
            "display_name": self.display_name,
            "scope": self.scope.value,
            "description": self.description,
            "input_groups": list(self.input_groups),
            "output_artifacts": list(self.output_artifacts),
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ProductDefinition":
        """Reconstruct a product definition from canonical mapping data."""
        _announce("Rehydrating product definition")

        data = require_mapping(payload, field="product")

        return cls(
            code=ProductCode.parse(data.get("code")),
            display_name=data.get("display_name"),
            scope=ProductScope.parse(data.get("scope")),
            description=data.get("description"),
            input_groups=data.get("input_groups") or (),
            output_artifacts=data.get("output_artifacts") or (),
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True, slots=True)
class ProductTier:
    """
    Configured commercial tier belonging to one BIMAP product.

    Prices are optional at the domain-model level because a deployment may need
    draft/unpriced tiers during configuration. When a price exists, a currency
    is mandatory. No values are hard-coded from proposed pricing test bands.
    """

    product_code: ProductCode
    tier_code: str
    display_name: str

    description: str | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        _announce("Validating product tier")

        object.__setattr__(self, "product_code", ProductCode.parse(self.product_code))
        object.__setattr__(
            self,
            "tier_code",
            require_text(self.tier_code, field="tier_code"),
        )
        object.__setattr__(
            self,
            "display_name",
            require_text(self.display_name, field="display_name"),
        )
        object.__setattr__(
            self,
            "description",
            optional_text(self.description, field="description"),
        )

        normalized_price = _normalize_price(self.unit_price, field="unit_price")
        normalized_currency = _normalize_currency(
            self.currency,
            field="currency",
            required=normalized_price is not None,
        )

        if normalized_price is None and normalized_currency is not None:
            raise DomainInvariantError(
                "Currency cannot be configured without a unit price.",
                field="currency",
                context={"tier_code": self.tier_code},
            )

        object.__setattr__(self, "unit_price", normalized_price)
        object.__setattr__(self, "currency", normalized_currency)

        source_metadata = require_mapping(self.metadata, field="metadata")
        frozen_metadata = freeze_json_value(source_metadata, field="metadata")

        if not isinstance(frozen_metadata, Mapping):
            raise DomainValidationError(
                "Tier metadata normalization did not produce a mapping.",
                field="metadata",
            )

        object.__setattr__(self, "metadata", frozen_metadata)

    @property
    def key(self) -> tuple[ProductCode, str]:
        """Return the stable product/tier composite key."""
        return self.product_code, self.tier_code

    @property
    def is_priced(self) -> bool:
        """Return whether this tier has an explicit configured price."""
        return self.unit_price is not None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this tier to deterministic JSON-ready data."""
        _announce("Serializing product tier")

        return {
            "product_code": self.product_code.value,
            "tier_code": self.tier_code,
            "display_name": self.display_name,
            "description": self.description,
            "unit_price": (
                format(self.unit_price, "f") if self.unit_price is not None else None
            ),
            "currency": self.currency,
            "metadata": thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ProductTier":
        """Reconstruct a tier from canonical mapping data."""
        _announce("Rehydrating product tier")

        data = require_mapping(payload, field="product_tier")

        return cls(
            product_code=ProductCode.parse(data.get("product_code")),
            tier_code=data.get("tier_code"),
            display_name=data.get("display_name"),
            description=data.get("description"),
            unit_price=data.get("unit_price"),
            currency=data.get("currency"),
            metadata=data.get("metadata") or {},
        )


@dataclass(frozen=True, slots=True)
class ProductCatalog:
    """
    Immutable BIMAP product catalog.

    The catalog centralizes uniqueness and product/tier referential integrity so
    API, order, checkout, and reporting layers do not duplicate those checks.
    """

    products: tuple[ProductDefinition, ...]
    tiers: tuple[ProductTier, ...] = ()

    def __post_init__(self) -> None:
        _announce("Validating product catalog")

        products = self._normalize_products(self.products)
        tiers = self._normalize_tiers(self.tiers)

        product_codes: set[ProductCode] = set()
        for product in products:
            if product.code in product_codes:
                raise DomainInvariantError(
                    "Product catalog contains duplicate product codes.",
                    field="products",
                    context={"product_code": product.code.value},
                )
            product_codes.add(product.code)

        tier_keys: set[tuple[ProductCode, str]] = set()
        for tier in tiers:
            if tier.product_code not in product_codes:
                raise DomainInvariantError(
                    "Product tier references a product absent from the catalog.",
                    field="tiers",
                    context={
                        "product_code": tier.product_code.value,
                        "tier_code": tier.tier_code,
                    },
                )

            if tier.key in tier_keys:
                raise DomainInvariantError(
                    "Product catalog contains a duplicate tier key.",
                    field="tiers",
                    context={
                        "product_code": tier.product_code.value,
                        "tier_code": tier.tier_code,
                    },
                )

            tier_keys.add(tier.key)

        object.__setattr__(self, "products", products)
        object.__setattr__(self, "tiers", tiers)

    @staticmethod
    def _normalize_products(
        values: Iterable[ProductDefinition] | None,
    ) -> tuple[ProductDefinition, ...]:
        _announce("Normalizing product definitions")

        if values is None:
            return ()

        if isinstance(values, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError(
                "products must be an iterable of ProductDefinition objects.",
                field="products",
                context={"received_type": type(values).__name__},
            )

        try:
            iterator = iter(values)
        except TypeError as exc:
            raise DomainValidationError(
                "products must be iterable.",
                field="products",
                context={"received_type": type(values).__name__},
            ) from exc

        result: list[ProductDefinition] = []
        for index, product in enumerate(iterator):
            if not isinstance(product, ProductDefinition):
                raise DomainValidationError(
                    "products must contain only ProductDefinition objects.",
                    field=f"products[{index}]",
                    context={"received_type": type(product).__name__},
                )
            result.append(product)

        return tuple(result)

    @staticmethod
    def _normalize_tiers(
        values: Iterable[ProductTier] | None,
    ) -> tuple[ProductTier, ...]:
        _announce("Normalizing product tiers")

        if values is None:
            return ()

        if isinstance(values, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError(
                "tiers must be an iterable of ProductTier objects.",
                field="tiers",
                context={"received_type": type(values).__name__},
            )

        try:
            iterator = iter(values)
        except TypeError as exc:
            raise DomainValidationError(
                "tiers must be iterable.",
                field="tiers",
                context={"received_type": type(values).__name__},
            ) from exc

        result: list[ProductTier] = []
        for index, tier in enumerate(iterator):
            if not isinstance(tier, ProductTier):
                raise DomainValidationError(
                    "tiers must contain only ProductTier objects.",
                    field=f"tiers[{index}]",
                    context={"received_type": type(tier).__name__},
                )
            result.append(tier)

        return tuple(result)

    def get_product(self, code: ProductCode | str) -> ProductDefinition:
        """Return one configured product or raise a validation error."""
        _announce("Looking up product definition")

        normalized_code = ProductCode.parse(code)

        for product in self.products:
            if product.code is normalized_code:
                return product

        raise DomainValidationError(
            "Product is not configured in this catalog.",
            field="product_code",
            context={"product_code": normalized_code.value},
        )

    def get_tier(
        self,
        product_code: ProductCode | str,
        tier_code: str,
    ) -> ProductTier:
        """Return one configured product tier."""
        _announce("Looking up product tier")

        normalized_product = ProductCode.parse(product_code)
        normalized_tier = require_text(tier_code, field="tier_code")

        for tier in self.tiers:
            if tier.product_code is normalized_product and tier.tier_code == normalized_tier:
                return tier

        raise DomainValidationError(
            "Product tier is not configured in this catalog.",
            field="tier_code",
            context={
                "product_code": normalized_product.value,
                "tier_code": normalized_tier,
            },
        )

    def tiers_for(self, product_code: ProductCode | str) -> tuple[ProductTier, ...]:
        """Return all configured tiers for one product, preserving catalog order."""
        _announce("Listing product tiers")

        normalized_product = ProductCode.parse(product_code)
        self.get_product(normalized_product)

        return tuple(
            tier for tier in self.tiers if tier.product_code is normalized_product
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the catalog to deterministic JSON-ready data."""
        _announce("Serializing product catalog")

        return {
            "products": [product.to_dict() for product in self.products],
            "tiers": [tier.to_dict() for tier in self.tiers],
        }

    @classmethod
    def from_dict(cls, payload: Any) -> "ProductCatalog":
        """Reconstruct a catalog from canonical mapping data."""
        _announce("Rehydrating product catalog")

        data = require_mapping(payload, field="product_catalog")
        raw_products = data.get("products") or ()
        raw_tiers = data.get("tiers") or ()

        if isinstance(raw_products, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError("products must be a sequence.", field="products")
        if isinstance(raw_tiers, (str, bytes, bytearray, Mapping)):
            raise DomainValidationError("tiers must be a sequence.", field="tiers")

        try:
            products = tuple(
                item if isinstance(item, ProductDefinition) else ProductDefinition.from_dict(item)
                for item in raw_products
            )
            tiers = tuple(
                item if isinstance(item, ProductTier) else ProductTier.from_dict(item)
                for item in raw_tiers
            )
        except TypeError as exc:
            raise DomainValidationError(
                "Product catalog collections must be iterable.",
                field="product_catalog",
            ) from exc

        return cls(products=products, tiers=tiers)


# Backward-compatible alias for the original scaffold placeholder. There is one
# canonical model implementation rather than a duplicate wrapper class.
ProductModels = ProductDefinition


__all__ = [
    "ProductCode",
    "ProductScope",
    "EXPECTED_SCOPE_BY_PRODUCT",
    "CANONICAL_PRODUCT_NAMES",
    "ProductDefinition",
    "ProductTier",
    "ProductCatalog",
    "ProductModels",
]