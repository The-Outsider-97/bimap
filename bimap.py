"""
SLAI-root process launcher for the R3D BIM Audit Platform (BIMAP).

Location
--------
SLAI/bimap.py

Architectural role
------------------
This file is outside the BIMAP package and belongs to the SLAI host process.

It:

- resolves the deployment-owned BIMAP Bootstrap factory;
- creates one BIMAP runtime per server process;
- exposes the FastAPI application through Uvicorn;
- coordinates clean BIMAP shutdown;
- supports deployment preflight/readiness checking;
- exposes version information; and
- preserves the SLAI-root execution environment.

It does NOT:

- construct BIMAP repositories;
- construct storage/payment/malware/queue adapters;
- define product or rule policy;
- construct audit rules;
- manipulate sys.path;
- parse BIM evidence;
- launch or supervise the Next.js frontend process.

Why ``applications.bimap``?
---------------------------
This file is named ``bimap.py`` and therefore occupies the top-level Python
module name ``bimap`` when SLAI is executed from its root directory.

The BIMAP application package is consequently imported through:

    applications.bimap

rather than:

    bimap

This avoids module/package shadowing without sys.path manipulation.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import uvicorn

from collections.abc import Callable
from typing import Any, cast
from fastapi import FastAPI

from logs.logger import PrettyPrinter, configure_logging, get_logger
from applications.bimap.bootstrap import Bootstrap, BootstrapError
from applications.bimap.version import __version__


logger = get_logger("SLAI BIMAP Launcher")
printer = PrettyPrinter()


_FACTORY_ENV = "BIMAP_BOOTSTRAP_FACTORY"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000
_DEFAULT_WORKERS = 1

_DEFAULT_FORWARDED_ALLOW_IPS = "127.0.0.1"


BootstrapFactory = Callable[[], Bootstrap]


_active_bootstrap: Bootstrap | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BIMAPLauncherError(RuntimeError):
    """Base failure raised by the SLAI-root BIMAP launcher."""


class BIMAPLauncherConfigurationError(BIMAPLauncherError):
    """Raised when process/deployment configuration is invalid."""


class BIMAPLauncherFactoryError(BIMAPLauncherError):
    """Raised when the deployment Bootstrap factory cannot be resolved."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _announce(
    action: str,
    *,
    level: str = "info",
) -> None:
    """Emit one process-level diagnostic without customer evidence."""

    printer.status(
        "BIMAP",
        action,
        level,
    )

    logger.debug(
        {
            "event": "bimap_launcher_action",
            "action": action,
        }
    )


