"""
Build and verify the BIMAP customer delivery ZIP package.

The package builder is intentionally a packaging component rather than a
storage component. It receives already-generated artifacts and their validated
``ReportManifest``, verifies integrity through ``ArtifactManifest``, then
creates a deterministic ZIP byte stream suitable for publication by an
application/infrastructure storage service.

``audit_bundle.zip`` is a delivery container and is not listed as an artifact
inside its own manifest. Likewise, ``report_manifest.json`` is embedded as the
control document for the package but is not self-hashed by that same manifest;
this avoids recursive/self-referential integrity metadata.
"""

from __future__ import annotations

import io
import zipfile

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from ..contracts.utils.contracts_errors import ContractError
from .utils.reporting_errors import *
from .utils.reporting_helpers import announce_reporting_action
from ..contracts.report_manifest import ReportManifest
from .artifact_manifest import ArtifactManifest
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Package Builder")
printer = PrettyPrinter()

_COMPONENT = "package_builder"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_BYTES_TYPES = (bytes, bytearray, memoryview)
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


def _require_basename(value: Any, *, field: str) -> str:
    announce_reporting_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Validating package entry filename",
        event="package_builder_filename_validate_start",
        context={"field": field},
    )

    if not isinstance(value, str) or not value.strip():
        raise ReportingValidationError(
            "Package entry filename must be non-empty text.",
            component=_COMPONENT,
            field=field,
            context={"received_type": type(value).__name__},
        )
    filename = value.strip()
    if PurePosixPath(filename).name != filename or "/" in filename or "\\" in filename:
        raise ReportingValidationError(
            "Package entry filename must be a basename and may not contain paths.",
            component=_COMPONENT,
            field=field,
        )
    return filename


def _as_bytes(value: Any, *, field: str) -> bytes:
    announce_reporting_action(
        printer,
        logger,
        component=_COMPONENT,
        action="Normalizing package artifact bytes",
        event="package_builder_bytes_normalize_start",
        context={"field": field, "received_type": type(value).__name__},
    )
    if not isinstance(value, _BYTES_TYPES):
        raise ReportingValidationError(
            "Package artifacts must be bytes-like values.",
            component=_COMPONENT,
            field=field,
            context={"received_type": type(value).__name__},
        )
    return bytes(value)


