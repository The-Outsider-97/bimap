"""
Controlled internal envelope passed from BIMAP into SLAI orchestration.

The versioned external/queue work-unit remains ``contracts.audit_job.AuditJob``.
This module does **not** redefine that contract.  ``SLAIJobEnvelope`` wraps an
already validated ``AuditJob`` with the normalized, policy-approved context that
SLAI is allowed to reason over and with the exact set of agents authorized for
that invocation.

Intended flow
-------------
    contracts/audit_job.py
            +
    normalized deterministic audit context
            +
    slai/agent_policy.py
            -> slai/job_envelope.py
            -> slai/orchestration.py
            -> SLAI agents

Raw customer files, database handles, storage clients, agent instances, and
arbitrary runtime objects are rejected at this boundary.  The grounded context
must be JSON-safe so it can be hashed, audited, retried, and inspected without
Python object identity affecting the work unit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .utils.slai_errors import *
from .utils.slai_helpers import *
from .agent_policy import SLAIAgentPolicy
from ..contracts.audit_job import AuditJob
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("SLAI Job Envelope")
printer = PrettyPrinter()


@dataclass(frozen=True, slots=True)
class SLAIJobEnvelope:
    """Immutable BIMAP -> SLAI runtime work envelope.

    Parameters
    ----------
    audit_job:
        Existing versioned BIMAP ``AuditJob`` contract.  Its evidence references
        remain authoritative for job/order identity and queue-level provenance.
    requested_agents:
        Exact normalized agent keys authorized for this invocation.
    grounded_context:
        JSON-safe normalized audit/evidence context.  It must represent data
        already accepted by BIMAP's deterministic/input-governance pipeline;
        this class intentionally does not parse raw files.
    correlation_id:
        Request/job correlation identifier used across logs and orchestration.
    created_at:
        UTC envelope-construction time.
    context_digest:
        Optional persisted SHA-256 digest.  When supplied during reconstruction,
        it must match the canonical grounded context exactly.
    """

    audit_job: AuditJob
    requested_agents: tuple[str, ...]
    grounded_context: Mapping[str, Any]
    correlation_id: str
    created_at: datetime | str = field(default_factory=utc_now)
    context_digest: str | None = None

    def __post_init__(self) -> None:
        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Validating BIMAP SLAI job envelope",
        )

        if not isinstance(self.audit_job, AuditJob):
            raise SLAIEnvelopeValidationError(
                "audit_job must be an AuditJob contract instance.",
                component="job_envelope",
                operation="validate",
                field="audit_job",
                context={"received_type": type(self.audit_job).__name__},
            )

        agents = normalize_agent_sequence(
            self.requested_agents,
            field="requested_agents",
            error_type=SLAIEnvelopeValidationError,
        )
        if not agents:
            raise SLAIEnvelopeValidationError(
                "SLAI job envelope requires at least one authorized agent.",
                component="job_envelope",
                operation="validate",
                field="requested_agents",
            )

        context = normalize_json_mapping(
            self.grounded_context,
            field="grounded_context",
        )
        if not context:
            raise SLAIEnvelopeValidationError(
                "grounded_context must contain normalized audit context.",
                component="job_envelope",
                operation="validate",
                field="grounded_context",
            )

        correlation_id = require_text(
            self.correlation_id,
            field="correlation_id",
            error_type=SLAIEnvelopeValidationError,
        )
        created_at = ensure_utc_datetime(
            self.created_at,
            field="created_at",
            error_type=SLAIEnvelopeValidationError,
        )

        computed_digest = stable_payload_digest(context)
        if self.context_digest is not None:
            supplied_digest = require_text(
                self.context_digest,
                field="context_digest",
                error_type=SLAIEnvelopeValidationError,
            ).casefold()
            if not digests_equal(supplied_digest, computed_digest):
                raise SLAIEnvelopeIntegrityError(
                    "SLAI job envelope context digest does not match its grounded context.",
                    component="job_envelope",
                    operation="validate_integrity",
                    field="context_digest",
                    context={
                        "job_id": self.audit_job.job_id,
                        "correlation_id": correlation_id,
                    },
                )

        object.__setattr__(self, "requested_agents", agents)
        object.__setattr__(self, "grounded_context", freeze_json_value(context))
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "context_digest", computed_digest)

        logger.info(
            "SLAI job envelope validated: job_id=%s order_id=%s agents=%d correlation_id=%s context_digest=%s",
            self.audit_job.job_id,
            self.audit_job.order_id,
            len(agents),
            correlation_id,
            computed_digest,
        )

    @classmethod
    def build(
        cls,
        audit_job: AuditJob,
        *,
        policy: SLAIAgentPolicy,
        grounded_context: Mapping[str, Any],
        requested_agents: Any = None,
        correlation_id: str | None = None,
        created_at: datetime | str | None = None,
        max_context_bytes: int | None = None,
    ) -> "SLAIJobEnvelope":
        """Build an envelope while enforcing the effective agent policy.

        ``max_context_bytes`` is intentionally caller-configured.  The BIMAP
        implementation report requires bounded/controlled processing but does
        not prescribe one universal byte threshold for normalized SLAI context.
        """

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Building policy-approved SLAI job envelope",
        )

        if not isinstance(policy, SLAIAgentPolicy):
            raise SLAIEnvelopeValidationError(
                "policy must be an SLAIAgentPolicy instance.",
                component="job_envelope",
                operation="build",
                field="policy",
                context={"received_type": type(policy).__name__},
            )
        if not isinstance(audit_job, AuditJob):
            raise SLAIEnvelopeValidationError(
                "audit_job must be an AuditJob contract instance.",
                component="job_envelope",
                operation="build",
                field="audit_job",
                context={"received_type": type(audit_job).__name__},
            )

        agents = policy.resolve_requested_agents(requested_agents)
        normalized_context = normalize_json_mapping(
            grounded_context,
            field="grounded_context",
        )
        if not normalized_context:
            raise SLAIEnvelopeValidationError(
                "grounded_context must contain normalized audit context.",
                component="job_envelope",
                operation="build",
                field="grounded_context",
            )

        if max_context_bytes is not None:
            if isinstance(max_context_bytes, bool) or not isinstance(max_context_bytes, int):
                raise SLAIEnvelopeValidationError(
                    "max_context_bytes must be an integer or None.",
                    component="job_envelope",
                    operation="build",
                    field="max_context_bytes",
                    context={"received_type": type(max_context_bytes).__name__},
                )
            if max_context_bytes <= 0:
                raise SLAIEnvelopeValidationError(
                    "max_context_bytes must be greater than zero when configured.",
                    component="job_envelope",
                    operation="build",
                    field="max_context_bytes",
                )
            actual_size = payload_size_bytes(normalized_context)
            if actual_size > max_context_bytes:
                raise SLAIEnvelopeValidationError(
                    "Grounded SLAI context exceeds the configured size limit.",
                    component="job_envelope",
                    operation="build",
                    field="grounded_context",
                    context={
                        "actual_bytes": actual_size,
                        "max_context_bytes": max_context_bytes,
                        "job_id": audit_job.job_id,
                    },
                )

        return cls(
            audit_job=audit_job,
            requested_agents=agents,
            grounded_context=normalized_context,
            correlation_id=correlation_id or generate_identifier("CORR"),
            created_at=created_at if created_at is not None else utc_now(),
        )

    def assert_policy(self, policy: SLAIAgentPolicy) -> None:
        """Revalidate every stored requested agent against a current policy."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Revalidating envelope against current SLAI agent policy",
            context={"job_id": self.audit_job.job_id},
        )
        if not isinstance(policy, SLAIAgentPolicy):
            raise SLAIEnvelopeValidationError(
                "policy must be an SLAIAgentPolicy instance.",
                component="job_envelope",
                operation="assert_policy",
                field="policy",
                context={"received_type": type(policy).__name__},
            )
        for agent in self.requested_agents:
            policy.require_allowed(agent)

    def assert_integrity(self) -> None:
        """Recompute and verify the canonical grounded-context digest."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Verifying SLAI envelope context integrity",
            context={"job_id": self.audit_job.job_id},
        )
        computed = stable_payload_digest(self.grounded_context)
        if self.context_digest is None or not digests_equal(self.context_digest, computed):
            raise SLAIEnvelopeIntegrityError(
                "SLAI job envelope failed its context-integrity check.",
                component="job_envelope",
                operation="assert_integrity",
                field="context_digest",
                context={
                    "job_id": self.audit_job.job_id,
                    "correlation_id": self.correlation_id,
                },
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete internal JSON-ready envelope representation."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Serializing SLAI job envelope",
            context={"job_id": self.audit_job.job_id},
        )
        self.assert_integrity()
        created_at = ensure_utc_datetime(
            self.created_at,
            field="created_at",
            error_type=SLAIEnvelopeValidationError,
        )
        return {
            "audit_job": self.audit_job.to_dict(),
            "requested_agents": list(self.requested_agents),
            "grounded_context": thaw_json_value(self.grounded_context),
            "correlation_id": self.correlation_id,
            "created_at": format_utc_datetime(created_at),
            "context_digest": self.context_digest,
        }

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize the internal SLAI envelope using canonical JSON."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Encoding SLAI job envelope JSON",
            context={"job_id": self.audit_job.job_id},
        )
        return canonical_json_dumps(self.to_dict(), pretty=pretty)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        policy: SLAIAgentPolicy | None = None,
    ) -> "SLAIJobEnvelope":
        """Reconstruct an envelope and optionally re-authorize its agents."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Reconstructing SLAI job envelope",
        )
        data = require_mapping(
            payload,
            field="slai_job_envelope",
            error_type=SLAIEnvelopeValidationError,
        )
        required_fields = {
            "audit_job",
            "requested_agents",
            "grounded_context",
            "correlation_id",
            "created_at",
            "context_digest",
        }
        missing = sorted(required_fields - set(data))
        unexpected = sorted(set(data) - required_fields)
        if missing or unexpected:
            raise SLAIEnvelopeValidationError(
                "SLAI job envelope has an invalid field set.",
                component="job_envelope",
                operation="from_dict",
                context={"missing_fields": missing, "unexpected_fields": unexpected},
            )

        try:
            audit_job = AuditJob.from_dict(
                require_mapping(
                    data["audit_job"],
                    field="audit_job",
                    error_type=SLAIEnvelopeValidationError,
                )
            )
        except SLAIEnvelopeValidationError:
            raise
        except Exception as exc:
            raise SLAIEnvelopeSerializationError(
                "Unable to reconstruct the embedded AuditJob contract.",
                component="job_envelope",
                operation="from_dict",
                field="audit_job",
                cause=exc,
            ) from exc

        raw_requested_agents = data["requested_agents"]
        if isinstance(raw_requested_agents, (str, bytes, bytearray)) or not isinstance(
            raw_requested_agents, (list, tuple),
        ):
            raise SLAIEnvelopeValidationError(
                "requested_agents must be a list or tuple of agent names.",
                component="job_envelope",
                operation="from_dict",
                field="requested_agents",
                context={"received_type": type(raw_requested_agents).__name__},
            )

        envelope = cls(
            audit_job=audit_job,
            requested_agents=tuple(raw_requested_agents),
            grounded_context=require_mapping(
                data["grounded_context"],
                field="grounded_context",
                error_type=SLAIEnvelopeValidationError,
            ),
            correlation_id=data["correlation_id"],
            created_at=data["created_at"],
            context_digest=data["context_digest"],
        )
        if policy is not None:
            envelope.assert_policy(policy)
        return envelope

    @classmethod
    def from_json(
        cls,
        payload: str | bytes | bytearray,
        *,
        policy: SLAIAgentPolicy | None = None,
    ) -> "SLAIJobEnvelope":
        """Decode JSON and reconstruct a validated SLAI job envelope."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Decoding SLAI job envelope JSON",
        )
        data = canonical_json_loads(payload)
        if not isinstance(data, Mapping):
            raise SLAIEnvelopeSerializationError(
                "SLAI job envelope JSON root must be an object.",
                component="job_envelope",
                operation="from_json",
                context={"received_type": type(data).__name__},
            )
        return cls.from_dict(data, policy=policy)

    def summary(self) -> dict[str, Any]:
        """Return logging-safe envelope metadata without grounded content."""

        announce_method_start(
            printer,
            logger,
            "SLAI ENVELOPE",
            "Creating safe SLAI job envelope summary",
            context={"job_id": self.audit_job.job_id},
        )
        created_at = ensure_utc_datetime(
            self.created_at,
            field="created_at",
            error_type=SLAIEnvelopeValidationError,
        )
        return {
            "job_id": self.audit_job.job_id,
            "order_id": self.audit_job.order_id,
            "product_code": getattr(self.audit_job.product_code, "value", str(self.audit_job.product_code)),
            "requested_agents": list(self.requested_agents),
            "agent_count": len(self.requested_agents),
            "correlation_id": self.correlation_id,
            "created_at": format_utc_datetime(created_at),
            "context_digest": self.context_digest,
            "context_bytes": payload_size_bytes(self.grounded_context),
        }


__all__ = ["SLAIJobEnvelope"]