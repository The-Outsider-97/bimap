"""Application service for authenticated, quota-governed model-data extraction."""

from __future__ import annotations

import hashlib
import re
import tempfile
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import BinaryIO, cast

from ..ports.accounts import *
from ..ports.artifact_mailer import *
from ..ports.clock import Clock
from ..ports.data_extraction import *
from ..ports.malware import *
from ..utils.app_errors import *
from ..utils.app_helpers import *
from .entitlement_service import *
from ...domain.accounts.plans import UsageKind
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Data Extraction Service")
printer = PrettyPrinter()

_COMPONENT = "data_extraction_service"
_COPY_CHUNK_BYTES = 1024 * 1024
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class DataExtractionEmailStatus(str, Enum):
    """Delivery state for an extraction package."""

    NOT_REQUESTED = "not_requested"
    ACCEPTED = "accepted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DataExtractionResult:
    """Completed extraction and its governed package metadata."""

    extraction_id: str
    source_hash: str
    source_size_bytes: int
    inspection: DataSourceInspection
    entitlement: EntitlementConsumption
    package: DataExtractionPackage
    email_status: DataExtractionEmailStatus = DataExtractionEmailStatus.NOT_REQUESTED

    def __post_init__(self) -> None:
        extraction_id = require_app_text(
            self.extraction_id,
            field="extraction_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_result",
            max_length=128,
        )
        source_hash = require_app_text(
            self.source_hash,
            field="source_hash",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_result",
            max_length=64,
        ).casefold()
        if len(source_hash) != 64 or any(
            character not in "0123456789abcdef" for character in source_hash
        ):
            raise AppValidationError(
                "source_hash must be a SHA-256 hexadecimal digest.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_hash",
            )
        source_size_bytes = require_non_negative_int(
            self.source_size_bytes,
            field="source_size_bytes",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_result",
        )
        if source_size_bytes == 0:
            raise AppValidationError(
                "source_size_bytes must be greater than zero.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_size_bytes",
            )
        if not isinstance(self.inspection, DataSourceInspection):
            raise AppIntegrityError(
                "inspection must be DataSourceInspection.",
                component=_COMPONENT,
                operation="validate_result",
                field="inspection",
                context={"received_type": type(self.inspection).__name__},
            )
        if not isinstance(self.entitlement, EntitlementConsumption):
            raise AppIntegrityError(
                "entitlement must be EntitlementConsumption.",
                component=_COMPONENT,
                operation="validate_result",
                field="entitlement",
                context={"received_type": type(self.entitlement).__name__},
            )
        if not isinstance(self.package, DataExtractionPackage):
            raise AppIntegrityError(
                "package must be DataExtractionPackage.",
                component=_COMPONENT,
                operation="validate_result",
                field="package",
                context={"received_type": type(self.package).__name__},
            )
        if not isinstance(self.email_status, DataExtractionEmailStatus):
            try:
                status = DataExtractionEmailStatus(str(self.email_status))
            except ValueError as exc:
                raise AppValidationError(
                    "email_status is invalid.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="email_status",
                    cause=exc,
                ) from exc
            object.__setattr__(self, "email_status", status)

        object.__setattr__(self, "extraction_id", extraction_id)
        object.__setattr__(self, "source_hash", source_hash)
        object.__setattr__(self, "source_size_bytes", source_size_bytes)

    def close(self) -> None:
        self.package.close()


