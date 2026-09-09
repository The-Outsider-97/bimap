"""Provider-neutral model-conversion application port for BIMAP."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO

from ..utils.app_errors import *
from ..utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Model Conversion Port")
printer = PrettyPrinter()

_COMPONENT = "model_conversion"


class ModelSourceFormat(str, Enum):
    IFC = "ifc"

    @classmethod
    def parse(cls, value: "ModelSourceFormat | str") -> "ModelSourceFormat":
        if isinstance(value, cls):
            return value
        normalized = require_app_text(
            value,
            field="source_format",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="parse_source_format",
        ).casefold()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise UnsupportedAppInputError(
                "Unsupported model source format.",
                component=_COMPONENT,
                operation="parse_source_format",
                field="source_format",
                context={"received": normalized, "allowed": tuple(item.value for item in cls)},
                cause=exc,
            ) from exc


class ModelTargetFormat(str, Enum):
    GLB = "glb"
    OBJ = "obj"

    @classmethod
    def parse(cls, value: "ModelTargetFormat | str") -> "ModelTargetFormat":
        if isinstance(value, cls):
            return value
        normalized = require_app_text(
            value,
            field="target_format",
            error_type=UnsupportedAppInputError,
            component=_COMPONENT,
            operation="parse_target_format",
        ).casefold()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise UnsupportedAppInputError(
                "Unsupported model target format.",
                component=_COMPONENT,
                operation="parse_target_format",
                field="target_format",
                context={"received": normalized, "allowed": tuple(item.value for item in cls)},
                cause=exc,
            ) from exc


@dataclass(frozen=True, slots=True)
class ModelConversionCapability:
    source_format: ModelSourceFormat | str
    extensions: tuple[str, ...]
    target_formats: tuple[ModelTargetFormat | str, ...]

    def __post_init__(self) -> None:
        source = ModelSourceFormat.parse(self.source_format)
        if not self.extensions:
            raise AppValidationError(
                "A conversion capability requires at least one source extension.",
                component=_COMPONENT,
                operation="validate_capability",
                field="extensions",
            )
        extensions: list[str] = []
        for raw in self.extensions:
            value = require_app_text(
                raw,
                field="extension",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_capability",
                max_length=32,
            ).casefold()
            if not value.startswith("."):
                value = f".{value}"
            if value not in extensions:
                extensions.append(value)
        if not self.target_formats:
            raise AppValidationError(
                "A conversion capability requires at least one target format.",
                component=_COMPONENT,
                operation="validate_capability",
                field="target_formats",
            )
        targets: list[ModelTargetFormat] = []
        for raw in self.target_formats:
            target = ModelTargetFormat.parse(raw)
            if target not in targets:
                targets.append(target)
        object.__setattr__(self, "source_format", source)
        object.__setattr__(self, "extensions", tuple(extensions))
        object.__setattr__(self, "target_formats", tuple(targets))

    def to_dict(self) -> dict[str, object]:
        return {
            "source_format": self.source_format.value,
            "extensions": self.extensions,
            "target_formats": tuple(item.value for item in self.target_formats),
        }


@dataclass(frozen=True, slots=True)
class ModelSourceInspection:
    source_format: ModelSourceFormat | str
    schema: str
    product_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_format", ModelSourceFormat.parse(self.source_format))
        object.__setattr__(
            self,
            "schema",
            require_app_text(
                self.schema,
                field="schema",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_inspection",
                max_length=128,
            ),
        )
        object.__setattr__(
            self,
            "product_count",
            require_non_negative_int(
                self.product_count,
                field="product_count",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_inspection",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_format": self.source_format.value,
            "schema": self.schema,
            "product_count": self.product_count,
        }


@dataclass(frozen=True, slots=True)
class ConvertedModelArtifact:
    stream: BinaryIO
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    hash_algorithm: str = "sha256"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stream",
            require_binary_stream(
                self.stream,
                field="stream",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_artifact",
            ),
        )
        filename = require_app_text(
            self.filename,
            field="filename",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=255,
        )
        if (
            "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
            or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        ):
            raise AppValidationError(
                "Converted artifact filename is not a safe basename.",
                component=_COMPONENT,
                operation="validate_artifact",
                field="filename",
            )
        object.__setattr__(self, "filename", filename)
        object.__setattr__(
            self,
            "content_type",
            require_app_text(
                self.content_type,
                field="content_type",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_artifact",
                max_length=128,
            ),
        )
        object.__setattr__(
            self,
            "size_bytes",
            require_non_negative_int(
                self.size_bytes,
                field="size_bytes",
                error_type=AppValidationError,
                component=_COMPONENT,
                operation="validate_artifact",
            ),
        )
        if self.size_bytes == 0:
            raise AppValidationError(
                "Converted model artifact cannot be empty.",
                component=_COMPONENT,
                operation="validate_artifact",
                field="size_bytes",
            )
        algorithm = require_app_text(
            self.hash_algorithm,
            field="hash_algorithm",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=32,
        ).casefold()
        digest = require_app_text(
            self.content_hash,
            field="content_hash",
            error_type=AppValidationError,
            component=_COMPONENT,
            operation="validate_artifact",
            max_length=256,
        ).casefold()
        if algorithm == "sha256":
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise AppValidationError(
                    "SHA-256 artifact hash must be a 64-character hexadecimal digest.",
                    component=_COMPONENT,
                    operation="validate_artifact",
                    field="content_hash",
                )
        object.__setattr__(self, "hash_algorithm", algorithm)
        object.__setattr__(self, "content_hash", digest)

    def close(self) -> None:
        self.stream.close()


class ModelConverter(ABC):
    """Technical model-conversion dependency isolated from BIMAP orchestration."""

    @property
    @abstractmethod
    def capabilities(self) -> tuple[ModelConversionCapability, ...]:
        raise NotImplementedError

    @abstractmethod
    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
    ) -> ModelSourceInspection:
        raise NotImplementedError

    @abstractmethod
    def convert(
        self,
        stream: BinaryIO,
        *,
        source_format: ModelSourceFormat,
        target_format: ModelTargetFormat,
        output_stem: str,
    ) -> ConvertedModelArtifact:
        raise NotImplementedError


__all__ = [
    "ModelSourceFormat",
    "ModelTargetFormat",
    "ModelConversionCapability",
    "ModelSourceInspection",
    "ConvertedModelArtifact",
    "ModelConverter",
]
