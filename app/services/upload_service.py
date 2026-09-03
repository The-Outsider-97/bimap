"""
Application service for secure BIMAP upload staging and acceptance.

``UploadService`` coordinates only responsibilities supported by the current
BIMAP ports and order lifecycle:

* upload lifecycle transitions (draft -> uploading -> upload_validated or
  upload_rejected);
* optional assignment of an opaque upload-session identifier;
* streaming staging through ``Storage``;
* malware scanning through ``Malware``;
* fail-closed acceptance: only an explicit ``clean`` verdict is accepted.

The storage port does not expose presigned URLs or provider-specific upload-slot
creation, so this service does not fabricate such an API.  Likewise, it does not
invent extension/MIME allowlists, product file counts, size thresholds, or
archive-expansion limits; those must come from configured ingress/product policy
when such policy is defined.

A scanner timeout/unavailability leaves staged content available for a later
scan retry.  An explicit malicious or indeterminate verdict is not treated as an
infrastructure failure: the convenience ``stage_and_validate`` operation removes
the staged object and raises an application validation error so unapproved
content is never returned as accepted evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO

from ..ports.clock import Clock
from ..ports.malware import *
from ..ports.repositories import Repository
from ..ports.storage import Storage, StoredObject
from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...domain.orders.models import Order
from ...domain.orders.states import OrderState
from ...domain.orders.transitions import OrderTransitions
from ...domain.utils.domain_errors import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Upload Service")
printer = PrettyPrinter()

_COMPONENT = "upload_service"


def _translate_domain_error(
    exc: DomainError,
    *,
    operation: str,
    message: str,
    field: str | None = None,
) -> AppError:
    """Translate order-domain failures into the application error vocabulary."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Translating upload order-domain failure",
        event="upload_service_domain_error_translate_start",
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


