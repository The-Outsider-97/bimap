"""Application service for authenticated, quota-governed model conversion."""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import BinaryIO, cast

from ..ports.malware import Malware, MalwareScanResult
from ..ports.model_conversion import (
    ConvertedModelArtifact,
    ModelConversionCapability,
    ModelConverter,
    ModelSourceFormat,
    ModelSourceInspection,
    ModelTargetFormat,
)
from ..utils.app_errors import *
from ..utils.app_helpers import *
from .entitlement_service import *
from ...domain.accounts.plans import UsageKind
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Model Conversion Service")
printer = PrettyPrinter()

_COMPONENT = "model_conversion_service"
_COPY_CHUNK_BYTES = 1024 * 1024
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class ModelConversionResult:
    conversion_id: str
    source_hash: str
    source_size_bytes: int
    inspection: ModelSourceInspection
    entitlement: EntitlementConsumption
    artifact: ConvertedModelArtifact

    def __post_init__(self) -> None:
        conversion_id = require_app_text(
            self.conversion_id,
            field="conversion_id",
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
        if (
            len(source_hash) != 64
            or any(character not in "0123456789abcdef" for character in source_hash)
        ):
            raise AppValidationError(
                "source_hash must be a SHA-256 hexadecimal digest.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_hash",
            )
        source_size = require_non_negative_int(
            self.source_size_bytes,
            field="source_size_bytes",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_result",
        )
        if source_size == 0:
            raise AppValidationError(
                "source_size_bytes must be greater than zero.",
                component=_COMPONENT,
                operation="validate_result",
                field="source_size_bytes",
            )
        if not isinstance(self.inspection, ModelSourceInspection):
            raise AppIntegrityError(
                "inspection must be ModelSourceInspection.",
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
        if not isinstance(self.artifact, ConvertedModelArtifact):
            raise AppIntegrityError(
                "artifact must be ConvertedModelArtifact.",
                component=_COMPONENT,
                operation="validate_result",
                field="artifact",
                context={"received_type": type(self.artifact).__name__},
            )
        object.__setattr__(self, "conversion_id", conversion_id)
        object.__setattr__(self, "source_hash", source_hash)
        object.__setattr__(self, "source_size_bytes", source_size)

    def close(self) -> None:
        self.artifact.close()


class ModelConversionService:
    """Coordinate source admission, safety scanning, quota use, and conversion."""

    __slots__ = (
        "_converter",
        "_malware",
        "_entitlement",
        "_max_source_bytes",
    )

    def __init__(
        self,
        converter: ModelConverter,
        malware: Malware,
        entitlement: EntitlementService,
        *,
        max_source_bytes: int | None = None,
    ) -> None:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing model conversion service",
            event="model_conversion_service_init_start",
        )
        if not isinstance(converter, ModelConverter):
            raise AppConfigurationError(
                "converter must implement ModelConverter.",
                component=_COMPONENT,
                operation="initialize",
                field="converter",
                context={"received_type": type(converter).__name__},
            )
        if not isinstance(malware, Malware):
            raise AppConfigurationError(
                "malware must implement the BIMAP Malware port.",
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
        if max_source_bytes is not None:
            if (
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
        self._converter = converter
        self._malware = malware
        self._entitlement = entitlement
        self._max_source_bytes = max_source_bytes
        logger.info(
            {
                "event": "model_conversion_service_initialized",
                "max_source_bytes": max_source_bytes,
                "capability_count": len(converter.capabilities),
            }
        )

    @property
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        return self._converter.capabilities

    def _source_format_from_filename(self, filename: str) -> ModelSourceFormat:
        suffix = PurePath(filename).suffix.casefold()

        matches: set[ModelSourceFormat] = {
            cast(ModelSourceFormat, capability.source_format)
            for capability in self.capabilities
            if suffix in capability.extensions
        }

        if not matches:
            raise UnsupportedAppInputError(
                "The selected source model format is not supported "
                "by the configured conversion adapters.",
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
                "Multiple conversion capabilities claim the same source extension.",
                component=_COMPONENT,
                operation="resolve_source_format",
                field="source",
                context={
                    "extension": suffix,
                    "formats": tuple(
                        sorted(
                            item.value
                            for item in matches
                        )
                    ),
                },
            )

        return next(iter(matches))

    @staticmethod
    def _output_stem(filename: str) -> str:
        raw = PurePath(filename).stem.strip()
        stem = _SAFE_STEM.sub("-", raw).strip("._-")
        return (stem or "converted-model")[:120]

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
                data = bytes(chunk)
                size_bytes += len(data)
                if self._max_source_bytes is not None and size_bytes > self._max_source_bytes:
                    raise AppValidationError(
                        "Source model exceeds the configured conversion size limit.",
                        component=_COMPONENT,
                        operation="stage_source",
                        field="source_size",
                        context={"max_source_bytes": self._max_source_bytes},
                    )
                staged.write(data)
                digest.update(data)

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
                "Staged conversion stream cannot be rewound.",
                component=_COMPONENT,
                operation=operation,
                field="source",
                cause=exc,
            ) from exc

    def _scan(
        self,
        stream: BinaryIO,
        *,
        conversion_id: str,
        source_hash: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
    ) -> MalwareScanResult:
        object_id = f"conversion:{conversion_id}:{source_hash[:16]}"
        result = self._malware.scan(
            stream,
            object_id=object_id,
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

    def convert(
        self,
        *,
        account_id: str,
        conversion_id: str,
        idempotency_key: str,
        source: BinaryIO,
        filename: str,
        content_type: str | None,
        target_format: ModelTargetFormat | str,
    ) -> ModelConversionResult:
        announce_app_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Executing governed model conversion",
            event="model_conversion_service_convert_start",
            context={"conversion_id": conversion_id, "target_format": str(target_format)},
        )
        normalized_account_id = require_app_text(
            account_id,
            field="account_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="convert",
            max_length=512,
        )
        normalized_conversion_id = require_app_text(
            conversion_id,
            field="conversion_id",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="convert",
            max_length=128,
        )
        normalized_key = require_app_text(
            idempotency_key,
            field="idempotency_key",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="convert",
            max_length=512,
        )
        normalized_filename = require_app_text(
            filename,
            field="filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="convert",
            max_length=255,
        )
        normalized_content_type = None
        if content_type is not None and content_type.strip():
            normalized_content_type = require_app_text(
                content_type,
                field="content_type",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="convert",
                max_length=128,
            )

        source_format = self._source_format_from_filename(normalized_filename)
        target = ModelTargetFormat.parse(target_format)
        capability = next(
            (item for item in self.capabilities if item.source_format is source_format),
            None,
        )
        if capability is None or target not in capability.target_formats:
            raise UnsupportedAppInputError(
                "Requested source/target conversion pair is unsupported.",
                component=_COMPONENT,
                operation="convert",
                field="target_format",
            )

        staged, source_size, source_hash = self._stage(source)
        try:
            self._scan(
                staged,
                conversion_id=normalized_conversion_id,
                source_hash=source_hash,
                filename=normalized_filename,
                content_type=normalized_content_type,
                size_bytes=source_size,
            )
            self._rewind(staged, operation="inspect_source")
            inspection = self._converter.inspect(staged, source_format=source_format)

            binding_material = (
                f"{normalized_conversion_id}\0{source_hash}\0{target.value}"
            ).encode("utf-8")
            entitlement_source_id = (
                "conversion:"
                + hashlib.sha256(binding_material).hexdigest()
            )
            entitlement = self._entitlement.consume(
                account_id=normalized_account_id,
                kind=UsageKind.CONVERSION,
                source_id=entitlement_source_id,
                idempotency_key=normalized_key,
            )

            self._rewind(staged, operation="convert_source")
            artifact = self._converter.convert(
                staged,
                source_format=source_format,
                target_format=target,
                output_stem=self._output_stem(normalized_filename),
            )
        finally:
            staged.close()

        logger.info(
            {
                "event": "model_conversion_service_convert_completed",
                "account_id": normalized_account_id,
                "conversion_id": normalized_conversion_id,
                "source_format": source_format.value,
                "target_format": target.value,
                "source_size_bytes": source_size,
                "output_size_bytes": artifact.size_bytes,
                "ifc_schema": inspection.schema,
            }
        )
        return ModelConversionResult(
            conversion_id=normalized_conversion_id,
            source_hash=source_hash,
            source_size_bytes=source_size,
            inspection=inspection,
            entitlement=entitlement,
            artifact=artifact,
        )


__all__ = ["ModelConversionResult", "ModelConversionService"]
