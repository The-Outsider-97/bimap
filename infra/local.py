"""
Local concrete infrastructure adapters for BIMAP.

Location
--------
SLAI/applications/bimap/infrastructure/local.py

Purpose
-------
These adapters provide a deterministic, dependency-free local deployment for
BIMAP development, integration testing, and single-process evaluation.

They implement the existing BIMAP application ports without changing those
ports or leaking provider-specific semantics into the application layer.

Important deployment boundary
-----------------------------
This module is intentionally local/development infrastructure:

- persistence is process-local and non-durable;
- object storage is process-local and non-durable;
- queue delivery is process-local and does not supervise worker execution;
- payment operations fail closed because no payment provider is configured;
- malware scanning returns ``indeterminate`` unless the deployment explicitly
  opts into a development-only trust override.

A production deployment must replace these adapters with durable/provider-backed
implementations while preserving the same application-port contracts.
"""

from __future__ import annotations

import hashlib

from datetime import datetime, timezone
from io import BytesIO
from threading import RLock
from typing import BinaryIO

from ..app.ports.clock import Clock
from ..app.ports.malware import Malware, MalwareScanResult, MalwareVerdict
from ..app.ports.payment import Payment, PaymentCheckout, PaymentEvent
from ..app.ports.queue import Queue, QueueReceipt
from ..app.ports.repositories import Repository
from ..app.ports.storage import Storage, StoredObject
from ..app.utils.app_errors import (
    PaymentUnavailableError,
    QueueIntegrityError,
    RepositoryConflictError,
    StorageIntegrityError,
    StorageNotFoundError,
)
from ..contracts.audit_job import AuditJob
from ..contracts.report_manifest import ReportManifest
from ..domain.evidence.models import EvidenceItem
from ..domain.findings.models import Finding
from ..domain.governance.review import Review
from ..domain.orders.models import Order
from ..domain.products.models import ProductTier

from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Local Infrastructure")
printer = PrettyPrinter()


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class SystemClock(Clock):
    """UTC system clock implementation of the BIMAP ``Clock`` port."""

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local UTC clock", "info")
        super().__init__()

    def _read_utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class InMemoryRepository(Repository):
    """
    Thread-safe process-local implementation of the composite Repository port.

    The adapter preserves the port's optimistic-concurrency requirement for
    ``Order`` writes whenever ``expected_version`` is supplied.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local repository", "info")

        self._lock = RLock()
        self._orders: dict[str, Order] = {}
        self._evidence: dict[str, EvidenceItem] = {}
        self._findings: dict[str, Finding] = {}
        self._reviews: dict[str, Review] = {}
        self._reports: dict[str, ReportManifest] = {}

        super().__init__()

    def _get_order(self, order_id: str) -> Order | None:
        with self._lock:
            return self._orders.get(order_id)

    def _save_order(
        self,
        order: Order,
        *,
        expected_version: int | None,
    ) -> Order:
        with self._lock:
            current = self._orders.get(order.order_id)

            if expected_version is not None:
                actual_version = None if current is None else current.version

                if actual_version != expected_version:
                    raise RepositoryConflictError(
                        "Order optimistic-concurrency precondition failed.",
                        component="local_repository",
                        operation="save_order",
                        field="expected_version",
                        context={
                            "order_id": order.order_id,
                            "expected_version": expected_version,
                            "actual_version": actual_version,
                        },
                    )

            if current is not None and order.version < current.version:
                raise RepositoryConflictError(
                    "Refusing to persist an older Order aggregate revision.",
                    component="local_repository",
                    operation="save_order",
                    field="order.version",
                    context={
                        "order_id": order.order_id,
                        "stored_version": current.version,
                        "received_version": order.version,
                    },
                )

            self._orders[order.order_id] = order
            return order

    def _get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        with self._lock:
            return self._evidence.get(evidence_id)

    def _save_evidence(self, evidence: EvidenceItem) -> EvidenceItem:
        with self._lock:
            self._evidence[evidence.evidence_id] = evidence
            return evidence

    def _get_finding(self, finding_id: str) -> Finding | None:
        with self._lock:
            return self._findings.get(finding_id)

    def _save_finding(self, finding: Finding) -> Finding:
        with self._lock:
            self._findings[finding.finding_id] = finding
            return finding

    def _get_review(self, review_id: str) -> Review | None:
        with self._lock:
            return self._reviews.get(review_id)

    def _save_review(self, review: Review) -> Review:
        with self._lock:
            self._reviews[review.review_id] = review
            return review

    def _get_report_manifest(self, report_id: str) -> ReportManifest | None:
        with self._lock:
            return self._reports.get(report_id)

    def _save_report_manifest(self, manifest: ReportManifest) -> ReportManifest:
        with self._lock:
            self._reports[manifest.report_id] = manifest
            return manifest


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class InMemoryStorage(Storage):
    """Thread-safe process-local binary storage with integrity checking."""

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local object storage", "info")

        self._lock = RLock()
        self._objects: dict[str, tuple[bytes, StoredObject]] = {}

        super().__init__()

    def _put(
        self,
        stream: BinaryIO,
        *,
        object_id: str,
        content_type: str | None,
        hash_algorithm: str,
        expected_size_bytes: int | None,
        expected_hash: str | None,
    ) -> StoredObject:
        payload = stream.read()

        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise StorageIntegrityError(
                "Binary storage stream returned non-binary content.",
                component="local_storage",
                operation="put",
                field="stream",
                context={
                    "object_id": object_id,
                    "received_type": type(payload).__name__,
                },
            )

        data = bytes(payload)
        digest = hashlib.new(hash_algorithm, data).hexdigest()
        size_bytes = len(data)

        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            raise StorageIntegrityError(
                "Stored content size does not match expected_size_bytes.",
                component="local_storage",
                operation="put",
                field="expected_size_bytes",
                context={
                    "object_id": object_id,
                    "expected_size_bytes": expected_size_bytes,
                    "actual_size_bytes": size_bytes,
                },
            )

        if expected_hash is not None and digest.casefold() != expected_hash.casefold():
            raise StorageIntegrityError(
                "Stored content hash does not match expected_hash.",
                component="local_storage",
                operation="put",
                field="expected_hash",
                context={
                    "object_id": object_id,
                    "hash_algorithm": hash_algorithm,
                },
            )

        metadata = StoredObject(
            object_id=object_id,
            size_bytes=size_bytes,
            content_hash=digest,
            hash_algorithm=hash_algorithm,
            content_type=content_type,
        )

        with self._lock:
            self._objects[object_id] = (data, metadata)

        return metadata

    def _open(self, object_id: str) -> BinaryIO:
        with self._lock:
            stored = self._objects.get(object_id)

        if stored is None:
            raise StorageNotFoundError(
                "Stored object does not exist.",
                component="local_storage",
                operation="open",
                context={"object_id": object_id},
            )

        payload, _ = stored
        return BytesIO(payload)

    def _stat(self, object_id: str) -> StoredObject | None:
        with self._lock:
            stored = self._objects.get(object_id)
            return None if stored is None else stored[1]

    def _delete(self, object_id: str) -> bool:
        with self._lock:
            return self._objects.pop(object_id, None) is not None


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class InProcessQueue(Queue):
    """
    Idempotent process-local AuditJob submission adapter.

    This adapter acknowledges jobs and retains them for the lifetime of the
    process. It deliberately does not pretend to be a durable message broker.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing local audit queue", "info")

        self._lock = RLock()
        self._receipts: dict[str, QueueReceipt] = {}
        self._jobs: dict[str, AuditJob] = {}

        super().__init__()

    def _enqueue(
        self,
        job: AuditJob,
        *,
        idempotency_key: str,
    ) -> QueueReceipt:
        with self._lock:
            existing = self._receipts.get(idempotency_key)

            if existing is not None:
                if existing.job_id != job.job_id:
                    raise QueueIntegrityError(
                        "Queue idempotency key is already bound to another job.",
                        component="local_queue",
                        operation="enqueue",
                        field="idempotency_key",
                        context={
                            "existing_job_id": existing.job_id,
                            "received_job_id": job.job_id,
                        },
                    )
                return existing

            receipt = QueueReceipt(
                job_id=job.job_id,
                queue_reference=f"local:{job.job_id}",
                idempotency_key=idempotency_key,
            )

            self._jobs[job.job_id] = job
            self._receipts[idempotency_key] = receipt

            return receipt

    def snapshot(self) -> tuple[AuditJob, ...]:
        """Return a stable process-local snapshot for deployment diagnostics."""
        with self._lock:
            return tuple(self._jobs.values())


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------


