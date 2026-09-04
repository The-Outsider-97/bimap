"""
FastAPI liveness/readiness routes for BIMAP's SLAI integration surface.

The current lower-layer health abstraction is ``slai.health.SLAIHealthCheck``.
It distinguishes:

* liveness: required SLAI integration modules can be discovered/inspected; and
* readiness: injected runtime objects and the explicitly required agent set are
  ready to accept BIMAP work.

This route does not construct agents, initialize SLAI, query customer data, or
claim that database/storage/payment/queue infrastructure is healthy.  Those
dependencies do not yet expose a shared health port and must not be fabricated
here.

Detailed component diagnostics are disabled by default so a public/load-balancer
probe exposes only coarse operational state.  Operators may explicitly enable
``expose_details`` on a protected/private deployment surface.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence
from fastapi import APIRouter, Request, Response, status

from ._shared import json_response
from ..utils.api_errors import APIConfigurationError
from ..utils.api_helpers import announce_api_action
from ...slai.health import SLAIHealthCheck, SLAIHealthReport
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Health")
printer = PrettyPrinter()

_COMPONENT = "api_route_health"


class RouteHealth:
    """Dependency-injected SLAI liveness/readiness route group."""

    __slots__ = (
        "router",
        "_health_check",
        "_factory",
        "_shared_memory",
        "_required_agents",
        "_agents",
        "_expose_details",
    )

    def __init__(
        self,
        health_check: SLAIHealthCheck,
        *,
        factory: Any,
        shared_memory: Any,
        required_agents: Sequence[str],
        agents: Mapping[str, Any] | None = None,
        expose_details: bool = False,
    ) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing health API routes",
            event="api_route_health_init_start",
        )
        if not isinstance(health_check, SLAIHealthCheck):
            raise APIConfigurationError(
                "health_check must be an SLAIHealthCheck.",
                component=_COMPONENT,
                operation="initialize",
                field="health_check",
                context={"received_type": type(health_check).__name__},
            )
        if isinstance(required_agents, (str, bytes, bytearray)):
            raise APIConfigurationError(
                "required_agents must be a sequence of agent names.",
                component=_COMPONENT,
                operation="initialize",
                field="required_agents",
            )
        try:
            normalized_required = tuple(required_agents)
        except TypeError as exc:
            raise APIConfigurationError(
                "required_agents must be iterable.",
                component=_COMPONENT,
                operation="initialize",
                field="required_agents",
                context={"received_type": type(required_agents).__name__},
                cause=exc,
            ) from exc
        if not normalized_required:
            raise APIConfigurationError(
                "At least one required SLAI agent must be configured for readiness.",
                component=_COMPONENT,
                operation="initialize",
                field="required_agents",
            )
        if agents is not None and not isinstance(agents, Mapping):
            raise APIConfigurationError(
                "agents must be a mapping or None.",
                component=_COMPONENT,
                operation="initialize",
                field="agents",
                context={"received_type": type(agents).__name__},
            )
        if not isinstance(expose_details, bool):
            raise APIConfigurationError(
                "expose_details must be boolean.",
                component=_COMPONENT,
                operation="initialize",
                field="expose_details",
            )

        # Exact agent-name normalization belongs to SLAIHealthCheck; this route
        # only materializes the supplied sequence once.
        self._health_check = health_check
        self._factory = factory
        self._shared_memory = shared_memory
        self._required_agents = normalized_required
        self._agents = None if agents is None else dict(agents)
        self._expose_details = expose_details

        router = APIRouter(prefix="/health", tags=["health"])
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
                "required_agent_count": len(self._required_agents),
                "expose_details": self._expose_details,
            }
        )

    def _payload(self, report: SLAIHealthReport) -> dict[str, Any]:
        """Return coarse health state or explicitly enabled diagnostics."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Projecting health response",
            event="api_route_health_payload_start",
        )
        if not isinstance(report, SLAIHealthReport):
            raise APIConfigurationError(
                "Health checker returned an unsupported report type.",
                component=_COMPONENT,
                operation="project_health",
                field="report",
                context={"received_type": type(report).__name__},
            )

        if self._expose_details:
            return report.to_dict()

        payload: dict[str, Any] = {
            "mode": report.mode.value,
            "state": report.overall_state.value,
        }
        if report.mode.value == "liveness":
            payload["live"] = report.live
        else:
            payload["ready"] = report.ready
        return payload

    async def liveness(self, request: Request) -> Response:
        """GET ``/health/live`` -> coarse side-effect-free integration liveness."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling liveness probe",
            event="api_route_health_liveness_start",
        )
        del request

        report = self._health_check.check_liveness()
        response_status = (
            status.HTTP_200_OK
            if report.live
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        logger.info(
            {
                "event": "api_route_health_liveness_completed",
                "state": report.overall_state.value,
                "live": report.live,
                "status_code": response_status,
            }
        )
        return json_response(
            self._payload(report),
            status_code=response_status,
            headers={"Cache-Control": "no-store"},
        )

    async def readiness(self, request: Request) -> Response:
        """GET ``/health/ready`` -> strict current SLAI readiness."""
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling readiness probe",
            event="api_route_health_readiness_start",
        )
        del request

        report = self._health_check.check_readiness(
            factory=self._factory,
            shared_memory=self._shared_memory,
            required_agents=self._required_agents,
            agents=self._agents,
        )
        response_status = (
            status.HTTP_200_OK
            if report.ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        logger.info(
            {
                "event": "api_route_health_readiness_completed",
                "state": report.overall_state.value,
                "ready": report.ready,
                "status_code": response_status,
            }
        )
        return json_response(
            self._payload(report),
            status_code=response_status,
            headers={"Cache-Control": "no-store"},
        )


__all__ = ["RouteHealth"]