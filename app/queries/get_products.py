"""
Read-only application query for the configured BIMAP product catalog.

Product configuration is injected by bootstrap/composition as canonical
``ProductCatalog`` and ``ProductLimits`` objects.  This module never opens YAML
files, hard-codes prices or limits, or duplicates the domain's catalog and limit
validation rules.

The query returns an immutable application projection for each configured
product: its canonical definition, configured tiers, and the exact product/tier
limit sets supplied by composition.  Limit scopes are intentionally not merged
or assigned precedence here; such policy belongs to the application service
that evaluates a concrete order/usage context.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.products.limits import ProductLimits
from ...domain.products.models import *
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Get Products Query")
printer = PrettyPrinter()

_COMPONENT = "get_products_query"


@dataclass(frozen=True, slots=True)
class ProductView:
    """Immutable application-facing projection of one configured BIMAP product."""

    product: ProductDefinition
    tiers: tuple[ProductTier, ...] = ()
    limits: tuple[ProductLimits, ...] = ()

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating product query view",
            event="get_products_query_view_validate_start",
            context={"product_code": getattr(getattr(self.product, "code", None), "value", None)},
        )
        if not isinstance(self.product, ProductDefinition):
            raise AppValidationError(
                "product must be a ProductDefinition.",
                component=_COMPONENT,
                operation="validate_view",
                field="product",
                context={"received_type": type(self.product).__name__},
            )
        if not isinstance(self.tiers, tuple):
            raise AppValidationError(
                "tiers must be a tuple of ProductTier values.",
                component=_COMPONENT,
                operation="validate_view",
                field="tiers",
                context={"received_type": type(self.tiers).__name__},
            )
        if not isinstance(self.limits, tuple):
            raise AppValidationError(
                "limits must be a tuple of ProductLimits values.",
                component=_COMPONENT,
                operation="validate_view",
                field="limits",
                context={"received_type": type(self.limits).__name__},
            )

        for index, tier in enumerate(self.tiers):
            if not isinstance(tier, ProductTier):
                raise AppValidationError(
                    "tiers contains a non-ProductTier value.",
                    component=_COMPONENT,
                    operation="validate_view",
                    field=f"tiers[{index}]",
                    context={"received_type": type(tier).__name__},
                )
            if tier.product_code is not self.product.code:
                raise AppIntegrityError(
                    "Product view contains a tier owned by another product.",
                    component=_COMPONENT,
                    operation="validate_view",
                    field=f"tiers[{index}].product_code",
                    context={
                        "product_code": self.product.code.value,
                        "tier_product_code": tier.product_code.value,
                    },
                )

        for index, configured in enumerate(self.limits):
            if not isinstance(configured, ProductLimits):
                raise AppValidationError(
                    "limits contains a non-ProductLimits value.",
                    component=_COMPONENT,
                    operation="validate_view",
                    field=f"limits[{index}]",
                    context={"received_type": type(configured).__name__},
                )
            if configured.product_code is not self.product.code:
                raise AppIntegrityError(
                    "Product view contains limits owned by another product.",
                    component=_COMPONENT,
                    operation="validate_view",
                    field=f"limits[{index}].product_code",
                    context={
                        "product_code": self.product.code.value,
                        "limits_product_code": configured.product_code.value,
                    },
                )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-ready product configuration data."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing product query view",
            event="get_products_query_view_to_dict_start",
            context={"product_code": self.product.code.value},
        )
        return {
            "product": self.product.to_dict(),
            "tiers": [tier.to_dict() for tier in self.tiers],
            "limits": [configured.to_dict() for configured in self.limits],
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Encode this view using BIMAP's canonical application JSON rules."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Encoding product query view JSON",
            event="get_products_query_view_to_json_start",
            context={"product_code": self.product.code.value},
        )
        return canonical_app_json(self.to_dict(), pretty=pretty)


class GetProducts:
    """Expose the canonical product catalog supplied by bootstrap/composition."""

    def __init__(
        self,
        catalog: ProductCatalog,
        *,
        product_limits: Iterable[ProductLimits] = (),
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing get-products query",
            event="get_products_query_init_start",
        )
        if not isinstance(catalog, ProductCatalog):
            raise AppConfigurationError(
                "catalog must be a ProductCatalog.",
                component=_COMPONENT,
                operation="initialize",
                field="catalog",
                context={"received_type": type(catalog).__name__},
            )
        if isinstance(product_limits, (str, bytes, bytearray, Mapping)):
            raise AppConfigurationError(
                "product_limits must be an iterable of ProductLimits.",
                component=_COMPONENT,
                operation="initialize",
                field="product_limits",
                context={"received_type": type(product_limits).__name__},
            )
        try:
            limits = tuple(product_limits)
        except TypeError as exc:
            raise AppConfigurationError(
                "product_limits must be iterable.",
                component=_COMPONENT,
                operation="initialize",
                field="product_limits",
                context={"received_type": type(product_limits).__name__},
                cause=exc,
            ) from exc

        seen: set[tuple[ProductCode, str | None]] = set()
        for index, configured in enumerate(limits):
            if not isinstance(configured, ProductLimits):
                raise AppConfigurationError(
                    "product_limits contains a non-ProductLimits value.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"product_limits[{index}]",
                    context={"received_type": type(configured).__name__},
                )
            try:
                configured.assert_catalog_membership(catalog)
            except DomainError as exc:
                raise AppConfigurationError(
                    "Configured product limits do not belong to the supplied catalog.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=f"product_limits[{index}]",
                    context=lower_error_context(exc),
                    cause=exc,
                ) from exc

            key = (configured.product_code, configured.tier_code)
            if key in seen:
                raise AppConfigurationError(
                    "Duplicate ProductLimits configuration for one product/tier scope.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="product_limits",
                    context={
                        "product_code": configured.product_code.value,
                        "tier_code": configured.tier_code,
                    },
                )
            seen.add(key)

        self.catalog = catalog
        self.product_limits = limits
        logger.info(
            {
                "event": "get_products_query_initialized",
                "configured_product_count": len(catalog.products),
                "configured_tier_count": len(catalog.tiers),
                "configured_limit_scope_count": len(limits),
            }
        )

    def _build_view(self, product: ProductDefinition) -> ProductView:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building configured product view",
            event="get_products_query_build_view_start",
            context={"product_code": product.code.value},
        )
        tiers = self.catalog.tiers_for(product.code)
        limits = tuple(
            configured
            for configured in self.product_limits
            if configured.product_code is product.code
        )
        return ProductView(product=product, tiers=tiers, limits=limits)

    def execute(self) -> tuple[ProductView, ...]:
        """Return all configured products in canonical catalog order."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing get-products query",
            event="get_products_query_execute_start",
        )
        result = tuple(self._build_view(product) for product in self.catalog.products)
        logger.info(
            {
                "event": "get_products_query_completed",
                "product_count": len(result),
            }
        )
        return result

    def get(self, product_code: ProductCode | str) -> ProductView:
        """Return one configured product view by canonical product code."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Getting configured product",
            event="get_products_query_get_start",
            context={"product_code": getattr(product_code, "value", product_code)},
        )
        try:
            product = self.catalog.get_product(product_code)
        except DomainError as exc:
            raise AppValidationError(
                "Requested product is not configured in the BIMAP catalog.",
                component=_COMPONENT,
                operation="get",
                field="product_code",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        return self._build_view(product)


class GetProduct:
    """Backward-compatible singular query facade over ``GetProducts``."""

    def __init__(
        self,
        catalog: ProductCatalog,
        *,
        product_limits: Iterable[ProductLimits] = (),
    ) -> None:
        self._query = GetProducts(catalog, product_limits=product_limits)

    def execute(self, product_code: ProductCode | str) -> ProductView:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing singular get-product query",
            event="get_product_query_execute_start",
            context={"product_code": getattr(product_code, "value", product_code)},
        )
        return self._query.get(product_code)


__all__ = ["ProductView", "GetProducts", "GetProduct"]