class DisabledPayment(Payment):
    """
    Fail-closed payment adapter for deployments without a payment provider.

    It exists so the application graph can be composed without inventing a
    successful payment implementation or accepting unverifiable webhooks.
    """

    def __init__(self) -> None:
        printer.status("BIMAP", "Initializing disabled payment adapter", "info")
        super().__init__()

    def _create_checkout(
        self,
        order: Order,
        tier: ProductTier,
        *,
        idempotency_key: str,
    ) -> PaymentCheckout:
        del tier, idempotency_key

        raise PaymentUnavailableError(
            "No payment provider is configured for this BIMAP deployment.",
            component="disabled_payment",
            operation="create_checkout",
            context={"order_id": order.order_id},
        )

    def _verify_event(
        self,
        payload: bytes,
        *,
        signature: str,
    ) -> PaymentEvent:
        del payload, signature

        raise PaymentUnavailableError(
            "No payment provider is configured for this BIMAP deployment.",
            component="disabled_payment",
            operation="verify_event",
        )


# ---------------------------------------------------------------------------
# Malware
# ---------------------------------------------------------------------------


class DevelopmentMalware(Malware):
    """
    Explicit development-only malware boundary.

    Default behavior is fail-safe/indeterminate. ``trust_uploads=True`` exists
    only for controlled local integration work and must never be enabled in a
    production deployment.
    """

    def __init__(self, *, trust_uploads: bool = False) -> None:
        if not isinstance(trust_uploads, bool):
            raise TypeError("trust_uploads must be boolean")

        self._trust_uploads = trust_uploads

        printer.status(
            "BIMAP",
            (
                "Initializing development malware gate "
                f"(trust_uploads={trust_uploads})"
            ),
            "warning" if trust_uploads else "info",
        )

        super().__init__()

    @property
    def trust_uploads(self) -> bool:
        return self._trust_uploads

    def _scan_stream(
        self,
        stream: BinaryIO,
        *,
        object_id: str,
        filename: str | None,
        content_type: str | None,
        size_bytes: int | None,
    ) -> MalwareScanResult:
        del stream, filename, content_type, size_bytes

        return MalwareScanResult(
            object_id=object_id,
            verdict=(
                MalwareVerdict.CLEAN
                if self._trust_uploads
                else MalwareVerdict.INDETERMINATE
            ),
            scanned_at=datetime.now(timezone.utc),
            scanner_name="bimap-development-malware-gate",
            scanner_version="1",
        )


__all__ = [
    "SystemClock",
    "InMemoryRepository",
    "InMemoryStorage",
    "InProcessQueue",
    "DisabledPayment",
    "DevelopmentMalware",
]