class PackageBuilder:
    """Construct and verify deterministic BIMAP report delivery bundles."""

    def __init__(
        self,
        *,
        artifact_manifest: ArtifactManifest | None = None,
        compression: int = zipfile.ZIP_DEFLATED,
        compresslevel: int | None = 6,
    ) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing package builder",
            event="package_builder_init",
            context={"compression": compression, "compresslevel": compresslevel},
        )

        if compression not in _ALLOWED_COMPRESSION:
            raise ReportingValidationError(
                "PackageBuilder supports ZIP_STORED or ZIP_DEFLATED compression only.",
                component=_COMPONENT,
                field="compression",
                context={"received": compression},
            )
        if compresslevel is not None:
            if isinstance(compresslevel, bool) or not isinstance(compresslevel, int):
                raise ReportingValidationError(
                    "compresslevel must be an integer or None.",
                    component=_COMPONENT,
                    field="compresslevel",
                    context={"received_type": type(compresslevel).__name__},
                )
            if compression == zipfile.ZIP_DEFLATED and not 0 <= compresslevel <= 9:
                raise ReportingValidationError(
                    "DEFLATED compresslevel must be between 0 and 9.",
                    component=_COMPONENT,
                    field="compresslevel",
                    context={"received": compresslevel},
                )

        self.artifact_manifest = artifact_manifest or ArtifactManifest()
        if not isinstance(self.artifact_manifest, ArtifactManifest):
            raise ReportingValidationError(
                "artifact_manifest must be an ArtifactManifest instance.",
                component=_COMPONENT,
                field="artifact_manifest",
                context={"received_type": type(self.artifact_manifest).__name__},
            )
        self.compression = compression
        self.compresslevel = compresslevel
        logger.info({"event": "package_builder_initialized"})

    def _write_entry(self, archive: zipfile.ZipFile, filename: str, data: bytes) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Writing deterministic package entry",
            event="package_builder_write_entry_start",
            context={"filename": filename, "size_bytes": len(data)},
        )

        info = zipfile.ZipInfo(filename=filename, date_time=_ZIP_EPOCH)
        info.compress_type = self.compression
        info.create_system = 3
        info.external_attr = (0o644 & 0xFFFF) << 16
        info.flag_bits |= 0x800  # UTF-8 filename flag.

        kwargs: dict[str, Any] = {"compress_type": self.compression}
        if self.compression == zipfile.ZIP_DEFLATED and self.compresslevel is not None:
            kwargs["compresslevel"] = self.compresslevel
        archive.writestr(info, data, **kwargs)

    def build_package(
        self,
        manifest: ReportManifest,
        artifacts: Mapping[str, bytes | bytearray | memoryview],
        *,
        manifest_filename: str = "report_manifest.json",
    ) -> bytes:
        """Return deterministic ZIP bytes containing manifest plus all artifacts."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building BIMAP audit delivery package",
            event="package_builder_build_start",
            context={"report_id": getattr(manifest, "report_id", None)},
        )

        manifest_name = _require_basename(manifest_filename, field="manifest_filename")
        if not isinstance(manifest, ReportManifest):
            raise ReportManifestValidationError(
                "manifest must be a ReportManifest instance.",
                component=_COMPONENT,
                field="manifest",
                context={"received_type": type(manifest).__name__},
            )
        if not isinstance(artifacts, Mapping):
            raise ReportingValidationError(
                "artifacts must be a mapping of filename to bytes.",
                component=_COMPONENT,
                field="artifacts",
                context={"received_type": type(artifacts).__name__},
            )
        if manifest_name in artifacts:
            raise PackageBuilderError(
                "The report manifest control file must not also be supplied as a managed artifact.",
                component=_COMPONENT,
                field="manifest_filename",
                context={"manifest_filename": manifest_name},
            )

        try:
            self.artifact_manifest.verify(manifest, artifacts)
            manifest_bytes = manifest.to_json(pretty=True).encode("utf-8")

            normalized_artifacts = {
                _require_basename(name, field="artifacts.filename"): _as_bytes(
                    payload,
                    field=f"artifacts[{name!r}]",
                )
                for name, payload in artifacts.items()
            }

            buffer = io.BytesIO()
            with zipfile.ZipFile(
                buffer,
                mode="w",
                compression=self.compression,
                allowZip64=True,
                strict_timestamps=True,
            ) as archive:
                self._write_entry(archive, manifest_name, manifest_bytes)
                for filename in sorted(normalized_artifacts):
                    self._write_entry(archive, filename, normalized_artifacts[filename])

            package = buffer.getvalue()
            logger.info(
                {
                    "event": "audit_package_built",
                    "report_id": manifest.report_id,
                    "artifact_count": len(artifacts),
                    "package_size_bytes": len(package),
                }
            )
            return package
        except ReportingError:
            raise
        except ContractError as exc:
            raise PackageBuilderError(
                "Report manifest could not be serialized for packaging.",
                component=_COMPONENT,
                cause=exc,
            ) from exc
        except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile) as exc:
            raise PackageBuilderError(
                "BIMAP delivery package construction failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def verify_package(
        self,
        package: bytes | bytearray | memoryview,
        *,
        expected_manifest: ReportManifest | None = None,
        manifest_filename: str = "report_manifest.json",
    ) -> ReportManifest:
        """
        Verify package structure and artifact integrity, returning its manifest.

        This method is suitable for pre-publication verification or regression
        tests. It rejects directories, duplicate/path-bearing entries, missing
        manifest control data, unexpected payload entries, and artifact hash or
        size mismatches.
        """
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Verifying BIMAP audit delivery package",
            event="package_builder_verify_start",
        )

        manifest_name = _require_basename(manifest_filename, field="manifest_filename")
        data = _as_bytes(package, field="package")

        if expected_manifest is not None and not isinstance(expected_manifest, ReportManifest):
            raise ReportManifestValidationError(
                "expected_manifest must be a ReportManifest instance or None.",
                component=_COMPONENT,
                field="expected_manifest",
                context={"received_type": type(expected_manifest).__name__},
            )

        try:
            with zipfile.ZipFile(io.BytesIO(data), mode="r") as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]

                if len(names) != len(set(names)):
                    raise PackageBuilderError(
                        "Delivery package contains duplicate ZIP entry names.",
                        component=_COMPONENT,
                    )
                for index, info in enumerate(infos):
                    if info.is_dir():
                        raise PackageBuilderError(
                            "Delivery package must not contain directory entries.",
                            component=_COMPONENT,
                            field=f"entries[{index}]",
                        )
                    _require_basename(info.filename, field=f"entries[{index}].filename")

                if manifest_name not in names:
                    raise PackageBuilderError(
                        "Delivery package does not contain its report manifest control file.",
                        component=_COMPONENT,
                        field="manifest_filename",
                    )

                manifest = ReportManifest.from_json(archive.read(manifest_name))
                if expected_manifest is not None and manifest.to_dict() != expected_manifest.to_dict():
                    raise PackageBuilderError(
                        "Embedded report manifest does not match the expected manifest.",
                        component=_COMPONENT,
                        field="manifest",
                        context={
                            "embedded_report_id": manifest.report_id,
                            "expected_report_id": expected_manifest.report_id,
                        },
                    )

                managed_names = {item.filename for item in manifest.artifacts}
                expected_entries = managed_names | {manifest_name}
                actual_entries = set(names)
                if actual_entries != expected_entries:
                    raise PackageBuilderError(
                        "Delivery package entries do not match its report manifest.",
                        component=_COMPONENT,
                        field="entries",
                        context={
                            "missing": tuple(sorted(expected_entries - actual_entries)),
                            "unexpected": tuple(sorted(actual_entries - expected_entries)),
                        },
                    )

                artifacts = {
                    filename: archive.read(filename)
                    for filename in managed_names
                }
                self.artifact_manifest.verify(manifest, artifacts)

            logger.info(
                {
                    "event": "audit_package_verified",
                    "report_id": manifest.report_id,
                    "artifact_count": len(manifest.artifacts),
                }
            )
            return manifest
        except ReportingError:
            raise
        except ContractError as exc:
            raise ReportManifestValidationError(
                "Embedded report manifest is invalid.",
                component=_COMPONENT,
                cause=exc,
            ) from exc
        except (OSError, RuntimeError, KeyError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise PackageBuilderError(
                "Delivery package verification failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc


__all__ = ["PackageBuilder"]


if __name__ == "__main__":
    print("\n=== Running Package Builder Self-Test ===\n")
    printer.status("TEST", "Package Builder initialized", "info")

    artifacts = {"findings.json": b"[]"}
    manifest_service = ArtifactManifest()
    manifest = manifest_service.create_manifest(
        report_id="REPORT-TEST",
        order_id="ORDER-TEST",
        report_version="1.0",
        generated_at="2026-09-01T00:00:00Z",
        artifacts=artifacts,
        artifact_ids={"findings.json": "ART-FINDINGS"},
    )
    builder = PackageBuilder(artifact_manifest=manifest_service)
    package = builder.build_package(manifest, artifacts)
    assert builder.verify_package(package, expected_manifest=manifest) == manifest
    printer.status("PASS", "Delivery package build/verify round trip", "success")

    print("\n=== Test ran successfully ===\n")