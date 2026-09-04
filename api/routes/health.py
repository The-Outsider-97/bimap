"""
FastAPI liveness/readiness routes for BIMAP's SLAI integration surface.

The API depends only on the application-facing SLAI port.  Concrete SLAI
runtime objects such as AgentFactory, SharedMemory, SLAIHealthCheck, and
individual agents remain behind the integration adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Request, Response, status # type: ignore

from ._shared import json_response
from ..utils.api_errors import APIConfigurationError
from ..utils.api_helpers import *
from ...app.ports.slai import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Health")
printer = PrettyPrinter()

_COMPONENT = "api_route_health"


class RouteHealth:
    """Dependency-injected SLAI liveness/readiness route group."""

    __slots__ = (
        "router",
        "_slai",
        "_required_agents",
        "_expose_details",
    )

    def __init__(
        self,
        slai: SLAIPort,
        *,
        required_agents: Sequence[str] | None = None,
        expose_details: bool = False,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing health API routes",
            event="api_route_health_init_start",
        )

        if not isinstance(slai, SLAIPort):
            raise APIConfigurationError(
                "slai must implement the BIMAP SLAI application port.",
                component=_COMPONENT,
                operation="initialize",
                field="slai",
                context={"received_type": type(slai).__name__},
            )

        normalized_required: tuple[str, ...] | None = None

        if required_agents is not None:
            if isinstance(required_agents, (str, bytes, bytearray)):
                raise APIConfigurationError(
                    "required_agents must be a sequence of agent names or None.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="required_agents",
                )

            try:
                raw_agents = tuple(required_agents)
            except TypeError as exc:
                raise APIConfigurationError(
                    "required_agents must be iterable.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="required_agents",
                    context={
                        "received_type": type(required_agents).__name__,
                    },
                    cause=exc,
                ) from exc

            names: list[str] = []
            seen: set[str] = set()

            for index, raw_name in enumerate(raw_agents):
                name = require_api_text(
                    raw_name,
                    field=f"required_agents[{index}]",
                    error_type=APIConfigurationError,
                    component=_COMPONENT,
                    operation="initialize",
                    max_length=256,
                )

                if name in seen:
                    raise APIConfigurationError(
                        "required_agents contains a duplicate agent name.",
                        component=_COMPONENT,
                        operation="initialize",
                        field="required_agents",
                        context={"agent": name},
                    )

                seen.add(name)
                names.append(name)

            if not names:
                raise APIConfigurationError(
                    "required_agents cannot be empty when explicitly supplied.",
                    component=_COMPONENT,
                    operation="initialize",
                    field="required_agents",
                )

            normalized_required = tuple(names)

        if not isinstance(expose_details, bool):
            raise APIConfigurationError(
                "expose_details must be boolean.",
                component=_COMPONENT,
                operation="initialize",
                field="expose_details",
                context={"received_type": type(expose_details).__name__},
            )

        self._slai = slai
        self._required_agents = normalized_required
        self._expose_details = expose_details

        router = APIRouter(
            prefix="/health",
            tags=["health"],
        )
        router.add_api_route(
            "/live",
            self.liveness,
            methods=["GET"],
            response_class=Response,
            name="health_liveness",
        )
        router.add_api_route(
            "/ready",
            self.readiness,
            methods=["GET"],
            response_class=Response,
            name="health_readiness",
        )

        self.router = router

        logger.info(
            {
                "event": "api_route_health_initialized",
                "registered_route_count": 2,
                "required_agent_count": (
                    None
                    if self._required_agents is None
                    else len(self._required_agents)
                ),
                "expose_details": self._expose_details,
            }
        )

    def _payload(self, report: SlaiHealth, *, mode: str) -> dict[str, Any]:
        """Project one validated application-level health result."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Projecting health response",
            event="api_route_health_payload_start",
            context={"mode": mode},
        )

        if self._expose_details:
            payload = report.to_dict()
            if not isinstance(payload, dict):
                raise APIConfigurationError(
                    "SLAI health to_dict() must return a dictionary.",
                    component=_COMPONENT,
                    operation="project_health",
                    field="report",
                    context={
                        "received_type": type(payload).__name__,
                    },
                )
            return payload

        if mode == "liveness":
            available = report.live
            return {
                "mode": "liveness",
                "state": "healthy" if available else "unavailable",
                "live": available,
            }

        if mode == "readiness":
            available = report.ready
            return {
                "mode": "readiness",
                "state": "healthy" if available else "unavailable",
                "ready": available,
            }

        raise APIConfigurationError(
            "Unsupported health projection mode.",
            component=_COMPONENT,
            operation="project_health",
            field="mode",
            context={"mode": mode},
        )

    async def liveness(self, request: Request) -> Response:
        """GET /health/live."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling liveness probe",
            event="api_route_health_liveness_start",
        )
        del request

        report = probe_slai_liveness(self._slai)

        response_status = (
            status.HTTP_200_OK
            if report.live
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        logger.info(
            {
                "event": "api_route_health_liveness_completed",
                "live": report.live,
                "status_code": response_status,
            }
        )

        return json_response(
            self._payload(report, mode="liveness"),
            status_code=response_status,
            headers={"Cache-Control": "no-store"},
        )

    async def readiness(self, request: Request) -> Response:
        """GET /health/ready."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling readiness probe",
            event="api_route_health_readiness_start",
        )
        del request

        report = probe_slai_readiness(
            self._slai,
            required_agents=self._required_agents,
            prepare=False,
        )

        response_status = (
            status.HTTP_200_OK
            if report.ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )

        logger.info(
            {
                "event": "api_route_health_readiness_completed",
                "ready": report.ready,
                "status_code": response_status,
            }
        )

        return json_response(
            self._payload(report, mode="readiness"),
            status_code=response_status,
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteHealth"]
