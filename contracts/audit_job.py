"""
Stable versioned job envelope submitted to BIMAP's audit worker/SLAI boundary.

The job contract is deliberately reference-oriented. It carries order identity,
product identity, the immutable order revision observed when the job was
created, and references to approved evidence/manifests. It does not embed raw
customer files, a complete application object graph, database clients, SLAI
agents, or report-generation state.

The application/queue layer remains responsible for exactly-once submission,
retry policy, cancellation, and persistence. This contract only makes the work
unit deterministic and serializable.

Dependency direction
--------------------
contracts.utils
contracts.versions
contracts.order
        ↑
contracts.audit_job
        ↑
app.ports.queue / workers / slai.job_envelope

No lower contract module imports ``audit_job.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain.products.models import ProductCode
from ..domain.utils.domain_errors import DomainError
from ..domain.utils.domain_helpers import *
from .utils.contracts_errors import *
from .utils.contracts_helpers import *
from .order import OrderContract
from .versions import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Contracts Audit Job")
printer = PrettyPrinter()

_CONTRACT = ContractName.AUDIT_JOB.value
_SUPPORTED_VERSIONS = SUPPORTED_SCHEMA_VERSIONS[_CONTRACT]


def _announce(action: str) -> None:
    """Emit a method-start diagnostic without customer evidence content."""
    printer.status("CONTRACTS", action, "info")
    logger.debug({"event": "audit_job_method_start", "action": action})


def _normalize_text(value: Any, *, field: str) -> str:
    _announce(f"Normalizing Audit Job text field: {field}")
    try:
        return require_text(value, field=field)
    except DomainError as exc:
        raise ContractValidationError(
            "Audit Job contains invalid required text.",
            contract=_CONTRACT,
            field=field,
            cause=exc,
        ) from exc


def _normalize_non_negative_int(value: Any, *, field: str) -> int:
    _announce(f"Normalizing Audit Job integer field: {field}")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError(
            "Audit Job integer field must be an integer.",
            contract=_CONTRACT,
            field=field,
            context={"received_type": type(value).__name__},
        )
    if value < 0:
        raise ContractValidationError(
            "Audit Job integer field must be non-negative.",
            contract=_CONTRACT,
            field=field,
            context={"received": value},
        )
    return value


@dataclass(frozen=True, slots=True)
class AuditJob:
    """Immutable externally serializable BIMAP audit work envelope."""

    job_id: str
    order_id: str
    order_version: int
    product_code: ProductCode | str
    submitted_at: str | datetime

    evidence_refs: tuple[str, ...] = ()
    evidence_manifest_ref: str | None = None
    metadata: Mapping[str, Any] | None = None
    schema_version: str = AUDIT_JOB_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _announce("Validating Audit Job contract")

        ensure_supported_schema_version(
            self.schema_version,
            supported=_SUPPORTED_VERSIONS,
            contract=_CONTRACT,
        )

        job_id = _normalize_text(self.job_id, field="job_id")
        order_id = _normalize_text(self.order_id, field="order_id")
        order_version = _normalize_non_negative_int(
            self.order_version,
            field="order_version",
        )

        try:
            product_code = ProductCode.parse(self.product_code)
            submitted_at = ensure_utc_datetime(self.submitted_at, field="submitted_at")
            evidence_refs = stable_unique_text(self.evidence_refs, field="evidence_refs")
        except DomainError as exc:
            raise ContractValidationError(
                "Audit Job contains invalid product, timestamp, or evidence references.",
                contract=_CONTRACT,
                cause=exc,
            ) from exc

        if self.evidence_manifest_ref is None:
            manifest_ref = None
        else:
            manifest_ref = _normalize_text(self.evidence_manifest_ref, field="evidence_manifest_ref")

        if not evidence_refs and manifest_ref is None:
            raise ContractIntegrityError(
                "Audit Job must reference approved evidence or an evidence manifest.",
                contract=_CONTRACT,
                field="evidence_refs",
            )

        metadata: dict[str, Any]
        if self.metadata is None:
            metadata = {}
        else:
            if not isinstance(self.metadata, Mapping):
                raise ContractValidationError(
                    "Audit Job metadata must be a mapping or None.",
                    contract=_CONTRACT,
                    field="metadata",
                    context={"received_type": type(self.metadata).__name__},
                )
            primitive = to_json_primitive(self.metadata, contract=_CONTRACT, field="metadata")
            if not isinstance(primitive, dict):
                raise ContractValidationError(
                    "Audit Job metadata must serialize to a JSON object.",
                    contract=_CONTRACT, field="metadata")
            metadata = primitive

        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "order_version", order_version)
        object.__setattr__(self, "product_code", product_code)
        object.__setattr__(self, "submitted_at", format_utc_datetime(submitted_at))
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "evidence_manifest_ref", manifest_ref)
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "schema_version", str(self.schema_version).strip())

        logger.debug(
            {
                "event": "audit_job_validated",
                "job_id": self.job_id,
                "order_id": self.order_id,
                "order_version": self.order_version,
                "product_code": product_code.value,
                "evidence_ref_count": len(evidence_refs),
                "has_manifest_ref": manifest_ref is not None,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-ready audit-job representation."""
        _announce("Serializing Audit Job contract")
        product_code = (
            self.product_code
            if isinstance(self.product_code, ProductCode)
            else ProductCode.parse(self.product_code)
        )
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "order_id": self.order_id,
            "order_version": self.order_version,
            "product_code": product_code.value,
            "submitted_at": self.submitted_at,
            "evidence_refs": list(self.evidence_refs),
            "evidence_manifest_ref": self.evidence_manifest_ref,
            "metadata": to_json_primitive( self.metadata or {}, contract=_CONTRACT, field="metadata"),
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the job envelope using BIMAP canonical JSON rules."""
        _announce("Encoding Audit Job JSON")
        return canonical_json_dumps(
            self.to_dict(),
            contract=_CONTRACT,
            pretty=pretty,
        )

    @classmethod
    def from_order(
        cls,
        order: OrderContract,
        *,
        job_id: str,
        evidence_refs: tuple[str, ...] = (),
        evidence_manifest_ref: str | None = None,
        submitted_at: str | datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
        schema_version: str = AUDIT_JOB_SCHEMA_VERSION,
    ) -> "AuditJob":
        """Construct a minimal job envelope from an immutable order contract."""
        _announce("Creating Audit Job from Order contract")

        if not isinstance(order, OrderContract):
            raise ContractValidationError(
                "order must be an OrderContract instance.",
                contract=_CONTRACT,
                field="order",
                context={"received_type": type(order).__name__},
            )

        return cls(
            job_id=job_id,
            order_id=order.order_id,
            order_version=order.version,
            product_code=order.product_code,
            submitted_at=submitted_at if submitted_at is not None else utc_now(),
            evidence_refs=evidence_refs,
            evidence_manifest_ref=evidence_manifest_ref,
            metadata=metadata,
            schema_version=schema_version,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AuditJob":
        """Parse a strict versioned audit-job mapping."""
        _announce("Deserializing Audit Job contract")

        data = validate_contract_fields(
            payload,
            required=(
                "schema_version",
                "job_id",
                "order_id",
                "order_version",
                "product_code",
                "submitted_at",
            ),
            optional=(
                "evidence_refs",
                "evidence_manifest_ref",
                "metadata",
            ),
            contract=_CONTRACT,
        )
        return cls(
            schema_version=data["schema_version"],
            job_id=data["job_id"],
            order_id=data["order_id"],
            order_version=data["order_version"],
            product_code=data["product_code"],
            submitted_at=data["submitted_at"],
            evidence_refs=data.get("evidence_refs") or (),
            evidence_manifest_ref=data.get("evidence_manifest_ref"),
            metadata=data.get("metadata") or {},
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "AuditJob":
        """Decode canonical JSON and validate it as an Audit Job."""
        _announce("Decoding Audit Job JSON")
        data = canonical_json_loads(payload, contract=_CONTRACT)
        if not isinstance(data, Mapping):
            raise ContractDeserializationError(
                "Audit Job JSON root must be an object.",
                contract=_CONTRACT,
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data)


__all__ = ["AuditJob"]


if __name__ == "__main__":
    print("\n=== Running Audit Job Contract Self-Test ===\n")
    printer.status("TEST", "Audit Job contract module initialized", "info")

    order = OrderContract(
        order_id="ORD-0001",
        product_code="family_audit",
        state="paid",
        created_at="2026-09-01T00:00:00Z",
        updated_at="2026-09-01T00:00:00Z",
        version=4,
    )
    job = AuditJob.from_order(
        order,
        job_id="JOB-0001",
        evidence_manifest_ref="manifest://ORDER-0001",
        submitted_at="2026-09-01T00:01:00Z",
    )
    assert job.order_version == 4
    assert AuditJob.from_json(job.to_json()) == job
    printer.status("PASS", "Audit Job round trip", "success")

    print("\n=== Test ran successfully ===\n")