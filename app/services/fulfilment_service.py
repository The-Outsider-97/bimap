"""
Application service for BIMAP report finalization and delivery publication.

``FulfilmentService`` coordinates governance-aware release, report generation,
deterministic package construction, object publication, manifest persistence,
order delivery state, notifications, and explicit retention cleanup.

It preserves the lower-layer boundaries already present in BIMAP:

* ``ReportBuilder`` generates validated report artifacts and ``ReportManifest``;
* ``PackageBuilder`` builds/verifies the deterministic delivery ZIP;
* ``Storage`` publishes bytes but does not invent object naming or retention;
* ``Repository`` persists the versioned report manifest and order aggregate;
* ``Review`` remains the authority for whether an individual reviewed finding
  may be released;
* ``OrderTransitions`` remains the sole lifecycle transition authority;
* ``Notifications`` delivers caller-defined logical events.

No distributed transaction is claimed.  Publication uses caller-supplied stable
object IDs plus integrity metadata to make retries fail-safe/idempotent where the
current ports permit it.  Notification is deliberately a separate method so a
notification outage cannot make an already-published report appear undelivered.
"""

from __future__ import annotations

import hashlib

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from types import MappingProxyType
from typing import Any

from ..ports.clock import Clock
from ..ports.notifications import Notifications
from ..ports.repositories import Repository
from ..ports.storage import Storage, StoredObject
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.evidence import EvidenceContract
from ...contracts.finding import FindingContract
from ...contracts.report_manifest import ReportArtifactContract, ReportManifest
from ...contracts.requirement import RequirementContract
from ...domain.evidence.models import EvidenceItem
from ...domain.governance.review import Review
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from ...domain.orders.transitions import OrderTransitions
from ...domain.utils.domain_errors import DomainError, DomainInvariantError
from ...reporting.package_builder import PackageBuilder
from ...reporting.report_builder import ReportBuildResult, ReportBuilder
from ...reporting.utils.reporting_errors import ReportingError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Fulfilment Service")
printer = PrettyPrinter()

_COMPONENT = "fulfilment_service"


def _translate_domain_error(
    exc: DomainError,
    *,
    operation: str,
    message: str,
    field: str | None = None,
) -> AppError:
    """Translate order-domain failures at the fulfilment application boundary."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Translating fulfilment order-domain failure",
        event="fulfilment_domain_error_translate_start",
        context={"operation": operation, "error_type": type(exc).__name__},
    )
    error_type = AppIntegrityError if isinstance(exc, DomainInvariantError) else AppValidationError
    return error_type(
        message,
        component=_COMPONENT,
        operation=operation,
        field=field,
        context=lower_error_context(exc),
        cause=exc,
    )


def _materialize_records(
    value: Iterable[Any],
    *,
    accepted_type: type | tuple[type, ...],
    field: str,
    operation: str,
) -> tuple[Any, ...]:
    """Materialize a typed iterable once without accepting scalar/mapping traps."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action=f"Normalizing fulfilment records: {field}",
        event="fulfilment_records_normalize_start",
        context={"operation": operation, "field": field},
    )
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise UnsupportedAppInputError(
            "Fulfilment record collection must be an iterable of typed records.",
            component=_COMPONENT,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
        )
    try:
        records = tuple(value)
    except TypeError as exc:
        raise UnsupportedAppInputError(
            "Fulfilment record collection must be iterable.",
            component=_COMPONENT,
            operation=operation,
            field=field,
            context={"received_type": type(value).__name__},
            cause=exc,
        ) from exc

    for index, record in enumerate(records):
        if not isinstance(record, accepted_type):
            raise UnsupportedAppInputError(
                "Fulfilment record collection contains an unsupported type.",
                component=_COMPONENT,
                operation=operation,
                field=f"{field}[{index}]",
                context={"received_type": type(record).__name__},
            )
    return records