class DataExtractionService:
    """Coordinate source admission, scanning, extraction, quota use, and packaging."""

    __slots__ = (
        "_extractor",
        "_pdf_renderer",
        "_malware",
        "_entitlement",
        "_clock",
        "_accounts",
        "_artifact_mailer",
        "_max_source_bytes",
    )

    def __init__(
        self,
        extractor: DataExtractor,
        pdf_renderer: DataExtractionPDFRenderer,
        malware: Malware,
        entitlement: EntitlementService,
        clock: Clock,
        *,
        accounts: Accounts,
        artifact_mailer: ArtifactMailer | None = None,
        max_source_bytes: int | None = None,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing data extraction service",
            event="data_extraction_service_init_start",
        )
        if not isinstance(extractor, DataExtractor):
            raise AppConfigurationError(
                "extractor must implement DataExtractor.",
                component=_COMPONENT,
                operation="initialize",
                field="extractor",
                context={"received_type": type(extractor).__name__},
            )
        if not isinstance(pdf_renderer, DataExtractionPDFRenderer):
            raise AppConfigurationError(
                "pdf_renderer must implement DataExtractionPDFRenderer.",
                component=_COMPONENT,
                operation="initialize",
                field="pdf_renderer",
                context={"received_type": type(pdf_renderer).__name__},
            )
        if not isinstance(malware, Malware):
            raise AppConfigurationError(
                "malware must implement Malware.",
                component=_COMPONENT,
                operation="initialize",
                field="malware",
                context={"received_type": type(malware).__name__},
            )
        if not isinstance(entitlement, EntitlementService):
            raise AppConfigurationError(
                "entitlement must be an EntitlementService.",
                component=_COMPONENT,
                operation="initialize",
                field="entitlement",
                context={"received_type": type(entitlement).__name__},
            )
        if not isinstance(clock, Clock):
            raise AppConfigurationError(
                "clock must implement Clock.",
                component=_COMPONENT,
                operation="initialize",
                field="clock",
                context={"received_type": type(clock).__name__},
            )
        if not isinstance(accounts, Accounts):
            raise AppConfigurationError(
                "accounts must implement the BIMAP Accounts port.",
                component=_COMPONENT,
                operation="initialize",
                field="accounts",
                context={
                    "received_type": type(accounts).__name__,
                },
            )

        self._accounts = accounts
        if max_source_bytes is not None and (
            isinstance(max_source_bytes, bool)
            or not isinstance(max_source_bytes, int)
            or max_source_bytes <= 0
        ):
            raise AppConfigurationError(
                "max_source_bytes must be a positive integer or None.",
                component=_COMPONENT,
                operation="initialize",
                field="max_source_bytes",
            )

        self._extractor = extractor
        self._pdf_renderer = pdf_renderer
        self._malware = malware
        self._entitlement = entitlement
        self._clock = clock
        self._max_source_bytes = max_source_bytes

        logger.info(
            {
                "event": "data_extraction_service_initialized",
                "capability_count": len(extractor.capabilities),
                "max_source_bytes": max_source_bytes,
                "email_available": False,
            }
        )

    @property
    def capabilities(self) -> tuple[DataExtractionCapability, ...]:
        return self._extractor.capabilities

    @property
    def email_available(self) -> bool:
        # The current Notifications port carries logical notification metadata,
        # not arbitrary binary attachments.  Do not advertise package e-mail
        # until an attachment-capable delivery port is explicitly introduced.
        return False

    def _source_format_from_filename(self, filename: str) -> ExtractionSourceFormat:
        suffix = PurePath(filename).suffix.casefold()
        matches = {
            ExtractionSourceFormat.parse(capability.source_format)
            for capability in self.capabilities
            if suffix in capability.extensions
        }
        if not matches:
            raise UnsupportedAppInputError(
                "The selected source model format is not supported by the configured extraction adapters.",
                component=_COMPONENT,
                operation="resolve_source_format",
                field="source",
                context={
                    "extension": suffix or None,
                    "supported_extensions": tuple(
                        sorted(
                            {
                                extension
                                for capability in self.capabilities
                                for extension in capability.extensions
                            }
                        )
                    ),
                },
            )
        if len(matches) != 1:
            raise AppIntegrityError(
                "Multiple extraction capabilities claim the same source extension.",
                component=_COMPONENT,
                operation="resolve_source_format",
                field="source",
                context={
                    "extension": suffix,
                    "formats": tuple(sorted(item.value for item in matches)),
                },
            )
        return next(iter(matches))

    @staticmethod
    def _output_stem(filename: str) -> str:
        raw = PurePath(filename).stem.strip()
        stem = _SAFE_STEM.sub("-", raw).strip("._-")
        return (stem or "model")[:120]

    def _stage(self, source: BinaryIO) -> tuple[BinaryIO, int, str]:
        stream = require_binary_stream(
            source,
            field="source",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="stage_source",
        )
        staged = tempfile.TemporaryFile(mode="w+b")
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            try:
                stream.seek(0)
            except (AttributeError, OSError) as exc:
                raise UnsupportedAppInputError(
                    "Source model stream must be seekable.",
                    component=_COMPONENT,
                    operation="stage_source",
                    field="source",
                    cause=exc,
                ) from exc

            while True:
                chunk = stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise UnsupportedAppInputError(
                        "Source stream yielded non-binary data.",
                        component=_COMPONENT,
                        operation="stage_source",
                        field="source",
                    )
                payload = bytes(chunk)
                size_bytes += len(payload)
                if self._max_source_bytes is not None and size_bytes > self._max_source_bytes:
                    raise AppValidationError(
                        "Source model exceeds the configured data-extraction size limit.",
                        component=_COMPONENT,
                        operation="stage_source",
                        field="source_size",
                        context={"max_source_bytes": self._max_source_bytes},
                    )
                staged.write(payload)
                digest.update(payload)

            if size_bytes == 0:
                raise AppValidationError(
                    "Source model cannot be empty.",
                    component=_COMPONENT,
                    operation="stage_source",
                    field="source",
                )
            staged.seek(0)
            return cast(BinaryIO, staged), size_bytes, digest.hexdigest()
        except Exception:
            staged.close()
            raise

    @staticmethod
    def _rewind(stream: BinaryIO, *, operation: str) -> None:
        try:
            stream.seek(0)
        except (AttributeError, OSError) as exc:
            raise AppIntegrityError(
                "Staged extraction stream cannot be rewound.",
                component=_COMPONENT,
                operation=operation,
                field="source",
                cause=exc,
            ) from exc

    def _scan(
        self,
        stream: BinaryIO,
        *,
        extraction_id: str,
        source_hash: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
    ) -> MalwareScanResult:
        result = self._malware.scan(
            stream,
            object_id=f"data-extraction:{extraction_id}:{source_hash[:16]}",
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
        )
        if not result.is_clean:
            raise AppValidationError(
                "Model source did not receive a definitive clean malware verdict.",
                component=_COMPONENT,
                operation="scan_source",
                field="malware",
                context={"verdict": str(result.verdict)},
            )
        return result

    def _select_datasets(
        self,
        source_format: ExtractionSourceFormat,
        values: tuple[ExtractionDataset | str, ...],
    ) -> tuple[ExtractionDataset, ...]:
        selected = normalize_datasets(values)
        capability = next(
            (
                item
                for item in self.capabilities
                if ExtractionSourceFormat.parse(item.source_format) is source_format
            ),
            None,
        )
        if capability is None:
            raise AppIntegrityError(
                "Resolved extraction source has no advertised capability.",
                component=_COMPONENT,
                operation="select_datasets",
                field="source_format",
                context={"source_format": source_format.value},
            )
        unsupported = tuple(
            item.value for item in selected if item not in capability.datasets
        )
        if unsupported:
            raise UnsupportedAppInputError(
                "One or more selected datasets are not supported for this source format.",
                component=_COMPONENT,
                operation="select_datasets",
                field="datasets",
                context={
                    "source_format": source_format.value,
                    "unsupported": unsupported,
                    "supported": tuple(
                        item.value if isinstance(item, ExtractionDataset) else item
                        for item in capability.datasets
                    ),
                },
            )
        return selected

    @staticmethod
    def _zip_info(filename: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(filename=filename, date_time=_ZIP_TIMESTAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o600 << 16
        return info

    def _build_package(self, *, output_stem: str, document: dict[str, object]) -> DataExtractionPackage:
        json_filename = f"{output_stem}-extraction.json"
        pdf_filename = f"{output_stem}-extraction.pdf"
        package_filename = f"{output_stem}-extraction.zip"

        json_bytes = canonical_app_json(document, pretty=True).encode("utf-8")
        if not json_bytes:
            raise AppIntegrityError(
                "Canonical extraction JSON is empty.",
                component=_COMPONENT,
                operation="build_package",
                field="json",
            )

        try:
            pdf_bytes = self._pdf_renderer.render(document=document)
        except AppError:
            raise
        except Exception as exc:
            raise AppIntegrityError(
                "Data extraction PDF renderer failed outside the application-error contract.",
                component=_COMPONENT,
                operation="build_package",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc
        if not isinstance(pdf_bytes, (bytes, bytearray, memoryview)) or not pdf_bytes:
            raise AppIntegrityError(
                "Data extraction PDF renderer returned an empty or non-binary artifact.",
                component=_COMPONENT,
                operation="build_package",
                field="pdf",
            )
        pdf_payload = bytes(pdf_bytes)

        package_stream = tempfile.TemporaryFile(mode="w+b")
        try:
            with zipfile.ZipFile(
                package_stream,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as archive:
                archive.writestr(self._zip_info(json_filename), json_bytes)
                archive.writestr(self._zip_info(pdf_filename), pdf_payload)

            package_stream.seek(0)
            digest = hashlib.sha256()
            size_bytes = 0
            while True:
                chunk = package_stream.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                size_bytes += len(chunk)

            if size_bytes == 0:
                raise AppIntegrityError(
                    "Data extraction package is empty.",
                    component=_COMPONENT,
                    operation="build_package",
                    field="package",
                )

            package_stream.seek(0)
            return DataExtractionPackage(
                stream=cast(BinaryIO, package_stream),
                filename=package_filename,
                content_type="application/zip",
                size_bytes=size_bytes,
                content_hash=digest.hexdigest(),
                json_filename=json_filename,
                pdf_filename=pdf_filename,
                json_size_bytes=len(json_bytes),
                pdf_size_bytes=len(pdf_payload),
            )
        except Exception:
            package_stream.close()
            raise

    def extract(
        self,
        *,
        account_id: str,
        extraction_id: str,
        idempotency_key: str,
        source: BinaryIO,
        filename: str,
        content_type: str | None,
        datasets: tuple[ExtractionDataset | str, ...],
        email_result: bool,
    ) -> DataExtractionResult:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing governed model-data extraction",
            event="data_extraction_service_extract_start",
            context={"extraction_id": extraction_id},
        )
        normalized_account_id = require_app_text(
            account_id,
            field="account_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="extract",
            max_length=512,
        )
        account = self._accounts.get_account(
            normalized_account_id
        )

        if account is None:
            raise AppValidationError(
                "Data-extraction account does not exist.",
                component=_COMPONENT,
                operation="extract",
                field="account_id",
                context={
                    "account_id": normalized_account_id,
                },
            )

        requester_name = (
            f"{account.name} {account.surname}"
        ).strip()

        recipient_email = account.email
        normalized_extraction_id = require_app_text(
            extraction_id,
            field="extraction_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="extract",
            max_length=128,
        )
        normalized_key = require_app_text(
            idempotency_key,
            field="idempotency_key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="extract",
            max_length=512,
        )
        normalized_filename = require_app_text(
            filename,
            field="filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="extract",
            max_length=255,
        )
        if not isinstance(email_result, bool):
            raise UnsupportedAppInputError(
                "email_result must be boolean.",
                component=_COMPONENT,
                operation="extract",
                field="email_result",
            )
        if email_result:
            raise AppConfigurationError(
                "Extraction-package e-mail delivery is not configured by the current binary-delivery contracts.",
                component=_COMPONENT,
                operation="extract",
                field="email_result",
            )

        normalized_content_type = None
        if content_type is not None and content_type.strip():
            normalized_content_type = require_app_text(
                content_type,
                field="content_type",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="extract",
                max_length=128,
            )

        source_format = self._source_format_from_filename(normalized_filename)
        selected = self._select_datasets(source_format, datasets)

        staged, source_size, source_hash = self._stage(source)
        try:
            self._scan(
                staged,
                extraction_id=normalized_extraction_id,
                source_hash=source_hash,
                filename=normalized_filename,
                content_type=normalized_content_type,
                size_bytes=source_size,
            )

            self._rewind(staged, operation="inspect_source")
            inspection = self._extractor.inspect(
                staged,
                source_format=source_format,
            )
            if inspection.source_format is not source_format:
                raise AppIntegrityError(
                    "Extractor inspection returned a mismatched source format.",
                    component=_COMPONENT,
                    operation="inspect_source",
                    field="inspection.source_format",
                    context={
                        "expected": source_format.value,
                        "received": getattr(
                            inspection.source_format,
                            "value",
                            inspection.source_format,
                        ),
                    },
                )

            binding_material = (
                f"{normalized_extraction_id}\0{source_hash}\0"
                + ",".join(sorted(item.value for item in selected))
            ).encode("utf-8")
            entitlement = self._entitlement.consume(
                account_id=normalized_account_id,
                kind=UsageKind.DATA_EXTRACTION,
                source_id=(
                    "data-extraction:"
                    + hashlib.sha256(binding_material).hexdigest()
                ),
                idempotency_key=normalized_key,
            )

            self._rewind(staged, operation="extract_source")
            extracted = self._extractor.extract(
                staged,
                source_format=source_format,
                datasets=selected,
            )
        finally:
            staged.close()

        if not isinstance(extracted, ExtractedModelData):
            raise AppIntegrityError(
                "Extractor returned an unsupported result type.",
                component=_COMPONENT,
                operation="extract_source",
                field="result",
                context={"received_type": type(extracted).__name__},
            )
        if extracted.inspection.source_format is not source_format:
            raise AppIntegrityError(
                "Extracted data reports a mismatched source format.",
                component=_COMPONENT,
                operation="extract_source",
                field="result.inspection.source_format",
            )

        generated_at = format_app_utc_datetime(
            self._clock.now(),
            field="generated_at",
            component=_COMPONENT,
            operation="build_document",
        )
        document: dict[str, object] = {
            "extraction": {
                "extraction_id": normalized_extraction_id,
                "generated_at": generated_at,
                "datasets": tuple(
                    item.value
                    for item in selected
                ),
                "requested_by": {
                    "account_id": normalized_account_id,
                    "display_name": requester_name,
                },
            },
            "source": {
                # existing source fields
            },
            "model": extracted.to_dict(),
        }
        package = self._build_package(output_stem=self._output_stem(normalized_filename), document=document )

        logger.info(
            {
                "event": "data_extraction_service_extract_completed",
                "account_id": normalized_account_id,
                "extraction_id": normalized_extraction_id,
                "source_format": source_format.value,
                "source_schema": inspection.schema,
                "entity_count": inspection.product_count,
                "selected_datasets": tuple(item.value for item in selected),
                "package_size_bytes": package.size_bytes,
            }
        )

        return DataExtractionResult(
            extraction_id=normalized_extraction_id,
            source_hash=source_hash,
            source_size_bytes=source_size,
            inspection=inspection,
            entitlement=entitlement,
            package=package,
            email_status=DataExtractionEmailStatus.NOT_REQUESTED,
        )


__all__ = [
    "DataExtractionEmailStatus",
    "DataExtractionResult",
    "DataExtractionService",
]
