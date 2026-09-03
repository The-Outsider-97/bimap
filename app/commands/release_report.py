"""
Release-report application command for BIMAP.

``FulfilmentService.release_report`` is the authoritative application service for
report release.  It already coordinates governance validation, deterministic
report generation, deterministic package construction, explicit object
publication, report-manifest persistence, lifecycle transitions to
``PACKAGING``/``DELIVERED``, and content-integrity verification.

This command therefore mirrors that supported use-case surface without creating
another report builder, storage naming convention, retention policy, notification
policy, or governance rule.

Notification is intentionally not hidden inside this command.  The fulfilment
service exposes ``notify_report_available`` separately so a notification outage
cannot make an already published/delivered report appear to have failed release.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ..services.fulfilment_service import *
from ...contracts.evidence import EvidenceContract
from ...contracts.finding import FindingContract
from ...contracts.requirement import RequirementContract
from ...domain.evidence.models import EvidenceItem
from ...domain.governance.review import Review
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Release Report Command")
printer = PrettyPrinter()

_COMPONENT = "release_report_command"


class ReleaseReport:
    """Build, publish, persist and deliver one governed BIMAP report."""

    __slots__ = ("_service",)

    def __init__(self, service: FulfilmentService) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing release-report command",
            event="release_report_command_init_start",
        )

        if not isinstance(service, FulfilmentService):
            raise AppConfigurationError(
                "service must be a FulfilmentService.",
                component=_COMPONENT,
                operation="initialize",
                field="service",
                context={"received_type": type(service).__name__},
            )

        self._service = service
        logger.debug(
            {
                "event": "release_report_command_initialized",
                "service_type": type(service).__name__,
            }
        )

    def execute(
        self,
        *,
        order_id: str,
        findings: Iterable[FindingContract],
        evidence: Iterable[EvidenceContract | EvidenceItem],
        report_id: str,
        report_version: str,
        artifact_ids: Mapping[str, str],
        artifact_object_ids: Mapping[str, str],
        package_object_id: str,
        packaging_idempotency_key: str,
        delivery_idempotency_key: str,
        requirements: Iterable[RequirementContract] = (),
        reviews: Iterable[Review] = (),
        expires_at: datetime | str | None = None,
        software_versions: Mapping[str, str] | None = None,
        ruleset_versions: Mapping[str, str] | None = None,
        include_pdf: bool = True,
        artifact_content_types: Mapping[str, str] | None = None,
        package_content_type: str | None = "application/zip",
        actor: str | None = None,
    ) -> FulfilmentResult:
        """
        Release one governed report using explicit report/storage identities.

        Collection arguments are forwarded without pre-materialization so this
        command does not consume generators twice.  The fulfilment service owns
        typed materialization and exact artifact/storage consistency checks.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing release-report command",
            event="release_report_command_execute_start",
            context={
                "order_id": order_id,
                "report_id": report_id,
                "include_pdf": include_pdf,
                "has_expiry": expires_at is not None,
            },
        )

        try:
            result = self._service.release_report(
                order_id=order_id,
                findings=findings,
                evidence=evidence,
                report_id=report_id,
                report_version=report_version,
                artifact_ids=artifact_ids,
                artifact_object_ids=artifact_object_ids,
                package_object_id=package_object_id,
                packaging_idempotency_key=packaging_idempotency_key,
                delivery_idempotency_key=delivery_idempotency_key,
                requirements=requirements,
                reviews=reviews,
                expires_at=expires_at,
                software_versions=software_versions,
                ruleset_versions=ruleset_versions,
                include_pdf=include_pdf,
                artifact_content_types=artifact_content_types,
                package_content_type=package_content_type,
                actor=actor,
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "FulfilmentService failed outside the BIMAP application-error contract.",
                component=_COMPONENT,
                operation="execute",
                context={
                    "order_id": order_id,
                    "report_id": report_id,
                    **lower_error_context(exc),
                },
                cause=exc,
            ) from exc

        if not isinstance(result, FulfilmentResult):
            raise AppIntegrityError(
                "Release-report service returned an unsupported result type.",
                component=_COMPONENT,
                operation="execute",
                field="result",
                context={"received_type": type(result).__name__},
            )

        logger.info(
            {
                "event": "release_report_command_completed",
                "order_id": result.order.order_id,
                "report_id": result.manifest.report_id,
                "artifact_count": len(result.artifact_objects),
                "package_size_bytes": result.package_object.size_bytes,
                "state": result.order.state.value,
            }
        )
        return result


__all__ = ["ReleaseReport"]