def _normalize_metadata(
    value: Mapping[str, Any] | None,
    *,
    operation: str,
) -> dict[str, Any]:
    """Normalize optional order-event metadata without upload content."""
    announce_app_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing upload lifecycle metadata",
        event="upload_service_metadata_normalize_start",
        context={"operation": operation},
    )
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UnsupportedAppInputError(
            "Upload lifecycle metadata must be a mapping or None.",
            component=_COMPONENT,
            operation=operation,
            field="metadata",
            context={"received_type": type(value).__name__},
        )
    primitive = to_app_primitive(dict(value), field=f"{operation}.metadata")
    if not isinstance(primitive, dict):
        raise AppIntegrityError(
            "Upload lifecycle metadata did not normalize to a JSON object.",
            component=_COMPONENT,
            operation=operation,
            field="metadata",
        )
    return primitive


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """Bind clean scanner evidence to the exact staged storage object."""

    order_id: str
    stored_object: StoredObject
    malware_scan: MalwareScanResult

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating accepted upload result",
            event="upload_service_result_validate_start",
            context={
                "order_id": self.order_id,
                "object_id": getattr(self.stored_object, "object_id", None),
            },
        )
        object.__setattr__(
            self,
            "order_id",
            require_app_text(
                self.order_id,
                field="order_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_upload_result",
            ),
        )
        if not isinstance(self.stored_object, StoredObject):
            raise UnsupportedAppInputError(
                "ValidatedUpload requires StoredObject metadata.",
                component=_COMPONENT,
                operation="validate_upload_result",
                field="stored_object",
                context={"received_type": type(self.stored_object).__name__},
            )
        if not isinstance(self.malware_scan, MalwareScanResult):
            raise UnsupportedAppInputError(
                "ValidatedUpload requires MalwareScanResult metadata.",
                component=_COMPONENT,
                operation="validate_upload_result",
                field="malware_scan",
                context={"received_type": type(self.malware_scan).__name__},
            )
        if self.malware_scan.object_id != self.stored_object.object_id:
            raise AppIntegrityError(
                "Malware scan belongs to a different staged storage object.",
                component=_COMPONENT,
                operation="validate_upload_result",
                field="malware_scan.object_id",
                context={
                    "stored_object_id": self.stored_object.object_id,
                    "scan_object_id": self.malware_scan.object_id,
                },
            )
        if not self.malware_scan.is_clean:
            raise AppIntegrityError(
                "ValidatedUpload cannot represent a non-clean malware verdict.",
                component=_COMPONENT,
                operation="validate_upload_result",
                field="malware_scan.verdict",
                context={"verdict": self.malware_scan.verdict.value},
            )

    def to_dict(self) -> dict[str, Any]:
        """Return content-free validation metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing accepted upload result",
            event="upload_service_result_to_dict_start",
            context={"order_id": self.order_id, "object_id": self.stored_object.object_id},
        )
        return {
            "order_id": self.order_id,
            "stored_object": self.stored_object.to_dict(),
            "malware_scan": self.malware_scan.to_dict(),
        }


class UploadService:
    """Coordinate secure staging while keeping upload policy explicit."""

    def __init__(
        self,
        repository: Repository,
        malware: Malware,
        clock: Clock,
        storage: Storage,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing upload service",
            event="upload_service_init_start",
        )
        dependencies = (
            ("repository", repository, Repository),
            ("malware", malware, Malware),
            ("clock", clock, Clock),
            ("storage", storage, Storage),
        )
        for field, value, expected in dependencies:
            if not isinstance(value, expected):
                raise AppConfigurationError(
                    f"{field} must implement the BIMAP {expected.__name__} port.",
                    component=_COMPONENT,
                    operation="initialize",
                    field=field,
                    context={"received_type": type(value).__name__},
                )

        self.repository = repository
        self.malware = malware
        self.clock = clock
        self.storage = storage
        logger.info({"event": "upload_service_initialized"})

    def _require_order(self, order_id: str, *, operation: str) -> Order:
        """Load one authoritative order."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Loading upload order",
            event="upload_service_order_load_start",
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
                "Upload operation references an order that does not exist.",
                component=_COMPONENT,
                operation=operation,
                field="order_id",
                context={"order_id": target},
            )
        return order

    def _require_uploading(self, order: Order, *, operation: str) -> None:
        """Require the canonical state in which staged upload changes are allowed."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating order upload state",
            event="upload_service_state_validate_start",
            context={"operation": operation, "order_id": order.order_id, "state": order.state.value},
        )
        if order.state is not OrderState.UPLOADING:
            raise AppValidationError(
                "Upload staging/scanning requires the order to be in uploading state.",
                component=_COMPONENT,
                operation=operation,
                field="order.state",
                context={
                    "order_id": order.order_id,
                    "current_state": order.state.value,
                    "required_state": OrderState.UPLOADING.value,
                },
            )

    def _persist_order(self, previous: Order, updated: Order, *, operation: str) -> Order:
        """Persist one updated order using optimistic concurrency."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Persisting upload order change",
            event="upload_service_order_persist_start",
            context={"operation": operation, "order_id": previous.order_id},
        )
        if updated is previous:
            return previous
        persisted = self.repository.save_order(updated, expected_version=previous.version)
        if (
            persisted.order_id != updated.order_id
            or persisted.version != updated.version
            or persisted.state is not updated.state
        ):
            raise AppIntegrityError(
                "Repository write-back changed upload order identity/version/state.",
                component=_COMPONENT,
                operation=operation,
                field="persisted_order",
                context={
                    "expected_order_id": updated.order_id,
                    "returned_order_id": persisted.order_id,
                    "expected_version": updated.version,
                    "returned_version": persisted.version,
                    "expected_state": updated.state.value,
                    "returned_state": persisted.state.value,
                },
            )
        return persisted

    def _transition_loaded(
        self,
        order: Order,
        target: OrderState,
        *,
        idempotency_key: str,
        actor: str | None,
        reason: str | None,
        metadata: Mapping[str, Any] | None,
        operation: str,
    ) -> Order:
        """Apply one upload lifecycle transition and persist it with CAS."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Applying upload lifecycle transition",
            event="upload_service_transition_start",
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
        normalized_reason = optional_app_text(
            reason,
            field="reason",
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
                reason=normalized_reason,
                metadata=_normalize_metadata(metadata, operation=operation),
            )
        except AppError:
            raise
        except DomainError as exc:
            raise _translate_domain_error(
                exc,
                operation=operation,
                message="Requested upload lifecycle transition is not valid.",
                field="target_state",
            ) from exc

        if not transition.applied:
            return transition.order
        return self._persist_order(order, transition.order, operation=operation)

    def begin_upload(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        upload_session_id: str | None = None,
        actor: str | None = None,
    ) -> Order:
        """Optionally bind an opaque session and enter the uploading state."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Beginning upload lifecycle",
            event="upload_service_begin_start",
            context={"order_id": order_id, "has_upload_session": upload_session_id is not None},
        )
        order = self._require_order(order_id, operation="begin_upload")
        key = require_app_text(
            idempotency_key,
            field="idempotency_key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="begin_upload",
            max_length=512,
        )
        session_id = (
            None
            if upload_session_id is None
            else require_app_text(
                upload_session_id,
                field="upload_session_id",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="begin_upload",
            )
        )

        existing_event = order.event_for_idempotency_key(key)
        if existing_event is not None:
            if existing_event.to_state is not OrderState.UPLOADING:
                raise AppIntegrityError(
                    "Upload idempotency key is already bound to a different lifecycle transition.",
                    component=_COMPONENT,
                    operation="begin_upload",
                    field="idempotency_key",
                    context={
                        "order_id": order.order_id,
                        "existing_target": existing_event.to_state.value,
                    },
                )
            if session_id is not None and order.upload_session_id != session_id:
                raise AppIntegrityError(
                    "Idempotent upload replay supplied a different upload session identifier.",
                    component=_COMPONENT,
                    operation="begin_upload",
                    field="upload_session_id",
                    context={"order_id": order.order_id},
                )
            return order

        if session_id is not None:
            try:
                with_session = order.with_upload_session(
                    session_id,
                    changed_at=self.clock.now(),
                )
            except DomainError as exc:
                raise _translate_domain_error(
                    exc,
                    operation="begin_upload",
                    message="Upload session cannot be assigned to the order timeline.",
                    field="upload_session_id",
                ) from exc
            order = self._persist_order(order, with_session, operation="begin_upload")

        return self._transition_loaded(
            order,
            OrderState.UPLOADING,
            idempotency_key=key,
            actor=actor,
            reason=None,
            metadata=None,
            operation="begin_upload",
        )

    def scan_staged_upload(
        self,
        order_id: str,
        object_id: str,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> MalwareScanResult:
        """Scan one existing staged object without changing order/storage state."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Scanning staged upload",
            event="upload_service_scan_start",
            context={"order_id": order_id, "object_id": object_id},
        )
        order = self._require_order(order_id, operation="scan_staged_upload")
        self._require_uploading(order, operation="scan_staged_upload")
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="scan_staged_upload",
        )

        stored = self.storage.stat(target_id)
        if stored is None:
            raise AppValidationError(
                "Staged upload object does not exist.",
                component=_COMPONENT,
                operation="scan_staged_upload",
                field="object_id",
                context={"object_id": target_id, "order_id": order.order_id},
            )

        supplied_content_type = optional_app_text(
            content_type,
            field="content_type",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="scan_staged_upload",
        )
        if (
            supplied_content_type is not None
            and stored.content_type is not None
            and supplied_content_type != stored.content_type
        ):
            raise AppIntegrityError(
                "Caller content type conflicts with stored-object metadata.",
                component=_COMPONENT,
                operation="scan_staged_upload",
                field="content_type",
                context={"object_id": target_id},
            )
        scan_content_type = supplied_content_type or stored.content_type

        stream = self.storage.open(target_id)
        with closing(stream):
            return self.malware.scan(
                stream,
                object_id=target_id,
                filename=filename,
                content_type=scan_content_type,
                size_bytes=stored.size_bytes,
            )

    def stage_and_validate(
        self,
        order_id: str,
        stream: BinaryIO,
        *,
        object_id: str,
        filename: str | None = None,
        content_type: str | None = None,
        expected_size_bytes: int | None = None,
        expected_hash: str | None = None,
        hash_algorithm: str = "sha256",
    ) -> ValidatedUpload:
        """Stage one binary object, scan it, and return only an explicit clean result.

        The staged stream is consumed by storage exactly once.  The object is
        reopened from storage for malware scanning, so large uploads do not need
        to be duplicated into memory or rewound by the caller.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Staging and validating upload",
            event="upload_service_stage_validate_start",
            context={"order_id": order_id, "object_id": object_id},
        )
        order = self._require_order(order_id, operation="stage_and_validate")
        self._require_uploading(order, operation="stage_and_validate")
        target_stream = require_binary_stream(
            stream,
            field="stream",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="stage_and_validate",
        )
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="stage_and_validate",
        )

        stored = self.storage.put(
            target_stream,
            object_id=target_id,
            content_type=content_type,
            hash_algorithm=hash_algorithm,
            expected_size_bytes=expected_size_bytes,
            expected_hash=expected_hash,
        )
        try:
            scan = self.scan_staged_upload(
                order.order_id,
                target_id,
                filename=filename,
                content_type=stored.content_type,
            )
        except AppValidationError:
            # If the order ceased to be uploadable between storage and scan, the
            # just-staged object must not remain silently admitted. Scanner/port
            # outages use separate AppPort errors and intentionally retain the
            # object for an idempotent later scan retry.
            try:
                self.storage.delete(target_id)
            except StorageError as cleanup_exc:
                raise AppIntegrityError(
                    "Invalid staged upload could not be removed after validation failed.",
                    component=_COMPONENT,
                    operation="stage_and_validate",
                    field="object_id",
                    context={
                        "order_id": order.order_id,
                        "object_id": target_id,
                        **lower_error_context(cleanup_exc),
                    },
                    cause=cleanup_exc,
                ) from cleanup_exc
            raise

        if scan.verdict is not MalwareVerdict.CLEAN:
            try:
                self.storage.delete(target_id)
            except StorageError as exc:
                raise AppIntegrityError(
                    "Rejected staged upload could not be removed from storage.",
                    component=_COMPONENT,
                    operation="stage_and_validate",
                    field="object_id",
                    context={
                        "object_id": target_id,
                        "malware_verdict": scan.verdict.value,
                        **lower_error_context(exc),
                    },
                    cause=exc,
                ) from exc

            raise AppValidationError(
                "Upload was not accepted because malware scanning did not return a clean verdict.",
                component=_COMPONENT,
                operation="stage_and_validate",
                field="malware_verdict",
                context={
                    "order_id": order.order_id,
                    "object_id": target_id,
                    "malware_verdict": scan.verdict.value,
                    "threat_identified": scan.threat_name is not None,
                },
            )

        result = ValidatedUpload(
            order_id=order.order_id,
            stored_object=stored,
            malware_scan=scan,
        )
        logger.info(
            {
                "event": "upload_service_upload_accepted",
                "order_id": order.order_id,
                "object_id": target_id,
                "size_bytes": stored.size_bytes,
                "hash_algorithm": stored.hash_algorithm,
            }
        )
        return result

    def mark_uploads_validated(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        actor: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Order:
        """Enter ``upload_validated`` after the caller has validated the full upload set.

        This method intentionally does not infer completeness from one clean
        staged file.  Product-specific required files/counts are not encoded in
        the current storage/malware ports and must be checked before this method
        is called.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Marking order uploads validated",
            event="upload_service_mark_validated_start",
            context={"order_id": order_id},
        )
        order = self._require_order(order_id, operation="mark_uploads_validated")
        return self._transition_loaded(
            order,
            OrderState.UPLOAD_VALIDATED,
            idempotency_key=idempotency_key,
            actor=actor,
            reason=None,
            metadata=metadata,
            operation="mark_uploads_validated",
        )

    def reject_uploads(
        self,
        order_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
        actor: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Order:
        """Record the canonical upload-rejected order outcome explicitly."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Rejecting order uploads",
            event="upload_service_reject_start",
            context={"order_id": order_id},
        )
        order = self._require_order(order_id, operation="reject_uploads")
        return self._transition_loaded(
            order,
            OrderState.UPLOAD_REJECTED,
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            metadata=metadata,
            operation="reject_uploads",
        )

    def delete_staged_upload(self, object_id: str) -> bool:
        """Delete one explicitly identified staged object."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Deleting staged upload",
            event="upload_service_delete_start",
            context={"object_id": object_id},
        )
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="delete_staged_upload",
        )
        return self.storage.delete(target_id)


__all__ = [
    "ValidatedUpload",
    "UploadService",
]