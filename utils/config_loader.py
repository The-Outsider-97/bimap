"""
Canonical BIMAP YAML configuration loader.

This module is the sole low-level YAML I/O boundary for BIMAP's global,
retention, and SLAI-profile configuration surfaces.

Responsibilities
----------------
- resolve configuration paths relative to the BIMAP package root;
- parse YAML safely;
- perform structural/schema validation;
- cache configurations independently by resolved file path;
- detect file modification and TTL expiry;
- return defensive deep copies;
- fail closed on missing, malformed, or unsupported configuration.

Non-responsibilities
--------------------
This module does not:
- construct Bootstrap;
- construct APISettings or middleware policy objects;
- construct SLAI agents;
- calculate retention deadlines;
- load account-plan business policy from plans.yaml;
- persist runtime state;
- read secrets from YAML.

Typed configuration objects remain composition-root responsibilities.
Commercial account-plan loading remains owned by ``utils/plan_loader.py``.
"""

from __future__ import annotations

import copy
import threading
import time
import yaml  # type: ignore

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Config Loader")
printer = PrettyPrinter()

_COMPONENT = "config_loader"

# ---------------------------------------------------------------------------
# Canonical paths
# ---------------------------------------------------------------------------

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PACKAGE_ROOT / "configs"

BIMAP_CONFIG_PATH = CONFIG_DIR / "bimap.yaml"
RETENTION_CONFIG_PATH = CONFIG_DIR / "retention.yaml"
SLAI_PROFILE_CONFIG_PATH = CONFIG_DIR / "slai_profile.yaml"

# Backward-compatible public default.
DEFAULT_CONFIG_PATH = BIMAP_CONFIG_PATH
DEFAULT_CACHE_TTL_SECONDS = 60.0
SUPPORTED_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Cache state
# ---------------------------------------------------------------------------

