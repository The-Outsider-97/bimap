"""FastAPI route groups for BIMAP."""

from ._shared import *
from .admin import *
from .checkout import *
from .deletion import *
from .downloads import *
from .health import *
from .orders import *
from .products import *
from .reports import *
from .uploads import *
from .webhooks import *

from ._shared import __all__ as _shared_exports
from .admin import __all__ as _admin_exports
from .checkout import __all__ as _checkout_exports
from .deletion import __all__ as _deletion_exports
from .downloads import __all__ as _downloads_exports
from .health import __all__ as _health_exports
from .orders import __all__ as _orders_exports
from .products import __all__ as _products_exports
from .reports import __all__ as _reports_exports
from .uploads import __all__ as _uploads_exports
from .webhooks import __all__ as _webhooks_exports

__all__ = [
    *_shared_exports,
    *_admin_exports,
    *_checkout_exports,
    *_deletion_exports,
    *_downloads_exports,
    *_health_exports,
    *_orders_exports,
    *_products_exports,
    *_reports_exports,
    *_uploads_exports,
    *_webhooks_exports,
]  # type: ignore
