"""Application query handlers and immutable query projections."""

from .get_audit_status import *
from .get_order import *
from .get_products import *
from .list_orders import *
from .list_reports import *


from .get_audit_status import __all__ as _get_audit_status_exports
from .get_order import __all__ as _get_order_exports
from .get_products import __all__ as _get_products_exports
from .list_orders import __all__ as _list_orders_exports
from .list_reports import __all__ as _list_reports_exports


__all__ = [
    *_get_audit_status_exports,
    *_get_order_exports,
    *_get_products_exports,
    *_list_orders_exports,
    *_list_reports_exports,
] # type: ignore