def _positive_int(value: str) -> int:
    """argparse validator for strictly positive integer values."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "value must be an integer"
        ) from exc

    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "value must be greater than zero"
        )

    return parsed


def _port(value: str) -> int:
    """Validate one TCP port."""

    parsed = _positive_int(value)

    if parsed > 65535:
        raise argparse.ArgumentTypeError(
            "port must be <= 65535"
        )

    return parsed


def _factory_spec(
    explicit: str | None = None,
) -> str:
    """
    Resolve the deployment Bootstrap factory specification.

    Format:

        package.module:callable

    Example:

        deployment.bimap:create_bootstrap
    """

    raw = (
        explicit
        if explicit is not None
        else os.getenv(_FACTORY_ENV)
    )

    if raw is None:
        raise BIMAPLauncherConfigurationError(
            f"{_FACTORY_ENV} is not configured. "
            "Set it to a callable such as "
            "'deployment.bimap:create_bootstrap'."
        )

    spec = raw.strip()

    if not spec:
        raise BIMAPLauncherConfigurationError(
            f"{_FACTORY_ENV} cannot be empty."
        )

    module_name, separator, attribute_name = spec.partition(":")

    if (
        separator != ":"
        or not module_name.strip()
        or not attribute_name.strip()
        or ":" in attribute_name
    ):
        raise BIMAPLauncherConfigurationError(
            "Bootstrap factory must use "
            "'module.path:callable_name' syntax."
        )

    return (
        f"{module_name.strip()}:"
        f"{attribute_name.strip()}"
    )


def _load_factory(
    specification: str,
) -> BootstrapFactory:
    """Import and validate one deployment Bootstrap factory."""

    _announce(
        "Resolving BIMAP deployment factory"
    )

    module_name, _, attribute_name = specification.partition(":")

    try:
        module = importlib.import_module(
            module_name
        )
    except Exception as exc:
        raise BIMAPLauncherFactoryError(
            "Unable to import BIMAP deployment module "
            f"{module_name!r}: {type(exc).__name__}"
        ) from exc

    try:
        factory = getattr(
            module,
            attribute_name,
        )
    except AttributeError as exc:
        raise BIMAPLauncherFactoryError(
            f"Deployment module {module_name!r} does not expose "
            f"{attribute_name!r}."
        ) from exc

    if not callable(factory):
        raise BIMAPLauncherFactoryError(
            "Configured BIMAP Bootstrap factory is not callable."
        )

    return cast(BootstrapFactory, factory)


def _create_bootstrap(
    specification: str,
) -> Bootstrap:
    """Create and validate one BIMAP Bootstrap instance."""

    factory = _load_factory(
        specification
    )

    try:
        bootstrap = factory()
    except Exception as exc:
        raise BIMAPLauncherFactoryError(
            "BIMAP deployment factory failed while constructing "
            f"Bootstrap: {type(exc).__name__}"
        ) from exc

    if not isinstance(
        bootstrap,
        Bootstrap,
    ):
        raise BIMAPLauncherFactoryError(
            "BIMAP deployment factory must return "
            "applications.bimap.bootstrap.Bootstrap; "
            f"received {type(bootstrap).__name__}."
        )

    return bootstrap


# ---------------------------------------------------------------------------
# ASGI factory
# ---------------------------------------------------------------------------


def create_application() -> FastAPI:
    """
    Uvicorn application factory.

    Each Uvicorn worker calls this function independently. Consequently each
    server process receives its own Bootstrap lifecycle and FastAPI application.

    The SLAI SharedMemory lifecycle remains governed by Bootstrap's explicit
    ownership configuration.
    """

    global _active_bootstrap

    _announce(
        "Creating BIMAP ASGI application"
    )

    if _active_bootstrap is not None:
        raise BIMAPLauncherError(
            "A BIMAP Bootstrap is already active in this process."
        )

    specification = _factory_spec()

    bootstrap = _create_bootstrap(
        specification
    )

    try:
        runtime = bootstrap.build()
    except Exception:
        try:
            bootstrap.close()
        except Exception:
            logger.exception(
                "BIMAP cleanup failed after unsuccessful startup"
            )
        raise

    application = runtime.application

    if not isinstance(
        application,
        FastAPI,
    ):
        try:
            bootstrap.close()
        finally:
            raise BIMAPLauncherError(
                "Bootstrap runtime did not provide a FastAPI application."
            )

    _active_bootstrap = bootstrap

    # Keep lifecycle objects available to trusted process integrations.
    # They are application state, not public HTTP API data.
    application.state.bimap_bootstrap = bootstrap
    application.state.bimap_runtime = runtime

    async def _shutdown_bimap() -> None:
        global _active_bootstrap

        _announce(
            "Shutting down BIMAP runtime"
        )

        try:
            bootstrap.close()
        finally:
            if _active_bootstrap is bootstrap:
                _active_bootstrap = None

    application.add_event_handler(
        "shutdown",
        _shutdown_bimap,
    )

    logger.info(
        {
            "event": "bimap_asgi_application_created",
            "version": __version__,
        }
    )

    printer.status(
        "BIMAP",
        f"Backend ready — version {__version__}",
        "success",
    )

    return application


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _run_preflight(
    specification: str,
) -> int:
    """Build the complete graph and evaluate its SLAI integration health."""

    _announce(
        "Running BIMAP deployment preflight"
    )

    bootstrap = _create_bootstrap(
        specification
    )

    try:
        runtime = bootstrap.build()

        liveness = runtime.slai.check_liveness()
        readiness = runtime.slai.check_readiness()

        printer.status(
            "LIVE",
            liveness.to_dict(),
            "success" if liveness.live else "error",
        )

        printer.status(
            "READY",
            readiness.to_dict(),
            "success" if readiness.ready else "warning",
        )

        if not liveness.live:
            return 2

        if not readiness.ready:
            return 3

        printer.status(
            "BIMAP",
            "Deployment preflight passed",
            "success",
        )

        return 0

    finally:
        bootstrap.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _create_parser() -> argparse.ArgumentParser:
    """Construct the SLAI-root BIMAP CLI."""

    parser = argparse.ArgumentParser(
        prog="bimap.py",
        description=(
            "R3D BIM Audit Platform service launcher "
            "for the SLAI host runtime."
        ),
    )

    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # ------------------------------------------------------------------
    # serve
    # ------------------------------------------------------------------

    serve = subcommands.add_parser(
        "serve",
        help="Run the BIMAP FastAPI backend.",
    )

    serve.add_argument(
        "--factory",
        default=None,
        help=(
            "Bootstrap factory as module:callable. "
            f"Defaults to ${_FACTORY_ENV}."
        ),
    )

    serve.add_argument(
        "--host",
        default=os.getenv(
            "BIMAP_HOST",
            _DEFAULT_HOST,
        ),
        help="Backend bind host.",
    )

    serve.add_argument(
        "--port",
        type=_port,
        default=int(
            os.getenv(
                "BIMAP_PORT",
                str(_DEFAULT_PORT),
            )
        ),
        help="Backend TCP port.",
    )

    serve.add_argument(
        "--workers",
        type=_positive_int,
        default=int(
            os.getenv(
                "BIMAP_WORKERS",
                str(_DEFAULT_WORKERS),
            )
        ),
        help="Number of Uvicorn worker processes.",
    )

    serve.add_argument(
        "--reload",
        action="store_true",
        help="Enable code reload for development only.",
    )

    serve.add_argument(
        "--log-level",
        choices=(
            "critical",
            "error",
            "warning",
            "info",
            "debug",
            "trace",
        ),
        default=os.getenv(
            "BIMAP_UVICORN_LOG_LEVEL",
            "info",
        ),
    )

    serve.add_argument(
        "--proxy-headers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Honor trusted proxy forwarding headers.",
    )

    serve.add_argument(
        "--forwarded-allow-ips",
        default=os.getenv(
            "BIMAP_FORWARDED_ALLOW_IPS",
            _DEFAULT_FORWARDED_ALLOW_IPS,
        ),
        help=(
            "Comma-separated proxy IP allowlist. "
            "Do not use '*' unless the network boundary is trusted."
        ),
    )

    serve.add_argument(
        "--access-log",
        action=argparse.BooleanOptionalAction,
        default=True,
    )

    # ------------------------------------------------------------------
    # check
    # ------------------------------------------------------------------

    check = subcommands.add_parser(
        "check",
        help="Build BIMAP and run deployment liveness/readiness checks.",
    )

    check.add_argument(
        "--factory",
        default=None,
        help=(
            "Bootstrap factory as module:callable. "
            f"Defaults to ${_FACTORY_ENV}."
        ),
    )

    # ------------------------------------------------------------------
    # version
    # ------------------------------------------------------------------

    subcommands.add_parser(
        "version",
        help="Print the BIMAP package version.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute the SLAI-root BIMAP launcher."""

    configure_logging()

    parser = _create_parser()
    args: dict[str, Any] = vars(parser.parse_args(argv))

    try:
        if args["command"] == "version":
            print(__version__)
            return 0

        specification = _factory_spec(args.get("factory"))

        if args["command"] == "check":
            return _run_preflight(specification)

        if args["command"] != "serve":
            raise BIMAPLauncherConfigurationError(
                f"Unsupported command: {args['command']}"
            )

        if (
            args["reload"]
            and args["workers"] != 1
        ):
            raise BIMAPLauncherConfigurationError("--reload and --workers > 1 cannot be used together.")

        # Uvicorn worker processes must inherit the exact same factory.
        os.environ[_FACTORY_ENV] = specification

        printer.status(
            "BIMAP",
            f"Starting backend on {args['host']}:{args['port']}",
            "info",
        )

        logger.info(
            {
                "event": "bimap_server_start",
                "host": args["host"],
                "port": args["port"],
                "workers": args["workers"],
                "reload": args["reload"],
                "version": __version__,
            }
        )

        uvicorn.run(
            "bimap:create_application",
            factory=True,
            host=args["host"],
            port=args["port"],
            workers=args["workers"],
            reload=args["reload"],
            log_level=args["log_level"],
            log_config=None,
            access_log=args["access_log"],
            proxy_headers=args["proxy_headers"],
            forwarded_allow_ips=args["forwarded_allow_ips"],
            server_header=False,
            lifespan="on",
        )

        return 0

    except (
        BIMAPLauncherError,
        BootstrapError,
    ) as exc:
        logger.exception(
            "BIMAP launcher failed"
        )

        printer.status(
            "BIMAP",
            str(exc),
            "error",
        )

        return 1

    except KeyboardInterrupt:
        printer.status(
            "BIMAP",
            "Shutdown requested",
            "warning",
        )

        return 130


__all__ = [
    "BIMAPLauncherError",
    "BIMAPLauncherConfigurationError",
    "BIMAPLauncherFactoryError",
    "create_application",
    "main",
]

if __name__ == "__main__":
    main()
    # raise SystemExit(
    #     main(sys.argv[1:])
    # )
