"""
Public BIMAP contact-form API route.

The route owns only HTTP parsing, validation, contact-subject projection,
ticket creation, and delegation to the deployment-owned contact-message
sender. Email rendering and SMTP transport remain outside the API layer.
"""

from __future__ import annotations

import asyncio

from uuid import uuid4
from fastapi import APIRouter, Request, Response, status  # type: ignore

from ._shared import *
from ..utils.api_errors import *
from ..utils.api_helpers import *
from ..dependencies import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP API Route Contact")
printer = PrettyPrinter()

_COMPONENT = "api_route_contact"


_SUBJECT_LABELS: dict[str, str] = {
    "bim-audit": "BIM Audit",
    "revit-audit": "Revit Audit",
    "3d-content": "3D Models & Scenes",
    "2d-content": "2D DWG Content",
    "conversion": "Model Conversion",
    "extraction": "Data Extraction",
    "other": "Other",
}


class RouteContact:
    """
    Public contact-message route.

    The route deliberately receives a callable rather than EmailService or an
    SMTP provider. This keeps concrete notification infrastructure outside the
    API package.
    """

    __slots__ = ("router", "_send_message")

    def __init__(self, send_message: ContactMessageSender) -> None:
        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing contact API route",
            event="api_route_contact_init_start",
        )

        if not callable(send_message):
            raise APIConfigurationError(
                "send_message must be callable.",
                component=_COMPONENT,
                operation="initialize",
                field="send_message",
                context={"received_type": type(send_message).__name__},
            )

        self._send_message = send_message

        router = APIRouter(prefix="/contact", tags=["contact"])

        router.add_api_route(
            "",
            self.submit,
            methods=["POST"],
            status_code=status.HTTP_202_ACCEPTED,
            response_class=Response,
            name="submit_contact_message",
        )

        self.router = router

        logger.info(
            {
                "event": "api_route_contact_initialized",
                "registered_route_count": 1,
            }
        )

    async def submit(self, request: Request) -> Response:
        """
        POST /contact

        Expected body:
        {
            "name": "...",
            "email": "...",
            "subject": "...",
            "message": "..."
        }
        """

        announce_api_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Handling contact message",
            event="api_route_contact_submit_start",
        )

        payload = validate_object_fields(
            await read_json_object(request),
            required=(
                "name",
                "email",
                "subject",
                "message",
            ),
        )

        sender_name = require_api_text(
            payload["name"],
            field="name",
            component=_COMPONENT,
            operation="submit",
            max_length=128,
        )

        sender_email = require_api_text(
            payload["email"],
            field="email",
            component=_COMPONENT,
            operation="submit",
            max_length=254,
        )

        subject_code = require_api_text(
            payload["subject"],
            field="subject",
            component=_COMPONENT,
            operation="submit",
            max_length=64,
        )

        message = require_api_text(
            payload["message"],
            field="message",
            component=_COMPONENT,
            operation="submit",
            max_length=10_000,
        )

        subject_display = _SUBJECT_LABELS.get(
            subject_code,
        )

        if subject_display is None:
            raise APIValidationError(
                "Unsupported contact subject.",
                public_message=(
                    "Please select a valid contact subject."
                ),
                component=_COMPONENT,
                operation="submit",
                field="subject",
                context={
                    "subject_code":
                        subject_code,
                },
            )

        ticket_number = str(uuid4())

        # EmailService/SMTP are synchronous. Keep blocking network I/O
        # off FastAPI's event loop.
        await asyncio.to_thread(
            self._send_message,
            sender_name,
            sender_email,
            subject_display,
            message,
            ticket_number,
        )

        logger.info(
            {
                "event": "api_route_contact_submit_completed",
                "ticket_number": ticket_number,
                "subject_code": subject_code,
            }
        )

        return json_response(
            {
                "ticket_number": ticket_number,
                "status": "accepted",
            },
            status_code=status.HTTP_202_ACCEPTED,
            headers={
                "Cache-Control": "no-store",
            },
        )


__all__ = [
    "RouteContact",
]