# Cache is deliberately keyed by absolute file path. This allows bimap.yaml,
# retention.yaml and slai_profile.yaml to coexist without invalidating one
# another.
_config_cache: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()
_ConfigValidator = Callable[
    [Mapping[str, Any], Path],
    None,
]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def _announce(
    action: str,
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Emit one content-free configuration diagnostic."""

    printer.status(
        "CONFIG",
        action,
        "info",
    )

    payload: dict[str, Any] = {
        "event": "bimap_config_action",
        "component": _COMPONENT,
        "action": action,
    }

    if context:
        payload["context"] = dict(context)

    logger.debug(payload)


# ---------------------------------------------------------------------------
# Generic validation helpers
# ---------------------------------------------------------------------------

def _require_mapping(
    value: Any,
    *,
    field: str,
) -> Mapping[str, Any]:
    """Require a configuration mapping."""

    if not isinstance(
        value,
        Mapping,
    ):
        raise TypeError(
            f"{field} must be a mapping; "
            f"received {type(value).__name__}."
        )

    return value


def _reject_unknown_keys(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    field: str,
) -> None:
    """Fail closed when a configuration mapping contains unknown keys."""

    unexpected = sorted(
        str(key)
        for key in value
        if key not in allowed
    )

    if unexpected:
        raise ValueError(
            f"{field} contains unsupported fields: "
            f"{unexpected}."
        )


def _require_bool(
    value: Any,
    *,
    field: str,
) -> bool:
    """Require an exact boolean configuration value."""

    if not isinstance(
        value,
        bool,
    ):
        raise TypeError(
            f"{field} must be boolean; "
            f"received {type(value).__name__}."
        )

    return value


def _require_optional_int(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
) -> int | None:
    """Require an optional integer with an explicit minimum."""

    if value is None:
        return None

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
    ):
        raise TypeError(
            f"{field} must be an integer or null."
        )

    if value < minimum:
        raise ValueError(
            f"{field} must be >= {minimum}."
        )

    return value


def _require_optional_text(
    value: Any,
    *,
    field: str,
) -> str | None:
    """Require optional non-empty text."""

    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        raise TypeError(
            f"{field} must be a string or null."
        )

    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{field} cannot be empty."
        )

    return normalized


def _validate_schema_version(
    payload: Mapping[str, Any],
    path: Path,
) -> None:
    """Validate BIMAP configuration schema version."""

    version = payload.get(
        "schema_version"
    )

    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != SUPPORTED_SCHEMA_VERSION
    ):
        raise ValueError(
            f"{path.name} has unsupported "
            f"schema_version={version!r}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}."
        )


# ---------------------------------------------------------------------------
# bimap.yaml validation
# ---------------------------------------------------------------------------

def _validate_bimap_config(
    payload: Mapping[str, Any],
    path: Path,
) -> None:
    """Validate the structural contract of configs/bimap.yaml."""

    _validate_schema_version(
        payload,
        path,
    )

    _reject_unknown_keys(
        payload,
        allowed={
            "schema_version",
            "api",
            "bootstrap",
        },
        field=path.name,
    )

    api = _require_mapping(
        payload.get("api"),
        field="api",
    )

    bootstrap = _require_mapping(
        payload.get("bootstrap"),
        field="bootstrap",
    )

    _reject_unknown_keys(
        api,
        allowed={
            "api_prefix",
            "title",
            "correlation_header",
            "request_id_header",
            "max_correlation_id_length",
            "reject_invalid_correlation",
            "openapi_url",
            "docs_url",
            "redoc_url",
            "request_limits",
            "security",
        },
        field="api",
    )

    for field_name in (
        "api_prefix",
        "title",
        "correlation_header",
        "request_id_header",
    ):
        value = api.get(field_name)

        if (
            not isinstance(value, str)
            or not value.strip()
        ):
            raise TypeError(
                f"api.{field_name} must be "
                "non-empty text."
            )

    max_correlation = api.get(
        "max_correlation_id_length"
    )

    if (
        isinstance(max_correlation, bool)
        or not isinstance(
            max_correlation,
            int,
        )
        or max_correlation <= 0
    ):
        raise TypeError(
            "api.max_correlation_id_length "
            "must be a positive integer."
        )

    _require_bool(
        api.get(
            "reject_invalid_correlation"
        ),
        field=(
            "api.reject_invalid_correlation"
        ),
    )

    for field_name in (
        "openapi_url",
        "docs_url",
        "redoc_url",
    ):
        _require_optional_text(
            api.get(field_name),
            field=f"api.{field_name}",
        )

    request_limits = _require_mapping(
        api.get("request_limits"),
        field="api.request_limits",
    )

    _reject_unknown_keys(
        request_limits,
        allowed={
            "max_body_bytes",
            "max_header_count",
            "max_header_bytes",
        },
        field="api.request_limits",
    )

    _require_optional_int(
        request_limits.get(
            "max_body_bytes"
        ),
        field=(
            "api.request_limits."
            "max_body_bytes"
        ),
        minimum=0,
    )

    _require_optional_int(
        request_limits.get(
            "max_header_count"
        ),
        field=(
            "api.request_limits."
            "max_header_count"
        ),
        minimum=1,
    )

    _require_optional_int(
        request_limits.get(
            "max_header_bytes"
        ),
        field=(
            "api.request_limits."
            "max_header_bytes"
        ),
        minimum=1,
    )

    security = _require_mapping(
        api.get("security"),
        field="api.security",
    )

    _reject_unknown_keys(
        security,
        allowed={
            "allowed_hosts",
            "require_https",
            "add_nosniff",
            "referrer_policy",
            "frame_options",
            "content_security_policy",
            "permissions_policy",
            "hsts_max_age_seconds",
            "hsts_include_subdomains",
            "hsts_preload",
        },
        field="api.security",
    )

    allowed_hosts = security.get(
        "allowed_hosts"
    )

    if (
        isinstance(
            allowed_hosts,
            (
                str,
                bytes,
                bytearray,
                Mapping,
            ),
        )
        or not isinstance(
            allowed_hosts,
            (list, tuple),
        )
    ):
        raise TypeError(
            "api.security.allowed_hosts "
            "must be a sequence."
        )

    normalized_hosts: list[str] = []

    for index, host in enumerate(
        allowed_hosts
    ):
        if (
            not isinstance(host, str)
            or not host.strip()
        ):
            raise TypeError(
                "api.security.allowed_hosts"
                f"[{index}] must be "
                "non-empty text."
            )

        normalized_hosts.append(
            host.strip()
        )

    if (
        len(normalized_hosts)
        != len(set(normalized_hosts))
    ):
        raise ValueError(
            "api.security.allowed_hosts "
            "contains duplicate values."
        )

    for field_name in (
        "require_https",
        "add_nosniff",
        "hsts_include_subdomains",
        "hsts_preload",
    ):
        _require_bool(
            security.get(field_name),
            field=(
                f"api.security.{field_name}"
            ),
        )

    for field_name in (
        "referrer_policy",
        "frame_options",
        "content_security_policy",
        "permissions_policy",
    ):
        _require_optional_text(
            security.get(field_name),
            field=(
                f"api.security.{field_name}"
            ),
        )

    _require_optional_int(
        security.get(
            "hsts_max_age_seconds"
        ),
        field=(
            "api.security."
            "hsts_max_age_seconds"
        ),
        minimum=0,
    )

    _reject_unknown_keys(
        bootstrap,
        allowed={
            "slai_required_agents",
            "allow_degraded_slai_readiness",
            "retain_slai_shared_memory",
            "expose_health_details",
        },
        field="bootstrap",
    )

    required_agents = bootstrap.get(
        "slai_required_agents"
    )

    if required_agents is not None:
        if (
            isinstance(
                required_agents,
                (
                    str,
                    bytes,
                    bytearray,
                    Mapping,
                ),
            )
            or not isinstance(
                required_agents,
                (list, tuple),
            )
        ):
            raise TypeError(
                "bootstrap.slai_required_agents "
                "must be a sequence or null."
            )

        normalized_agents: list[str] = []

        for index, agent in enumerate(
            required_agents
        ):
            if (
                not isinstance(agent, str)
                or not agent.strip()
            ):
                raise TypeError(
                    "bootstrap."
                    "slai_required_agents"
                    f"[{index}] must be "
                    "non-empty text."
                )

            normalized_agents.append(
                agent.strip().lower()
            )

        if (
            len(normalized_agents)
            != len(set(normalized_agents))
        ):
            raise ValueError(
                "bootstrap.slai_required_agents "
                "contains duplicate agents."
            )

    for field_name in (
        "allow_degraded_slai_readiness",
        "retain_slai_shared_memory",
        "expose_health_details",
    ):
        _require_bool(
            bootstrap.get(field_name),
            field=(
                f"bootstrap.{field_name}"
            ),
        )


# ---------------------------------------------------------------------------
# retention.yaml validation
# ---------------------------------------------------------------------------

_RETENTION_CLASSES = {
    "account_order_metadata",
    "staging_uploads",
    "project_uploads",
    "derived_evidence",
    "reports",
    "telemetry",
    "training_evaluation_data",
}


def _validate_retention_config(
    payload: Mapping[str, Any],
    path: Path,
) -> None:
    """Validate the structural contract of configs/retention.yaml."""

    _validate_schema_version(
        payload,
        path,
    )

    _reject_unknown_keys(
        payload,
        allowed={
            "schema_version",
            "retention",
        },
        field=path.name,
    )

    retention = _require_mapping(
        payload.get("retention"),
        field="retention",
    )

    missing = sorted(
        _RETENTION_CLASSES
        - set(retention)
    )

    unexpected = sorted(
        set(retention)
        - _RETENTION_CLASSES
    )

    if missing:
        raise ValueError(
            "retention.yaml is missing "
            f"retention classes: {missing}."
        )

    if unexpected:
        raise ValueError(
            "retention.yaml contains "
            "unsupported retention classes: "
            f"{unexpected}."
        )

    for name, raw_policy in (
        retention.items()
    ):
        policy = _require_mapping(
            raw_policy,
            field=f"retention.{name}",
        )

        if "policy" not in policy:
            raise ValueError(
                f"retention.{name}.policy "
                "is required."
            )

        _require_optional_text(
            policy.get("policy"),
            field=f"retention.{name}.policy",
        )

        if "duration_days" not in policy:
            raise ValueError(
                f"retention.{name}."
                "duration_days is required; "
                "use null when the duration "
                "has not yet been approved."
            )

        _require_optional_int(
            policy.get("duration_days"),
            field=(
                f"retention.{name}."
                "duration_days"
            ),
            minimum=1,
        )

        for key, value in policy.items():
            if key in {
                "policy",
                "duration_days",
            }:
                continue

            if not isinstance(
                value,
                (
                    bool,
                    str,
                    int,
                    type(None),
                ),
            ):
                raise TypeError(
                    f"retention.{name}.{key} "
                    "must be a scalar policy "
                    "value."
                )


# ---------------------------------------------------------------------------
# slai_profile.yaml validation
# ---------------------------------------------------------------------------

_SLAI_TIERS = {
    "core",
    "conditional",
    "supporting",
    "deferred",
    "disabled",
}


def _validate_slai_profile(
    payload: Mapping[str, Any],
    path: Path,
) -> None:
    """
    Validate the SLAI profile envelope.

    No ``schema_version`` is accepted here because SLAIAgentPolicy currently
    accepts exactly one top-level field: ``agents``.
    """

    _reject_unknown_keys(
        payload,
        allowed={"agents"},
        field=path.name,
    )

    agents = _require_mapping(
        payload.get("agents"),
        field="agents",
    )

    if not agents:
        raise ValueError(
            "slai_profile.yaml must define "
            "at least one agent policy."
        )

    for raw_name, raw_entry in (
        agents.items()
    ):
        if (
            not isinstance(raw_name, str)
            or not raw_name.strip()
        ):
            raise TypeError(
                "SLAI agent names must be "
                "non-empty text."
            )

        name = raw_name.strip().lower()

        entry = _require_mapping(
            raw_entry,
            field=f"agents.{name}",
        )

        _reject_unknown_keys(
            entry,
            allowed={
                "tier",
                "enabled",
                "required",
            },
            field=f"agents.{name}",
        )

        tier = entry.get("tier")

        if (
            not isinstance(tier, str)
            or tier.strip().lower()
            not in _SLAI_TIERS
        ):
            raise ValueError(
                f"agents.{name}.tier must be "
                f"one of {sorted(_SLAI_TIERS)}."
            )

        enabled = _require_bool(
            entry.get("enabled"),
            field=f"agents.{name}.enabled",
        )

        required = _require_bool(
            entry.get("required"),
            field=f"agents.{name}.required",
        )

        if required and not enabled:
            raise ValueError(
                f"agents.{name} cannot be "
                "required while disabled."
            )


# ---------------------------------------------------------------------------
# Path / YAML helpers
# ---------------------------------------------------------------------------

def _resolve_config_path(
    config_path: str | Path | None = None,
    *,
    default_path: Path = BIMAP_CONFIG_PATH,
) -> Path:
    """
    Resolve a configuration path relative to the BIMAP package root.

    This is intentional: BIMAP is expected to run from the SLAI repository
    root, so configuration resolution must not depend on the process CWD.
    """

    if config_path is None:
        target = default_path

    else:
        target = Path(
            config_path
        ).expanduser()

        if not target.is_absolute():
            target = (
                PACKAGE_ROOT
                / target
            )

    return target.resolve()


def _get_file_mtime(
    path: Path,
) -> float:
    """Return file mtime or 0.0 when unavailable."""

    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _validate_cache_ttl(
    value: float,
) -> float:
    """Validate cache TTL."""

    if (
        isinstance(value, bool)
        or not isinstance(
            value,
            (int, float),
        )
    ):
        raise TypeError(
            "cache_ttl must be numeric."
        )

    normalized = float(value)

    if normalized < 0:
        raise ValueError(
            "cache_ttl cannot be negative."
        )

    return normalized


def _should_reload(
    current_mtime: float,
    current_loaded_at: float,
    ttl: float,
    force: bool,
) -> bool:
    """
    Backward-compatible generic freshness helper.

    Per-path mtime comparison is additionally performed by ``_load_config``.
    """

    if force:
        return True

    if current_loaded_at <= 0:
        return True

    if (
        ttl > 0
        and (
            time.time()
            - current_loaded_at
        ) > ttl
    ):
        return True

    return False


def _load_yaml_file(
    path: Path,
) -> dict[str, Any]:
    """Read one YAML mapping without modifying its contents."""

    if not path.is_file():
        raise FileNotFoundError(
            "BIMAP configuration file "
            f"does not exist: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as stream:
        payload = (
            yaml.safe_load(stream)
            or {}
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise TypeError(
            f"Configuration file {path} "
            "must contain a YAML mapping; "
            f"received "
            f"{type(payload).__name__}."
        )

    return payload


# ---------------------------------------------------------------------------
# Cached loading
# ---------------------------------------------------------------------------

def _load_config(
    path: Path,
    *,
    validator: _ConfigValidator,
    force_reload: bool,
    cache_ttl: float,
) -> dict[str, Any]:
    """Load, validate and cache one configuration file."""

    ttl = _validate_cache_ttl(
        cache_ttl
    )

    resolved = path.resolve()
    cache_key = str(resolved)

    _announce(
        f"Loading {resolved.name}",
        context={
            "path": cache_key,
            "force_reload":
                force_reload,
        },
    )

    with _lock:
        entry = _config_cache.get(
            cache_key
        )

        current_mtime = (
            _get_file_mtime(
                resolved
            )
        )

        must_reload = (
            entry is None
            or entry.get("data") is None
            or force_reload
            or (
                current_mtime
                != entry.get(
                    "mtime",
                    0.0,
                )
            )
            or _should_reload(
                current_mtime,
                float(
                    entry.get(
                        "loaded_at",
                        0.0,
                    )
                )
                if entry is not None
                else 0.0,
                ttl,
                force_reload,
            )
        )

        if must_reload:
            try:
                payload = (
                    _load_yaml_file(
                        resolved
                    )
                )

                validator(
                    payload,
                    resolved,
                )

            except Exception:
                logger.exception(
                    "Failed to load BIMAP "
                    "configuration file: %s",
                    resolved,
                )
                raise

            # Cache a detached snapshot.
            snapshot = copy.deepcopy(
                payload
            )

            _config_cache[
                cache_key
            ] = {
                "path": cache_key,
                "mtime": current_mtime,
                "loaded_at": time.time(),
                "data": snapshot,
            }

            logger.info(
                {
                    "event":
                        "bimap_config_loaded",
                    "path":
                        cache_key,
                    "mtime":
                        current_mtime,
                }
            )

            entry = _config_cache[
                cache_key
            ]

        else:
            logger.debug(
                {
                    "event":
                        "bimap_config_cache_hit",
                    "path":
                        cache_key,
                }
            )

        return copy.deepcopy(entry["data"])


# ---------------------------------------------------------------------------
# Public configuration surfaces
# ---------------------------------------------------------------------------

def load_bimap_config(
    config_path: str | Path | None = None,
    *,
    force_reload: bool = False,
    cache_ttl: float = (
        DEFAULT_CACHE_TTL_SECONDS
    ),
) -> dict[str, Any]:
    """Load configs/bimap.yaml."""

    path = _resolve_config_path(
        config_path,
        default_path=BIMAP_CONFIG_PATH,
    )

    return _load_config(
        path,
        validator=_validate_bimap_config,
        force_reload=force_reload,
        cache_ttl=cache_ttl,
    )


def load_retention_config(
    config_path: str | Path | None = None,
    *,
    force_reload: bool = False,
    cache_ttl: float = (
        DEFAULT_CACHE_TTL_SECONDS
    ),
) -> dict[str, Any]:
    """Load configs/retention.yaml."""

    path = _resolve_config_path(
        config_path,
        default_path=RETENTION_CONFIG_PATH,
    )

    return _load_config(
        path,
        validator=(
            _validate_retention_config
        ),
        force_reload=force_reload,
        cache_ttl=cache_ttl,
    )


def load_slai_profile(
    config_path: str | Path | None = None,
    *,
    force_reload: bool = False,
    cache_ttl: float = (
        DEFAULT_CACHE_TTL_SECONDS
    ),
) -> dict[str, Any]:
    """Load configs/slai_profile.yaml."""

    path = _resolve_config_path(
        config_path,
        default_path=(
            SLAI_PROFILE_CONFIG_PATH
        ),
    )

    return _load_config(
        path,
        validator=(
            _validate_slai_profile
        ),
        force_reload=force_reload,
        cache_ttl=cache_ttl,
    )


def load_configuration_bundle(
    *,
    force_reload: bool = False,
    cache_ttl: float = (
        DEFAULT_CACHE_TTL_SECONDS
    ),
) -> dict[str, dict[str, Any]]:
    """
    Load the three canonical BIMAP configuration surfaces.

    The mappings remain separate deliberately. In particular, SLAI profile
    data is never merged with global configuration metadata because
    SLAIAgentPolicy is fail-closed for unknown top-level fields.
    """

    _announce(
        "Loading BIMAP configuration bundle"
    )

    return {
        "bimap": load_bimap_config(
            force_reload=force_reload,
            cache_ttl=cache_ttl,
        ),
        "retention":
            load_retention_config(
                force_reload=force_reload,
                cache_ttl=cache_ttl,
            ),
        "slai_profile":
            load_slai_profile(
                force_reload=force_reload,
                cache_ttl=cache_ttl,
            ),
    }


# ---------------------------------------------------------------------------
# Backward-compatible API
# ---------------------------------------------------------------------------

def load_global_config(
    config_path: str | Path | None = None,
    *,
    force_reload: bool = False,
    cache_ttl: float = (
        DEFAULT_CACHE_TTL_SECONDS
    ),
) -> dict[str, Any]:
    """
    Load BIMAP's global bimap.yaml.

    This remains the backward-compatible name for existing imports.
    """

    return load_bimap_config(
        config_path,
        force_reload=force_reload,
        cache_ttl=cache_ttl,
    )


def reload_config(
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Force-reload BIMAP's global configuration."""

    return load_bimap_config(
        config_path,
        force_reload=True,
    )


def clear_config_cache(
    config_path: str | Path | None = None,
) -> None:
    """
    Clear one cache entry or the complete configuration cache.

    ``config_path=None`` clears every BIMAP configuration surface.
    """

    with _lock:
        if config_path is None:
            _config_cache.clear()

            logger.info(
                {
                    "event":
                        "bimap_config_cache_cleared",
                    "scope": "all",
                }
            )
            return

        resolved = _resolve_config_path(
            config_path
        )

        _config_cache.pop(
            str(resolved),
            None,
        )

        logger.info(
            {
                "event":
                    "bimap_config_cache_cleared",
                "scope": "single",
                "path": str(resolved),
            }
        )


def get_config_cache_info(
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return diagnostics for one cached configuration."""

    resolved = _resolve_config_path(
        config_path,
        default_path=BIMAP_CONFIG_PATH,
    )

    key = str(resolved)

    with _lock:
        entry = _config_cache.get(
            key
        )

        if entry is None:
            return {
                "config_path": key,
                "mtime": None,
                "loaded_at": None,
                "age_seconds": None,
                "has_data": False,
            }

        loaded_at = float(
            entry["loaded_at"]
        )

        return {
            "config_path": key,
            "mtime": entry["mtime"],
            "loaded_at": loaded_at,
            "age_seconds": (
                time.time()
                - loaded_at
            ),
            "has_data": (
                entry.get("data")
                is not None
            ),
        }


def get_all_config_cache_info(
) -> dict[str, dict[str, Any]]:
    """Return diagnostics for every cached configuration file."""

    with _lock:
        now = time.time()

        return {
            path: {
                "config_path":
                    path,
                "mtime":
                    entry["mtime"],
                "loaded_at":
                    entry["loaded_at"],
                "age_seconds":
                    now
                    - float(
                        entry[
                            "loaded_at"
                        ]
                    ),
                "has_data":
                    entry.get("data")
                    is not None,
            }
            for path, entry
            in _config_cache.items()
        }


def get_config_section(
    section_name: str,
    config: Mapping[str, Any] | None = None,
    *,
    config_path: str | Path | None = None,
    default: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one mapping-valued section from bimap.yaml."""

    if (
        not isinstance(
            section_name,
            str,
        )
        or not section_name.strip()
    ):
        raise ValueError(
            "section_name must be "
            "non-empty text."
        )

    source = (
        load_global_config(
            config_path
        )
        if config is None
        else config
    )

    section = source.get(
        section_name.strip()
    )

    if section is None:
        return copy.deepcopy(
            dict(default or {})
        )

    if not isinstance(
        section,
        Mapping,
    ):
        raise TypeError(
            f"Configuration section "
            f"{section_name!r} "
            "must be a mapping."
        )

    return copy.deepcopy(
        dict(section)
    )


__all__ = [
    "PACKAGE_ROOT",
    "CONFIG_DIR",
    "DEFAULT_CONFIG_PATH",
    "BIMAP_CONFIG_PATH",
    "RETENTION_CONFIG_PATH",
    "SLAI_PROFILE_CONFIG_PATH",
    "DEFAULT_CACHE_TTL_SECONDS",
    "SUPPORTED_SCHEMA_VERSION",
    "_resolve_config_path",
    "_get_file_mtime",
    "_should_reload",
    "_load_yaml_file",
    "load_bimap_config",
    "load_retention_config",
    "load_slai_profile",
    "load_configuration_bundle",
    "load_global_config",
    "reload_config",
    "clear_config_cache",
    "get_config_cache_info",
    "get_all_config_cache_info",
    "get_config_section",
]