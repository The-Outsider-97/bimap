"""Read one artifact bound to a completed BIMAP audit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Any
from collections.abc import Mapping

from ..ports.audit_results import AuditResultStore
from ..ports.storage import Storage
from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Get Audit Artifect Query")
printer = PrettyPrinter()


_COMPONENT = "get_audit_artifact_query"
_ALLOWED_KINDS = frozenset(
    {
        "viewer_model",
        "family_data_pdf",
    }
)


@dataclass(frozen=True, slots=True)
class AuditArtifactDownload:
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    stream: BinaryIO


class GetAuditArtifact:
    __slots__ = ("_results",  "_storage")

    def __init__(self, results: AuditResultStore, storage: Storage) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing audit artifect",
            event="get_audit_artifect_projection_to_dict_start",
            #context={
            #    "filename": self.filename,
            #    "content_type": self.content_type,
            #    "sha256": self.sha256,},
        )
        if not isinstance(results, AuditResultStore):
            raise AppConfigurationError(
                "results must implement AuditResultStore.",
                component=_COMPONENT,
                operation="initialize",
                field="results",
            )

        if not isinstance(storage, Storage):
            raise AppConfigurationError(
                "storage must implement Storage.",
                component=_COMPONENT,
                operation="initialize",
                field="storage",
            )
        self._results = results
        self._storage = storage

    def execute(self, order_id: str, kind: str) -> AuditArtifactDownload:
        target_order = require_app_text(
            order_id,
            field="order_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="execute",
        )

        target_kind = require_app_text(
            kind,
            field="kind",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="execute",
            max_length=64,
        )

        if (
            target_kind
            not in _ALLOWED_KINDS
        ):
            raise AppValidationError(
                "Unsupported audit artifact kind.",
                component=_COMPONENT,
                operation="execute",
                field="kind",
                context={"kind": target_kind},
            )

        record = self._results.get_by_order(target_order)
        if record is None:
            raise AppValidationError(
                "Completed audit result does not exist.",
                component=_COMPONENT,
                operation="execute",
                field="order_id",
            )

        artifacts = record.payload.get("artifacts")

        if not isinstance(artifacts, Mapping):
            raise AppValidationError(
                "Audit contains no artifacts.",
                component=_COMPONENT,
                operation="execute",
                field="artifacts",
            )

        metadata = artifacts.get(target_kind)

        if not isinstance(metadata, Mapping):
            raise AppValidationError(
                "Requested audit artifact does not exist.",
                component=_COMPONENT,
                operation="execute",
                field="kind",
            )

        object_id = require_app_text(
            metadata.get("object_id"),
            field="object_id",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="execute",
        )

        filename = require_app_text(
            metadata.get("filename"),
            field="filename",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="execute",
        )

        content_type = require_app_text(
            metadata.get("content_type"),
            field="content_type",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="execute",
        )

        expected_size = (
            require_non_negative_int(
                metadata.get("size_bytes"),
                field="size_bytes",
                error_type=AppIntegrityError,
                component=_COMPONENT,
                operation="execute",
            )
        )

        expected_hash = require_app_text(
            metadata.get("sha256"),
            field="sha256",
            error_type=AppIntegrityError,
            component=_COMPONENT,
            operation="execute",
        ).casefold()

        stored = self._storage.stat(object_id)

        if stored is None:
            raise AppIntegrityError(
                "Bound audit artifact is missing from storage.",
                component=_COMPONENT,
                operation="execute",
                field="object_id",
            )

        if (
            stored.size_bytes
            != expected_size
            or stored.content_hash
            != expected_hash
        ):
            raise AppIntegrityError(
                "Audit artifact storage metadata failed integrity verification.",
                component=_COMPONENT,
                operation="execute",
                field="artifact",
            )

        return AuditArtifactDownload(
            kind=target_kind,
            filename=filename,
            content_type=content_type,
            size_bytes=expected_size,
            sha256=expected_hash,
            stream=self._storage.open(object_id),
        )


__all__ = ["AuditArtifactDownload", "GetAuditArtifact"]