"""BIMAP transactional email notification subsystem."""

from .email_models import *
from .email_renderer import EmailRenderer
from .email_service import EmailService
from .email_templates import *


from .email_models import __all__ as _email_models_exports
from .email_templates import __all__ as _email_templates_exports

__all__ = [
    "EmailService",
    "EmailRenderer",
    *_email_models_exports,
    *_email_templates_exports,
] # type: ignore