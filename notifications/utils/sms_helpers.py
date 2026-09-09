"""Validation and SMS-length helpers for BIMAP notifications."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ...app.utils.app_helpers import optional_app_text, require_app_text
from .sms_errors import SMSValidationError


_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_SMS_CODE_RE = re.compile(r"^\d{6}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")

_GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ "
    "!\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM7_EXTENDED = frozenset("^{}\\[~]|€\f")


@dataclass(frozen=True, slots=True)
class SMSLengthInfo:
    encoding: str
    units: int
    segments: int
    single_segment_capacity: int
    multipart_segment_capacity: int


def require_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    allow_newlines: bool = True,
) -> str:
    text = require_app_text(
        value,
        field=field,
        error_type=SMSValidationError,
        component="sms_helpers",
        operation="require_text",
        max_length=max_length,
    )
    if not allow_newlines and ("\r" in text or "\n" in text):
        raise SMSValidationError(
            "Value must not contain line breaks.",
            component="sms_helpers",
            operation="require_text",
            field=field,
        )
    if _CONTROL_RE.search(text):
        raise SMSValidationError(
            "Value contains unsupported control characters.",
            component="sms_helpers",
            operation="require_text",
            field=field,
        )
    return text


def optional_text(
    value: Any,
    *,
    field: str,
    max_length: int,
    allow_newlines: bool = True,
) -> str | None:
    if value is None:
        return None
    text = optional_app_text(
        value,
        field=field,
        error_type=SMSValidationError,
        component="sms_helpers",
        operation="optional_text",
        max_length=max_length,
    )
    if text is None:
        return None
    return require_text(
        text,
        field=field,
        max_length=max_length,
        allow_newlines=allow_newlines,
    )


def compact_sms_text(value: Any, *, field: str, max_length: int) -> str:
    text = require_text(value, field=field, max_length=max_length)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_e164_number(value: Any, *, field: str = "phone_e164") -> str:
    text = require_text(value, field=field, max_length=16, allow_newlines=False)
    if not _E164_RE.fullmatch(text):
        raise SMSValidationError(
            "Phone number must be canonical E.164 text, for example +31612345678.",
            component="sms_helpers",
            operation="normalize_e164_number",
            field=field,
        )
    return text


def mask_phone_number(value: Any) -> str:
    number = normalize_e164_number(value)
    visible_prefix = min(3, max(2, len(number) - 6))
    hidden = max(3, len(number) - visible_prefix - 2)
    return f"{number[:visible_prefix]}{'*' * hidden}{number[-2:]}"


def require_sms_verification_code(value: Any) -> str:
    code = require_text(
        value,
        field="verification_code",
        max_length=6,
        allow_newlines=False,
    )
    if not _SMS_CODE_RE.fullmatch(code):
        raise SMSValidationError(
            "SMS verification code must contain exactly six digits.",
            component="sms_helpers",
            operation="require_sms_verification_code",
            field="verification_code",
        )
    return code


def normalize_action_url(value: Any, *, field: str = "action_url") -> str | None:
    text = optional_text(value, field=field, max_length=2048, allow_newlines=False)
    if text is None:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SMSValidationError(
            "Action URL must be an absolute HTTP or HTTPS URL.",
            component="sms_helpers",
            operation="normalize_action_url",
            field=field,
        )
    if parsed.username is not None or parsed.password is not None:
        raise SMSValidationError(
            "Action URL must not contain embedded credentials.",
            component="sms_helpers",
            operation="normalize_action_url",
            field=field,
        )
    return text


def analyze_sms_length(value: Any) -> SMSLengthInfo:
    text = require_text(value, field="body", max_length=10_000)
    gsm_units = 0
    gsm_compatible = True
    for character in text:
        if character in _GSM7_BASIC:
            gsm_units += 1
        elif character in _GSM7_EXTENDED:
            gsm_units += 2
        else:
            gsm_compatible = False
            break

    if gsm_compatible:
        segments = 1 if gsm_units <= 160 else math.ceil(gsm_units / 153)
        return SMSLengthInfo(
            encoding="gsm-7",
            units=gsm_units,
            segments=segments,
            single_segment_capacity=160,
            multipart_segment_capacity=153,
        )

    unicode_units = len(text.encode("utf-16-be")) // 2
    segments = 1 if unicode_units <= 70 else math.ceil(unicode_units / 67)
    return SMSLengthInfo(
        encoding="ucs-2",
        units=unicode_units,
        segments=segments,
        single_segment_capacity=70,
        multipart_segment_capacity=67,
    )


def ensure_sms_segment_limit(value: Any, *, max_segments: int) -> SMSLengthInfo:
    if isinstance(max_segments, bool) or not isinstance(max_segments, int) or not 1 <= max_segments <= 10:
        raise SMSValidationError(
            "max_segments must be an integer between 1 and 10.",
            component="sms_helpers",
            operation="ensure_sms_segment_limit",
            field="max_segments",
        )
    info = analyze_sms_length(value)
    if info.segments > max_segments:
        raise SMSValidationError(
            "Rendered SMS exceeds the configured segment limit.",
            component="sms_helpers",
            operation="ensure_sms_segment_limit",
            field="body",
            context={
                "encoding": info.encoding,
                "units": info.units,
                "segments": info.segments,
                "max_segments": max_segments,
            },
        )
    return info


__all__ = [
    "SMSLengthInfo",
    "require_text",
    "optional_text",
    "compact_sms_text",
    "normalize_e164_number",
    "mask_phone_number",
    "require_sms_verification_code",
    "normalize_action_url",
    "analyze_sms_length",
    "ensure_sms_segment_limit",
]