def _normalize_object_ids(values: Iterable[str], *, operation: str) -> tuple[str, ...]:
    """Normalize a unique ordered set of explicit storage object IDs."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing retention object identifiers",
        event="fulfilment_object_ids_normalize_start",
        context={"operation": operation},
    )
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedAppInputError(
            "object_ids must be an iterable of individual object identifiers.",
            component=_COMPONENT,
            operation=operation,
            field="object_ids",
            context={"received_type": type(values).__name__},
        )
    try:
        raw = tuple(values)
    except TypeError as exc:
        raise UnsupportedAppInputError(
            "object_ids must be iterable.",
            component=_COMPONENT,
            operation=operation,
            field="object_ids",
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        object_id = require_app_text(
            value,
            field=f"object_ids[{index}]",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )
        if object_id in seen:
            continue
        seen.add(object_id)
        normalized.append(object_id)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class FulfilmentResult:
    """Content-free references/metadata for one completed report publication."""

    order: Order
    manifest: ReportManifest
    artifact_objects: Mapping[str, StoredObject]
    package_object: StoredObject

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating fulfilment result",
            event="fulfilment_result_validate_start",
            context={
                "order_id": getattr(self.order, "order_id", None),
                "report_id": getattr(self.manifest, "report_id", None),
            },
        )
        if not isinstance(self.order, Order):
            raise UnsupportedAppInputError(
                "FulfilmentResult requires an Order.",
                component=_COMPONENT,
                operation="validate_result",
                field="order",
                context={"received_type": type(self.order).__name__},
            )
        if not isinstance(self.manifest, ReportManifest):
            raise UnsupportedAppInputError(
                "FulfilmentResult requires a ReportManifest.",
                component=_COMPONENT,
                operation="validate_result",
                field="manifest",
                context={"received_type": type(self.manifest).__name__},
            )
        if self.manifest.order_id != self.order.order_id:
            raise AppIntegrityError(
                "Fulfilment manifest belongs to a different order.",
                component=_COMPONENT,
                operation="validate_result",
                field="manifest.order_id",
                context={
                    "order_id": self.order.order_id,
                    "manifest_order_id": self.manifest.order_id,
                },
            )
        if self.order.state is not OrderState.DELIVERED:
            raise AppIntegrityError(
                "FulfilmentResult may only represent a delivered order.",
                component=_COMPONENT,
                operation="validate_result",
                field="order.state",
                context={"state": self.order.state.value},
            )
        if not isinstance(self.artifact_objects, Mapping):
            raise UnsupportedAppInputError(
                "artifact_objects must be a mapping.",
                component=_COMPONENT,
                operation="validate_result",
                field="artifact_objects",
                context={"received_type": type(self.artifact_objects).__name__},
            )

        normalized: dict[str, StoredObject] = {}
        manifest_filenames = {artifact.filename for artifact in self.manifest.artifacts}
        for filename, stored in self.artifact_objects.items():
            name = require_app_text(
                filename,
                field="artifact_objects.filename",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_result",
            )
            if name not in manifest_filenames:
                raise AppIntegrityError(
                    "Fulfilment result contains storage metadata for a non-manifest artifact.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="artifact_objects",
                    context={"filename": name},
                )
            if not isinstance(stored, StoredObject):
                raise UnsupportedAppInputError(
                    "artifact_objects values must be StoredObject records.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=f"artifact_objects[{name}]",
                    context={"received_type": type(stored).__name__},
                )
            manifest_artifact = self.manifest.artifact_by_filename(name)
            if manifest_artifact is None:
                raise AppIntegrityError(
                    "Fulfilment result could not resolve manifest artifact metadata.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=f"artifact_objects[{name}]",
                )
            if (
                stored.hash_algorithm != "sha256"
                or stored.content_hash != manifest_artifact.sha256
                or stored.size_bytes != manifest_artifact.size_bytes
            ):
                raise AppIntegrityError(
                    "Published artifact storage metadata does not match the report manifest.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=f"artifact_objects[{name}]",
                    context={
                        "artifact_id": manifest_artifact.artifact_id,
                        "object_id": stored.object_id,
                    },
                )
            normalized[name] = stored

        if set(normalized) != manifest_filenames:
            raise AppIntegrityError(
                "Fulfilment result does not contain storage metadata for every report artifact.",
                component=_COMPONENT,
                operation="validate_result",
                field="artifact_objects",
                context={
                    "missing": tuple(sorted(manifest_filenames - set(normalized))),
                    "unexpected": tuple(sorted(set(normalized) - manifest_filenames)),
                },
            )
        if not isinstance(self.package_object, StoredObject):
            raise UnsupportedAppInputError(
                "package_object must be StoredObject metadata.",
                component=_COMPONENT,
                operation="validate_result",
                field="package_object",
                context={"received_type": type(self.package_object).__name__},
            )
        if self.package_object.object_id in {item.object_id for item in normalized.values()}:
            raise AppIntegrityError(
                "Delivery package storage object must be distinct from report artifacts.",
                component=_COMPONENT,
                operation="validate_result",
                field="package_object.object_id",
                context={"object_id": self.package_object.object_id},
            )
        object.__setattr__(self, "artifact_objects", MappingProxyType(normalized))

    def to_dict(self) -> dict[str, Any]:
        """Return publication metadata without report/package bytes."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing fulfilment result",
            event="fulfilment_result_to_dict_start",
            context={"order_id": self.order.order_id, "report_id": self.manifest.report_id},
        )
        return {
            "order": self.order.to_dict(),
            "manifest": self.manifest.to_dict(),
            "artifact_objects": {
                filename: stored.to_dict()
                for filename, stored in self.artifact_objects.items()
            },
            "package_object": self.package_object.to_dict(),
        }


