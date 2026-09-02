"""
Stream-oriented object-storage application port for BIMAP.

The storage port abstracts staged uploads, source objects, and generated report
artifacts without exposing a filesystem path, cloud bucket, provider SDK,
presigned URL, database blob implementation, or concrete retention mechanism.

Integrity and boundary policy
-----------------------------
* ``object_id`` is a BIMAP/application logical identity. Concrete adapters map it
  to provider-specific keys internally.
* All writes are stream-based; large BIM/Revit uploads are not duplicated into
  memory by this port.
* Every successful write returns size and cryptographic content-hash metadata.
  Optional expected size/hash values are checked and mismatches fail closed.
* ``stat()`` returns ``None`` for absence. ``open()`` requires the object and
  raises ``StorageNotFoundError`` when it does not exist.
* ``delete()`` returns ``True`` when deletion of an existing object is confirmed
  and ``False`` when absence is confirmed. Uncertain backend outcomes are errors.
* No upload allowlist, malware policy, retention duration, archive limit,
  signed-URL lifetime, MIME policy, or retry loop is invented here.
* Evidence/report convenience methods reuse existing contract integrity metadata
  rather than creating a parallel storage manifest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO

from ..utils.app_errors import *
from ..utils.app_helpers import *
from ...contracts.evidence import EvidenceContract
from ...contracts.report_manifest import ReportManifest
from ...domain.utils.domain_errors import DomainError
from ...domain.utils.domain_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Storage Port")
printer = PrettyPrinter()

_COMPONENT = "storage"


def _normalize_storage_hash(
    value: object,
    *,
    algorithm: object,
    field: str,
    operation: str,
) -> tuple[str, str]:
    """Delegate cryptographic digest validation to canonical domain helpers."""
    try:
        normalized_algorithm = normalize_hash_algorithm(algorithm)
        normalized_digest = normalize_hex_digest(
            value,
            algorithm=normalized_algorithm,
            field=field,
        )
    except (DomainError, TypeError, ValueError) as exc:
        raise StorageValidationError(
            "Storage content hash metadata is invalid.",
            component=_COMPONENT,
            operation=operation,
            field=field,
            context={"algorithm": str(algorithm)},
            cause=exc,
        ) from exc
    return normalized_algorithm, normalized_digest


def _normalize_storage_algorithm(value: object, *, operation: str) -> str:
    """Validate one requested cryptographic hash algorithm."""
    try:
        return normalize_hash_algorithm(value)
    except (DomainError, TypeError, ValueError) as exc:
        raise StorageValidationError(
            "Storage hash algorithm is invalid.",
            component=_COMPONENT,
            operation=operation,
            field="hash_algorithm",
            context={"received_type": type(value).__name__},
            cause=exc,
        ) from exc


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Immutable BIMAP-facing metadata for one stored binary object."""

    object_id: str
    size_bytes: int
    content_hash: str
    hash_algorithm: str = "sha256"
    content_type: str | None = None

    def __post_init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating stored-object metadata",
            event="storage_object_validate_start",
            context={"object_id": self.object_id, "size_bytes": self.size_bytes},
        )
        object_id = require_app_text(
            self.object_id,
            field="object_id",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="validate_object",
        )
        size_bytes = require_non_negative_int(
            self.size_bytes,
            field="size_bytes",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="validate_object",
        )
        algorithm, digest = _normalize_storage_hash(
            self.content_hash,
            algorithm=self.hash_algorithm,
            field="content_hash",
            operation="validate_object",
        )
        content_type = optional_app_text(
            self.content_type,
            field="content_type",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="validate_object",
            max_length=256,
        )

        object.__setattr__(self, "object_id", object_id)
        object.__setattr__(self, "size_bytes", size_bytes)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "hash_algorithm", algorithm)
        object.__setattr__(self, "content_type", content_type)

    def to_dict(self) -> dict[str, object]:
        """Return deterministic provider-independent storage metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing stored-object metadata",
            event="storage_object_to_dict_start",
            context={"object_id": self.object_id},
        )
        return {
            "object_id": self.object_id,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "hash_algorithm": self.hash_algorithm,
            "content_type": self.content_type,
        }


class Storage(ABC):
    """Abstract stream-oriented object-storage dependency."""

    def __init__(self) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing storage port",
            event="storage_init_start",
        )
        logger.debug(
            {
                "event": "storage_port_initialized",
                "implementation": type(self).__name__,
            }
        )

    @abstractmethod
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
        """Store one stream and return computed storage metadata."""
        raise NotImplementedError

    @abstractmethod
    def _open(self, object_id: str) -> BinaryIO:
        """Open one existing object for binary reading."""
        raise NotImplementedError

    @abstractmethod
    def _stat(self, object_id: str) -> StoredObject | None:
        """Return object metadata, or ``None`` when absent."""
        raise NotImplementedError

    @abstractmethod
    def _delete(self, object_id: str) -> bool:
        """Delete one object with confirmed-existence semantics."""
        raise NotImplementedError

    def put(
        self,
        stream: BinaryIO,
        *,
        object_id: str,
        content_type: str | None = None,
        hash_algorithm: str = "sha256",
        expected_size_bytes: int | None = None,
        expected_hash: str | None = None,
    ) -> StoredObject:
        """Store one binary stream and validate returned integrity metadata."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Storing binary object",
            event="storage_put_start",
            context={
                "object_id": object_id,
                "expected_size_bytes": expected_size_bytes,
                "has_expected_hash": expected_hash is not None,
                "has_content_type": content_type is not None,
            },
        )
        target_stream = require_binary_stream(
            stream,
            field="stream",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="put",
        )
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="put",
        )
        normalized_content_type = optional_app_text(
            content_type,
            field="content_type",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="put",
            max_length=256,
        )
        normalized_algorithm = _normalize_storage_algorithm(
            hash_algorithm,
            operation="put",
        )
        normalized_size = (
            None
            if expected_size_bytes is None
            else require_non_negative_int(
                expected_size_bytes,
                field="expected_size_bytes",
                error_type=StorageValidationError,
                component=_COMPONENT,
                operation="put",
            )
        )
        normalized_expected_hash: str | None = None
        if expected_hash is not None:
            _, normalized_expected_hash = _normalize_storage_hash(
                expected_hash,
                algorithm=normalized_algorithm,
                field="expected_hash",
                operation="put",
            )

        try:
            result = self._put(
                target_stream,
                object_id=target_id,
                content_type=normalized_content_type,
                hash_algorithm=normalized_algorithm,
                expected_size_bytes=normalized_size,
                expected_hash=normalized_expected_hash,
            )
        except StorageError:
            raise
        except TimeoutError as exc:
            raise StorageTimeoutError(
                "Object-storage write timed out.",
                component=_COMPONENT,
                operation="put",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise StorageUnavailableError(
                "Object-storage backend is unavailable.",
                component=_COMPONENT,
                operation="put",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageOperationError(
                "Storage adapter failed while writing an object.",
                component=_COMPONENT,
                operation="put",
                context={
                    "object_id": target_id,
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(result, StoredObject):
            raise StorageValidationError(
                "Storage adapter returned an unsupported write result.",
                component=_COMPONENT,
                operation="put",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.object_id != target_id:
            raise StorageIntegrityError(
                "Stored-object metadata belongs to a different object.",
                component=_COMPONENT,
                operation="put",
                field="result.object_id",
                context={
                    "requested_object_id": target_id,
                    "returned_object_id": result.object_id,
                },
            )
        if result.hash_algorithm != normalized_algorithm:
            raise StorageIntegrityError(
                "Storage adapter did not use the requested hash algorithm.",
                component=_COMPONENT,
                operation="put",
                field="result.hash_algorithm",
                context={
                    "requested_algorithm": normalized_algorithm,
                    "returned_algorithm": result.hash_algorithm,
                },
            )
        if normalized_size is not None and result.size_bytes != normalized_size:
            raise StorageIntegrityError(
                "Stored object size does not match the expected size.",
                component=_COMPONENT,
                operation="put",
                field="result.size_bytes",
                context={
                    "object_id": target_id,
                    "expected_size_bytes": normalized_size,
                    "actual_size_bytes": result.size_bytes,
                },
            )
        if (
            normalized_expected_hash is not None
            and result.content_hash != normalized_expected_hash
        ):
            raise StorageIntegrityError(
                "Stored object hash does not match the expected content hash.",
                component=_COMPONENT,
                operation="put",
                field="result.content_hash",
                context={
                    "object_id": target_id,
                    "hash_algorithm": normalized_algorithm,
                },
            )

        logger.info(
            {
                "event": "storage_object_written",
                "object_id": target_id,
                "size_bytes": result.size_bytes,
                "hash_algorithm": result.hash_algorithm,
            }
        )
        return result

    def open(self, object_id: str) -> BinaryIO:
        """
        Open one object as a readable binary stream.

        The caller owns closing the returned stream. This method validates its
        shape but never reads or rewinds it.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Opening stored binary object",
            event="storage_open_start",
            context={"object_id": object_id},
        )
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="open",
        )
        try:
            stream = self._open(target_id)
        except StorageError:
            raise
        except FileNotFoundError as exc:
            raise StorageNotFoundError(
                "Stored object does not exist.",
                component=_COMPONENT,
                operation="open",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except TimeoutError as exc:
            raise StorageTimeoutError(
                "Object-storage read timed out.",
                component=_COMPONENT,
                operation="open",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise StorageUnavailableError(
                "Object-storage backend is unavailable.",
                component=_COMPONENT,
                operation="open",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageOperationError(
                "Storage adapter failed while opening an object.",
                component=_COMPONENT,
                operation="open",
                context={
                    "object_id": target_id,
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        return require_binary_stream(
            stream,
            field="result",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="open",
        )

    def stat(self, object_id: str) -> StoredObject | None:
        """Return object metadata, or ``None`` when the object is absent."""
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Reading stored-object metadata",
            event="storage_stat_start",
            context={"object_id": object_id},
        )
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="stat",
        )
        try:
            result = self._stat(target_id)
        except StorageError:
            raise
        except TimeoutError as exc:
            raise StorageTimeoutError(
                "Object-storage metadata lookup timed out.",
                component=_COMPONENT,
                operation="stat",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise StorageUnavailableError(
                "Object-storage backend is unavailable.",
                component=_COMPONENT,
                operation="stat",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageOperationError(
                "Storage adapter failed while reading object metadata.",
                component=_COMPONENT,
                operation="stat",
                context={
                    "object_id": target_id,
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if result is None:
            return None
        if not isinstance(result, StoredObject):
            raise StorageValidationError(
                "Storage adapter returned unsupported metadata.",
                component=_COMPONENT,
                operation="stat",
                field="result",
                context={"received_type": type(result).__name__},
            )
        if result.object_id != target_id:
            raise StorageIntegrityError(
                "Storage metadata belongs to a different object.",
                component=_COMPONENT,
                operation="stat",
                field="result.object_id",
                context={
                    "requested_object_id": target_id,
                    "returned_object_id": result.object_id,
                },
            )
        return result

    def delete(self, object_id: str) -> bool:
        """
        Delete one object.

        ``False`` means the adapter definitively established that the object was
        already absent. Backend uncertainty is an operational failure.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Deleting stored object",
            event="storage_delete_start",
            context={"object_id": object_id},
        )
        target_id = require_app_text(
            object_id,
            field="object_id",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="delete",
        )
        try:
            result = self._delete(target_id)
        except StorageError:
            raise
        except TimeoutError as exc:
            raise StorageTimeoutError(
                "Object-storage deletion timed out.",
                component=_COMPONENT,
                operation="delete",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except ConnectionError as exc:
            raise StorageUnavailableError(
                "Object-storage backend is unavailable.",
                component=_COMPONENT,
                operation="delete",
                context={"object_id": target_id},
                cause=exc,
            ) from exc
        except Exception as exc:
            raise StorageOperationError(
                "Storage adapter failed while deleting an object.",
                component=_COMPONENT,
                operation="delete",
                context={
                    "object_id": target_id,
                    "implementation": type(self).__name__,
                    "error_type": type(exc).__name__,
                },
                cause=exc,
            ) from exc

        if not isinstance(result, bool):
            raise StorageValidationError(
                "Storage adapter delete result must be boolean.",
                component=_COMPONENT,
                operation="delete",
                field="result",
                context={"received_type": type(result).__name__},
            )
        logger.info(
            {
                "event": "storage_object_delete_completed",
                "object_id": target_id,
                "existed": result,
            }
        )
        return result

    def put_evidence_source(
        self,
        stream: BinaryIO,
        evidence: EvidenceContract,
        *,
        size_bytes: int | None = None,
        content_type: str | None = None,
    ) -> StoredObject:
        """
        Store a source object using authoritative evidence source identity/hash.

        ``source_file_id`` is already the authoritative opaque source identity in
        the EvidenceContract, so it becomes the logical storage object id. The
        concrete backend remains free to map it to any provider-specific key.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Storing evidence source object",
            event="storage_put_evidence_source_start",
            context={"evidence_id": getattr(evidence, "evidence_id", None)},
        )
        if not isinstance(evidence, EvidenceContract):
            raise StorageValidationError(
                "put_evidence_source() requires an EvidenceContract.",
                component=_COMPONENT,
                operation="put_evidence_source",
                field="evidence",
                context={"received_type": type(evidence).__name__},
            )
        return self.put(
            stream,
            object_id=evidence.source_file_id,
            content_type=content_type,
            hash_algorithm=evidence.hash_algorithm,
            expected_size_bytes=size_bytes,
            expected_hash=evidence.source_hash,
        )

    def put_report_artifact(
        self,
        stream: BinaryIO,
        manifest: ReportManifest,
        *,
        artifact_id: str,
        object_id: str,
        content_type: str | None = None,
    ) -> StoredObject:
        """
        Store a generated artifact against existing manifest integrity metadata.

        ``object_id`` remains explicit because ReportManifest guarantees artifact
        identity uniqueness inside a manifest, not a repository-wide storage-key
        naming convention.
        """
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Storing report artifact",
            event="storage_put_report_artifact_start",
            context={
                "report_id": getattr(manifest, "report_id", None),
                "artifact_id": artifact_id,
                "object_id": object_id,
            },
        )
        if not isinstance(manifest, ReportManifest):
            raise StorageValidationError(
                "put_report_artifact() requires a ReportManifest.",
                component=_COMPONENT,
                operation="put_report_artifact",
                field="manifest",
                context={"received_type": type(manifest).__name__},
            )
        normalized_artifact_id = require_app_text(
            artifact_id,
            field="artifact_id",
            error_type=StorageValidationError,
            component=_COMPONENT,
            operation="put_report_artifact",
        )
        artifact = manifest.artifact(normalized_artifact_id)
        if artifact is None:
            raise StorageValidationError(
                "Report manifest does not contain the requested artifact.",
                component=_COMPONENT,
                operation="put_report_artifact",
                field="artifact_id",
                context={
                    "report_id": manifest.report_id,
                    "artifact_id": normalized_artifact_id,
                },
            )
        return self.put(
            stream,
            object_id=object_id,
            content_type=content_type,
            hash_algorithm="sha256",
            expected_size_bytes=artifact.size_bytes,
            expected_hash=artifact.sha256,
        )


__all__ = [
    "StoredObject",
    "Storage",
]