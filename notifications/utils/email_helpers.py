"""Validation and normalization helpers for BIMAP email delivery."""

from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .email_errors import EmailConfigurationError, EmailValidationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Email Helpers")
printer = PrettyPrinter()

_MAX_EMAIL_LENGTH = 254
_MAX_LOCAL_LENGTH = 64
_MAX_DOMAIN_LENGTH = 253
_MAX_HEADER_LENGTH = 998
_MAX_TEXT_LENGTH = 16_384
_VERIFICATION_CODE_RE = re.compile(r"^[A-Za-z0-9]{24}$")
_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+$")
_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def require_text(
    value: Any,
    *,
    field: str,
    max_length: int = _MAX_TEXT_LENGTH,
    allow_newlines: bool = True,
) -> str:
    if not isinstance(value, str):
        raise EmailValidationError(
            "Value must be text.",
            component="email_helpers",
            operation="require_text",
            field=field,
            context={"received_type": type(value).__name__},
        )
    normalized = value.strip()
    if not normalized:
        raise EmailValidationError(
            "Value must not be empty.",
            component="email_helpers",
            operation="require_text",
            field=field,
        )
    if len(normalized) > max_length:
        raise EmailValidationError(
            "Value exceeds the allowed length.",
            component="email_helpers",
            operation="require_text",
            field=field,
            context={"max_length": max_length},
        )
    if _CONTROL_RE.search(normalized):
        raise EmailValidationError(
            "Value contains unsupported control characters.",
            component="email_helpers",
            operation="require_text",
            field=field,
        )
    if not allow_newlines and ("\r" in normalized or "\n" in normalized):
        raise EmailValidationError(
            "Value must not contain line breaks.",
            component="email_helpers",
            operation="require_text",
            field=field,
        )
    return normalized


def optional_text(
    value: Any,
    *,
    field: str,
    max_length: int = _MAX_TEXT_LENGTH,
    allow_newlines: bool = True,
) -> str | None:
    if value is None:
        return None
    return require_text(
        value,
        field=field,
        max_length=max_length,
        allow_newlines=allow_newlines,
    )


def normalize_email_address(value: Any, *, field: str = "email") -> str:
    address = require_text(
        value,
        field=field,
        max_length=_MAX_EMAIL_LENGTH,
        allow_newlines=False,
    )
    if address.count("@") != 1:
        raise EmailValidationError(
            "Email address must contain one @ separator.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )

    local, domain = address.rsplit("@", 1)
    if not local or len(local) > _MAX_LOCAL_LENGTH or not _LOCAL_RE.fullmatch(local):
        raise EmailValidationError(
            "Email address has an invalid local part.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise EmailValidationError(
            "Email address has an invalid local part.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )

    domain = domain.rstrip(".")
    if not domain:
        raise EmailValidationError(
            "Email address has an invalid domain.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise EmailValidationError(
            "Email address domain cannot be normalized.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
            cause=exc,
        ) from exc

    if len(ascii_domain) > _MAX_DOMAIN_LENGTH:
        raise EmailValidationError(
            "Email address domain exceeds the allowed length.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )
    labels = ascii_domain.split(".")
    if any(not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels):
        raise EmailValidationError(
            "Email address has an invalid domain.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )

    normalized = f"{local}@{ascii_domain.casefold()}"
    if len(normalized) > _MAX_EMAIL_LENGTH:
        raise EmailValidationError(
            "Email address exceeds the allowed length.",
            component="email_helpers",
            operation="normalize_email_address",
            field=field,
        )
    return normalized


def normalize_display_name(value: Any, *, field: str = "display_name") -> str | None:
    return optional_text(value, field=field, max_length=128, allow_newlines=False)


def require_header_value(value: Any, *, field: str, max_length: int = _MAX_HEADER_LENGTH) -> str:
    return require_text(value, field=field, max_length=max_length, allow_newlines=False)


def require_verification_code(value: Any, *, field: str = "verification_code") -> str:
    code = require_text(value, field=field, max_length=24, allow_newlines=False)
    if not _VERIFICATION_CODE_RE.fullmatch(code):
        raise EmailValidationError(
            "Verification code must contain exactly 24 ASCII letters or digits.",
            component="email_helpers",
            operation="require_verification_code",
            field=field,
        )
    return code


def normalize_action_url(value: Any, *, field: str = "action_url") -> str | None:
    if value is None:
        return None
    raw = require_text(value, field=field, max_length=2048, allow_newlines=False)
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EmailValidationError(
            "Action URL must be an absolute HTTP(S) URL.",
            component="email_helpers",
            operation="normalize_action_url",
            field=field,
        )
    if parsed.username is not None or parsed.password is not None:
        raise EmailValidationError(
            "Action URL must not contain embedded credentials.",
            component="email_helpers",
            operation="normalize_action_url",
            field=field,
        )
    return urlunsplit(parsed)


def normalize_metadata_headers(value: Mapping[str, Any] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EmailValidationError(
            "Email headers must be a mapping.",
            component="email_helpers",
            operation="normalize_metadata_headers",
            field="headers",
            context={"received_type": type(value).__name__},
        )

    normalized: dict[str, str] = {}
    for key, item in value.items():
        name = require_header_value(key, field="headers.name", max_length=128)
        if not name.lower().startswith("x-bimap-"):
            raise EmailValidationError(
                "Only X-BIMAP-* extension headers are accepted.",
                component="email_helpers",
                operation="normalize_metadata_headers",
                field="headers",
                context={"header_name": name},
            )
        normalized[name] = require_header_value(item, field=f"headers[{name}]")
    return normalized


def mask_email_address(value: str) -> str:
    try:
        normalized = normalize_email_address(value)
    except EmailValidationError:
        return "<invalid-email>"
    local, domain = normalized.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" * max(1, len(local) - 1)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def html_text(value: Any, *, field: str, max_length: int = _MAX_TEXT_LENGTH) -> str:
    return escape(require_text(value, field=field, max_length=max_length), quote=True)


def parse_env_bool(value: str | None, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise EmailConfigurationError(
        "Environment setting must be boolean.",
        component="email_helpers",
        operation="parse_env_bool",
        field=field,
    )


def parse_env_int(
    value: str | None,
    *,
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError) as exc:
        raise EmailConfigurationError(
            "Environment setting must be an integer.",
            component="email_helpers",
            operation="parse_env_int",
            field=field,
            cause=exc,
        ) from exc
    if not minimum <= parsed <= maximum:
        raise EmailConfigurationError(
            "Environment setting is outside the allowed range.",
            component="email_helpers",
            operation="parse_env_int",
            field=field,
            context={"minimum": minimum, "maximum": maximum},
        )
    return parsed


def parse_env_float(
    value: str | None,
    *,
    field: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    if value is None:
        return default
    try:
        parsed = float(value.strip())
    except (TypeError, ValueError) as exc:
        raise EmailConfigurationError(
            "Environment setting must be numeric.",
            component="email_helpers",
            operation="parse_env_float",
            field=field,
            cause=exc,
        ) from exc
    if not minimum <= parsed <= maximum:
        raise EmailConfigurationError(
            "Environment setting is outside the allowed range.",
            component="email_helpers",
            operation="parse_env_float",
            field=field,
            context={"minimum": minimum, "maximum": maximum},
        )
    return parsed


__all__ = [
    "require_text",
    "optional_text",
    "normalize_email_address",
    "normalize_display_name",
    "require_header_value",
    "require_verification_code",
    "normalize_action_url",
    "normalize_metadata_headers",
    "mask_email_address",
    "html_text",
    "parse_env_bool",
    "parse_env_int",
    "parse_env_float",
]