class FulfilmentService:
    """Build, publish, persist, deliver, notify, and expire report artifacts."""

    def __init__(
        self,
        repository: Repository,
        storage: Storage,
        notifications: Notifications | None,
        clock: Clock,
        *,
        report_builder: ReportBuilder | None = None,
        package_builder: PackageBuilder | None = None,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing fulfilment service",
            event="fulfilment_service_init_start",
            context={"notifications_configured": notifications is not None},
        )
        if not isinstance(repository, Repository):
            raise AppConfigurationError(
                "repository must implement the BIMAP Repository port.",
                component=_COMPONENT,
                operation="initialize",
                field="repository",
                context={"received_type": type(repository).__name__},
            )
        if not isinstance(storage, Storage):
            raise AppConfigurationError(
                "storage must implement the BIMAP Storage port.",
                component=_COMPONENT,
                operation="initialize",
                field="storage",
                context={"received_type": type(storage).__name__},
            )
        if notifications is not None and not isinstance(notifications, Notifications):
            raise AppConfigurationError(
                "notifications must implement Notifications or be None.",
                component=_COMPONENT,
                operation="initialize",
                field="notifications",
                context={"received_type": type(notifications).__name__},
            )
        if not isinstance(clock, Clock):
            raise AppConfigurationError(
                "clock must implement the BIMAP Clock port.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
                context={"received_type": type(clock).__name__},
            )
        if report_builder is not None and not isinstance(report_builder, ReportBuilder):
            raise AppConfigurationError(
                "report_builder must be a ReportBuilder or None.",
                component=_COMPONENT,
                operation="initialize",
                field="report_builder",
                context={"received_type": type(report_builder).__name__},
            )
        if package_builder is not None and not isinstance(package_builder, PackageBuilder):
            raise AppConfigurationError(
                "package_builder must be a PackageBuilder or None.",
                component=_COMPONENT,
                operation="initialize",
                field="package_builder",
                context={"received_type": type(package_builder).__name__},
            )

        self.repository = repository
        self.storage = storage
        self.notifications = notifications
        self.clock = clock
        self.report_builder = report_builder or ReportBuilder()
        self.package_builder = package_builder or PackageBuilder()

        logger.info(
            {
                "event": "fulfilment_service_initialized",
                "notifications_configured": notifications is not None,
                "report_renderer_configured": self.report_builder.renderer is not None,
            }
        )

    def _require_order(self, order_id: str, *, operation: str) -> Order:
        """Load one required authoritative order."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading fulfilment order",
            event="fulfilment_order_load_start",
            context={"operation": operation, "order_id": order_id},
        )
        target = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )
        order = self.repository.get_order(target)
        if order is None:
            raise AppValidationError(
                "Fulfilment operation references an order that does not exist.",
                component=_COMPONENT,
                operation=operation,
                field="order_id",
                context={"order_id": target},
            )
        return order

    def _transition_loaded(
        self,
        order: Order,
        target: OrderState,
        *,
        idempotency_key: str,
        actor: str | None,
        operation: str,
    ) -> Order:
        """Apply one fulfilment lifecycle transition with optimistic persistence."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Applying fulfilment lifecycle transition",
            event="fulfilment_transition_start",
            context={"operation": operation, "order_id": order.order_id, "target": target.value},
        )
        key = require_app_text(
            idempotency_key,
            field="idempotency_key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
            max_length=512,
        )
        normalized_actor = optional_app_text(
            actor,
            field="actor",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation=operation,
        )
        try:
            transition = OrderTransitions.apply(
                order,
                target,
                idempotency_key=key,
                occurred_at=self.clock.now(),
                expected_version=order.version,
                actor=normalized_actor,
            )
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation=operation,
                message="Requested fulfilment lifecycle transition is not valid.",
                field="target_state",
            ) from exc

        if not transition.applied:
            return transition.order

        persisted = self.repository.save_order(
            transition.order,
            expected_version=order.version,
        )
        if (
            persisted.order_id != transition.order.order_id
            or persisted.version != transition.order.version
            or persisted.state is not transition.order.state
        ):
            raise AppIntegrityError(
                "Repository write-back changed fulfilment order identity/version/state.",
                component=_COMPONENT,
                operation=operation,
                field="persisted_order",
            )
        return persisted

    def _validate_release_reviews(
        self,
        findings: tuple[FindingContract, ...],
        reviews: tuple[Review, ...],
    ) -> tuple[Review, ...]:
        """Validate persisted governance and return authoritative review records."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating governance release decisions",
            event="fulfilment_governance_validate_start",
            context={"finding_count": len(findings), "review_count": len(reviews)},
        )

        finding_ids: set[str] = set()
        findings_by_id: dict[str, FindingContract] = {}
        for finding in findings:
            if finding.finding_id in finding_ids:
                raise AppIntegrityError(
                    "Release findings contain a duplicate finding identifier.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="findings",
                    context={"finding_id": finding.finding_id},
                )
            finding_ids.add(finding.finding_id)
            findings_by_id[finding.finding_id] = finding

        authoritative_reviews: list[Review] = []
        reviews_by_finding: dict[str, Review] = {}
        for review in reviews:
            persisted = self.repository.get_review(review.review_id)
            if persisted is None:
                raise AppValidationError(
                    "Release input references a governance review that is not persisted.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="reviews",
                    context={"review_id": review.review_id, "finding_id": review.finding_id},
                )
            if persisted.to_dict() != review.to_dict():
                raise AppIntegrityError(
                    "Release input governance review differs from authoritative persistence.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="reviews",
                    context={"review_id": review.review_id, "finding_id": review.finding_id},
                )
            if persisted.finding_id in reviews_by_finding:
                raise AppIntegrityError(
                    "Release input contains multiple governance reviews for one finding.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="reviews",
                    context={"finding_id": persisted.finding_id},
                )
            reviews_by_finding[persisted.finding_id] = persisted
            authoritative_reviews.append(persisted)

        for finding_id in finding_ids:
            review = reviews_by_finding.get(finding_id)
            if review is None:
                # Not every deterministic finding requires manual governance.
                continue

            contract_finding = findings_by_id[finding_id]
            domain_finding = review.finding
            if (
                domain_finding.title != contract_finding.title
                or domain_finding.severity.level.label != contract_finding.severity
                or domain_finding.confidence.score != contract_finding.confidence
            ):
                raise AppIntegrityError(
                    "Governance review and release finding disagree on shared canonical finding fields.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="reviews",
                    context={
                        "finding_id": finding_id,
                        "review_id": review.review_id,
                    },
                )

            if not review.finding_release_allowed():
                current = review.current_decision()
                raise AppValidationError(
                    "A finding selected for release is not approved by its governance review.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="findings",
                    context={
                        "finding_id": finding_id,
                        "review_id": review.review_id,
                        "review_outcome": None if current is None else current.outcome.value,
                    },
                )

        return tuple(authoritative_reviews)

    def _validate_storage_mapping(
        self,
        build: ReportBuildResult,
        *,
        artifact_object_ids: Mapping[str, str],
        package_object_id: str,
    ) -> tuple[dict[str, str], str]:
        """Validate explicit filename->storage identity without inventing naming."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating report storage object mapping",
            event="fulfilment_storage_mapping_validate_start",
            context={"report_id": build.manifest.report_id},
        )
        if not isinstance(artifact_object_ids, Mapping):
            raise UnsupportedAppInputError(
                "artifact_object_ids must map generated filename to storage object ID.",
                component=_COMPONENT,
                operation="release_report",
                field="artifact_object_ids",
                context={"received_type": type(artifact_object_ids).__name__},
            )

        generated = set(build.artifacts)
        supplied = set(artifact_object_ids)
        if generated != supplied:
            raise AppValidationError(
                "artifact_object_ids must contain exactly the generated artifact filenames.",
                component=_COMPONENT,
                operation="release_report",
                field="artifact_object_ids",
                context={
                    "missing": tuple(sorted(generated - supplied)),
                    "unexpected": tuple(sorted(supplied - generated)),
                },
            )

        normalized: dict[str, str] = {}
        object_ids: set[str] = set()
        for filename in sorted(generated):
            object_id = require_app_text(
                artifact_object_ids[filename],
                field=f"artifact_object_ids[{filename}]",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="release_report",
            )
            if object_id in object_ids:
                raise AppIntegrityError(
                    "Multiple report artifacts map to the same storage object ID.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="artifact_object_ids",
                    context={"object_id": object_id},
                )
            object_ids.add(object_id)
            normalized[filename] = object_id

        package_id = require_app_text(
            package_object_id,
            field="package_object_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="release_report",
        )
        if package_id in object_ids:
            raise AppIntegrityError(
                "Delivery package object ID must be distinct from managed report artifacts.",
                component=_COMPONENT,
                operation="release_report",
                field="package_object_id",
                context={"object_id": package_id},
            )
        return normalized, package_id

    def _publish_artifact(
        self,
        manifest: ReportManifest,
        artifact: ReportArtifactContract,
        payload: bytes,
        *,
        object_id: str,
        content_type: str | None,
    ) -> StoredObject:
        """Publish/reuse one immutable report artifact using manifest integrity."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Publishing report artifact",
            event="fulfilment_artifact_publish_start",
            context={
                "report_id": manifest.report_id,
                "artifact_id": artifact.artifact_id,
                "object_id": object_id,
            },
        )
        existing = self.storage.stat(object_id)
        if existing is not None:
            if (
                existing.hash_algorithm != "sha256"
                or existing.content_hash != artifact.sha256
                or existing.size_bytes != artifact.size_bytes
                or (content_type is not None and existing.content_type != content_type)
            ):
                raise AppIntegrityError(
                    "Existing storage object conflicts with report artifact integrity metadata.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="artifact_object_ids",
                    context={
                        "report_id": manifest.report_id,
                        "artifact_id": artifact.artifact_id,
                        "object_id": object_id,
                    },
                )
            return existing

        return self.storage.put_report_artifact(
            BytesIO(payload),
            manifest,
            artifact_id=artifact.artifact_id,
            object_id=object_id,
            content_type=content_type,
        )

    def _publish_package(
        self,
        package: bytes,
        *,
        object_id: str,
        content_type: str | None,
    ) -> StoredObject:
        """Publish/reuse the deterministic package using an explicit SHA-256 check."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Publishing delivery package",
            event="fulfilment_package_publish_start",
            context={"object_id": object_id, "size_bytes": len(package)},
        )
        digest = hashlib.sha256(package).hexdigest()
        existing = self.storage.stat(object_id)
        if existing is not None:
            if (
                existing.hash_algorithm != "sha256"
                or existing.content_hash != digest
                or existing.size_bytes != len(package)
                or (content_type is not None and existing.content_type != content_type)
            ):
                raise AppIntegrityError(
                    "Existing package storage object conflicts with deterministic package bytes.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="package_object_id",
                    context={"object_id": object_id},
                )
            return existing

        return self.storage.put(
            BytesIO(package),
            object_id=object_id,
            content_type=content_type,
            hash_algorithm="sha256",
            expected_size_bytes=len(package),
            expected_hash=digest,
        )

    def _persist_manifest(self, manifest: ReportManifest) -> ReportManifest:
        """Persist one immutable manifest idempotently by stable report ID."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Persisting report manifest",
            event="fulfilment_manifest_persist_start",
            context={"report_id": manifest.report_id},
        )
        existing = self.repository.get_report_manifest(manifest.report_id)
        if existing is not None:
            if existing.to_dict() != manifest.to_dict():
                raise AppIntegrityError(
                    "Report identifier is already bound to a different immutable manifest.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="report_id",
                    context={"report_id": manifest.report_id},
                )
            return existing

        persisted = self.repository.save_report_manifest(manifest)
        if persisted.to_dict() != manifest.to_dict():
            raise AppIntegrityError(
                "Repository changed immutable report manifest data while saving.",
                component=_COMPONENT,
                operation="release_report",
                field="manifest",
                context={"report_id": manifest.report_id},
            )
        return persisted

    def release_report(
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
        """Build, publish and mark one governed BIMAP report delivered.

        ``artifact_ids`` are report-manifest identities consumed by
        ``ReportBuilder``. ``artifact_object_ids`` are separate storage object
        identities keyed by generated filename.  Keeping them explicit respects
        the storage-port guarantee that manifest artifact IDs are not a global
        storage naming convention.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Releasing BIMAP report",
            event="fulfilment_release_start",
            context={"order_id": order_id, "report_id": report_id, "include_pdf": include_pdf},
        )

        finding_records = _materialize_records(
            findings,
            accepted_type=FindingContract,
            field="findings",
            operation="release_report",
        )
        evidence_records = _materialize_records(
            evidence,
            accepted_type=(EvidenceContract, EvidenceItem),
            field="evidence",
            operation="release_report",
        )
        requirement_records = _materialize_records(
            requirements,
            accepted_type=RequirementContract,
            field="requirements",
            operation="release_report",
        )
        review_records = _materialize_records(
            reviews,
            accepted_type=Review,
            field="reviews",
            operation="release_report",
        )
        review_records = self._validate_release_reviews(finding_records, review_records)
        released_finding_ids = {finding.finding_id for finding in finding_records}
        report_reviews = tuple(
            review for review in review_records if review.finding_id in released_finding_ids
        )

        order = self._require_order(order_id, operation="release_report")
        releasable_states = {
            OrderState.GOVERNANCE_REVIEW,
            OrderState.PACKAGING,
            OrderState.DELIVERED,
        }
        if order.state not in releasable_states:
            raise AppValidationError(
                "Report release requires governance-review/packaging state or an idempotent delivered replay.",
                component=_COMPONENT,
                operation="release_report",
                field="order.state",
                context={
                    "order_id": order.order_id,
                    "current_state": order.state.value,
                    "allowed_states": tuple(sorted(state.value for state in releasable_states)),
                },
            )

        order = self._transition_loaded(
            order,
            OrderState.PACKAGING,
            idempotency_key=packaging_idempotency_key,
            actor=actor,
            operation="release_report",
        )
        packaging_event = order.event_for_idempotency_key(packaging_idempotency_key)
        if packaging_event is None or packaging_event.to_state is not OrderState.PACKAGING:
            raise AppIntegrityError(
                "Packaging transition did not preserve its lifecycle event identity.",
                component=_COMPONENT,
                operation="release_report",
                field="packaging_idempotency_key",
                context={"order_id": order.order_id},
            )
        report_generated_at = packaging_event.occurred_at

        if expires_at is not None:
            normalized_expiry = ensure_app_utc_datetime(
                expires_at,
                field="expires_at",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="release_report",
            )
        else:
            normalized_expiry = None

        normalized_content_types: dict[str, str] = {}
        if artifact_content_types is not None:
            if not isinstance(artifact_content_types, Mapping):
                raise UnsupportedAppInputError(
                    "artifact_content_types must be a mapping or None.",
                    component=_COMPONENT,
                    operation="release_report",
                    field="artifact_content_types",
                    context={"received_type": type(artifact_content_types).__name__},
                )
            for filename, content_type in artifact_content_types.items():
                name = require_app_text(
                    filename,
                    field="artifact_content_types.filename",
                    error_type=AppValidationError,
                    component=_COMPONENT,
                    operation="release_report",
                )
                normalized_content_types[name] = require_app_text(
                    content_type,
                    field=f"artifact_content_types[{name}]",
                    error_type=AppValidationError,
                    component=_COMPONENT,
                    operation="release_report",
                    max_length=256,
                )
        normalized_package_type = optional_app_text(
            package_content_type,
            field="package_content_type",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="release_report",
            max_length=256,
        )

        try:
            build = self.report_builder.build_report(
                findings=finding_records,
                evidence=evidence_records,
                requirements=requirement_records,
                reviews=report_reviews,
                report_id=report_id,
                order_id=order.order_id,
                report_version=report_version,
                generated_at=report_generated_at,
                artifact_ids=artifact_ids,
                expires_at=normalized_expiry,
                software_versions=software_versions,
                ruleset_versions=ruleset_versions,
                include_pdf=include_pdf,
            )
            package = self.package_builder.build_package(
                build.manifest,
                build.artifacts,
            )
            self.package_builder.verify_package(
                package,
                expected_manifest=build.manifest,
            )
        except ReportingError as exc:
            raise AppError(
                "Report generation or deterministic package construction failed.",
                component=_COMPONENT,
                operation="release_report",
                context={"order_id": order.order_id, "report_id": report_id, **lower_error_context(exc)},
                cause=exc,
            ) from exc

        storage_map, package_id = self._validate_storage_mapping(
            build,
            artifact_object_ids=artifact_object_ids,
            package_object_id=package_object_id,
        )
        unexpected_content_types = set(normalized_content_types) - set(build.artifacts)
        if unexpected_content_types:
            raise AppValidationError(
                "artifact_content_types contains filenames that were not generated.",
                component=_COMPONENT,
                operation="release_report",
                field="artifact_content_types",
                context={"unexpected": tuple(sorted(unexpected_content_types))},
            )

        published: dict[str, StoredObject] = {}
        for artifact in build.manifest.artifacts:
            payload = build.artifacts[artifact.filename]
            published[artifact.filename] = self._publish_artifact(
                build.manifest,
                artifact,
                payload,
                object_id=storage_map[artifact.filename],
                content_type=normalized_content_types.get(artifact.filename),
            )

        package_object = self._publish_package(
            package,
            object_id=package_id,
            content_type=normalized_package_type,
        )
        manifest = self._persist_manifest(build.manifest)

        delivered = self._transition_loaded(
            order,
            OrderState.DELIVERED,
            idempotency_key=delivery_idempotency_key,
            actor=actor,
            operation="release_report",
        )
        result = FulfilmentResult(
            order=delivered,
            manifest=manifest,
            artifact_objects=published,
            package_object=package_object,
        )

        logger.info(
            {
                "event": "fulfilment_report_released",
                "order_id": delivered.order_id,
                "report_id": manifest.report_id,
                "artifact_count": len(published),
                "package_size_bytes": package_object.size_bytes,
            }
        )
        return result

    def notify_report_available(
        self,
        report_id: str,
        *,
        event_type: str,
        target_ref: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Send one caller-defined notification for an already-persisted report."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Notifying report availability",
            event="fulfilment_notify_start",
            context={"report_id": report_id},
        )
        if self.notifications is None:
            raise AppConfigurationError(
                "FulfilmentService notifications dependency is not configured.",
                component=_COMPONENT,
                operation="notify_report_available",
                field="notifications",
            )

        target_report = require_app_text(
            report_id,
            field="report_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="notify_report_available",
        )
        manifest = self.repository.get_report_manifest(target_report)
        if manifest is None:
            raise AppValidationError(
                "Report manifest does not exist.",
                component=_COMPONENT,
                operation="notify_report_available",
                field="report_id",
                context={"report_id": target_report},
            )
        order = self._require_order(manifest.order_id, operation="notify_report_available")
        if order.state is not OrderState.DELIVERED:
            raise AppValidationError(
                "Report availability notification requires a delivered order.",
                component=_COMPONENT,
                operation="notify_report_available",
                field="order.state",
                context={"order_id": order.order_id, "state": order.state.value},
            )

        self.notifications.notify_report(
            order,
            manifest,
            event_type=event_type,
            target_ref=target_ref,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    def expire_delivery_if_due(
        self,
        order_id: str,
        *,
        object_ids: Iterable[str],
        idempotency_key: str,
        actor: str | None = None,
    ) -> Order:
        """Delete explicitly identified retained objects and expire a due delivery.

        The repository port intentionally has no hard-delete operation for report
        manifests, so immutable report-control metadata remains persisted.  Only
        caller-identified storage objects are deleted; this service never derives
        hidden storage keys from report IDs or filenames.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Evaluating delivery retention expiry",
            event="fulfilment_expire_start",
            context={"order_id": order_id},
        )
        order = self._require_order(order_id, operation="expire_delivery_if_due")
        if order.state not in {OrderState.DELIVERED, OrderState.EXPIRED}:
            raise AppValidationError(
                "Retention expiry applies only to delivered/expired orders.",
                component=_COMPONENT,
                operation="expire_delivery_if_due",
                field="order.state",
                context={"order_id": order.order_id, "state": order.state.value},
            )
        if order.retention_expires_at is None:
            raise AppValidationError(
                "Order has no configured retention expiry.",
                component=_COMPONENT,
                operation="expire_delivery_if_due",
                field="order.retention_expires_at",
                context={"order_id": order.order_id},
            )
        if not self.clock.is_expired(order.retention_expires_at):
            return order

        targets = _normalize_object_ids(object_ids, operation="expire_delivery_if_due")
        for object_id in targets:
            self.storage.delete(object_id)

        if order.state is OrderState.EXPIRED:
            return order
        expired = self._transition_loaded(
            order,
            OrderState.EXPIRED,
            idempotency_key=idempotency_key,
            actor=actor,
            operation="expire_delivery_if_due",
        )
        logger.info(
            {
                "event": "fulfilment_delivery_expired",
                "order_id": expired.order_id,
                "deleted_object_count": len(targets),
            }
        )
        return expired


__all__ = [
    "FulfilmentResult",
    "FulfilmentService",
]