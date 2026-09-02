"""
Canonical deterministic audit context for BIMAP Level 4.

``AuditContext`` is the immutable, engine-owned view of evidence that has already
passed the external contract/ingestion boundary.  It is deliberately smaller
than an application job, persistence record, SLAI envelope, or report model.
It contains only normalized evidence plus the minimal identifiers/grouping
needed by deterministic audit logic.

Architectural boundary
----------------------
contracts / domain evidence
        ↓
audit_engine.ingestion / normalization
        ↓
audit_engine.context
        ↓
rules / validation / product-specific audit engines

The context never opens files, loads configuration, invokes SLAI, performs
reporting, or stores infrastructure handles.  Customer evidence content is not
written to logs; method diagnostics contain identifiers/counts only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field as dataclass_field
from types import MappingProxyType
from typing import Any

from ..domain.evidence.models import EvidenceItem
from ..domain.products.models import ProductCode
from ..domain.utils.domain_errors import DomainError
from .utils.engine_errors import *
from .utils.engine_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Context")
printer = PrettyPrinter()


def _normalize_evidence_items(values: Iterable[EvidenceItem]) -> tuple[EvidenceItem, ...]:
    """Validate evidence membership, identity uniqueness, and source consistency."""
    announce_engine_action(
        printer,
        logger,
        component="context",
        action="Normalizing audit-context evidence",
        event="audit_context_normalize_evidence_start",
    )

    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            "evidence_items must be an iterable of EvidenceItem values.",
            component="context",
            operation="normalize_evidence",
            field="evidence_items",
            context={"received_type": type(values).__name__},
        )

    try:
        items = tuple(values)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            "evidence_items must be iterable.",
            component="context",
            operation="normalize_evidence",
            field="evidence_items",
            context={"received_type": type(values).__name__},
            cause=exc,
        ) from exc

    seen_ids: set[str] = set()
    source_signatures: dict[str, tuple[str, str, str]] = {}

    for index, item in enumerate(items):
        if not isinstance(item, EvidenceItem):
            raise UnsupportedEngineInputError(
                "AuditContext accepts canonical EvidenceItem values only.",
                component="context",
                operation="normalize_evidence",
                field=f"evidence_items[{index}]",
                context={"received_type": type(item).__name__},
            )

        if item.evidence_id in seen_ids:
            raise EngineIntegrityError(
                "AuditContext contains duplicate evidence identifiers.",
                component="context",
                operation="normalize_evidence",
                field="evidence_items",
                context={"evidence_id": item.evidence_id},
            )
        seen_ids.add(item.evidence_id)

        signature = (item.hash_algorithm, item.source_hash, item.source_type)
        previous = source_signatures.get(item.source_file_id)
        if previous is None:
            source_signatures[item.source_file_id] = signature
        elif previous != signature:
            raise EngineIntegrityError(
                "One source_file_id resolves to inconsistent source provenance in AuditContext.",
                component="context",
                operation="normalize_evidence",
                field="source_file_id",
                context={"source_file_id": item.source_file_id},
            )

    return items


def _normalize_group_index(value: Mapping[str, Iterable[str]] | None, *, evidence_ids: set[str]) -> Mapping[str, tuple[str, ...]]:
    """Normalize named evidence groups and require every reference to resolve."""
    announce_engine_action(
        printer,
        logger,
        component="context",
        action="Normalizing audit-context evidence groups",
        event="audit_context_normalize_groups_start",
    )

    if value is None:
        return MappingProxyType({})
    mapping = require_engine_mapping(
        value,
        field="evidence_groups",
        error_type=EngineValidationError,
    )

    normalized: dict[str, tuple[str, ...]] = {}
    for raw_name, raw_ids in mapping.items():
        name = require_engine_text(
            raw_name,
            field="evidence_groups.name",
            error_type=EngineValidationError,
        )
        if isinstance(raw_ids, (str, bytes, bytearray, Mapping)):
            raise EngineValidationError(
                "Each evidence group must be an iterable of evidence identifiers.",
                component="context",
                operation="normalize_groups",
                field=f"evidence_groups.{name}",
                context={"received_type": type(raw_ids).__name__},
            )

        try:
            iterator = iter(raw_ids)
        except TypeError as exc:
            raise EngineValidationError(
                "Evidence group identifiers must be iterable.",
                component="context",
                operation="normalize_groups",
                field=f"evidence_groups.{name}",
                cause=exc,
            ) from exc

        ordered: list[str] = []
        seen: set[str] = set()
        for index, raw_id in enumerate(iterator):
            evidence_id = require_engine_text(
                raw_id,
                field=f"evidence_groups.{name}[{index}]",
                error_type=EngineValidationError,
            )
            if evidence_id in seen:
                continue
            if evidence_id not in evidence_ids:
                raise EngineIntegrityError(
                    "Evidence group references an evidence identifier absent from AuditContext.",
                    component="context",
                    operation="normalize_groups",
                    field=f"evidence_groups.{name}",
                    context={"evidence_id": evidence_id},
                )
            seen.add(evidence_id)
            ordered.append(evidence_id)
        normalized[name] = tuple(ordered)

    return MappingProxyType(normalized)


def _normalize_json_mapping(
    value: Mapping[str, Any] | None,
    *,
    field: str,
) -> Mapping[str, Any]:
    """Return an isolated JSON-safe mapping without creating a new schema."""
    announce_engine_action(
        printer,
        logger,
        component="context",
        action=f"Normalizing audit-context {field}",
        event="audit_context_normalize_mapping_start",
        context={"field": field},
    )

    if value is None:
        return MappingProxyType({})
    mapping = require_engine_mapping(
        value,
        field=field,
        error_type=EngineValidationError,
    )
    primitive = to_engine_primitive(mapping, field=field)
    if not isinstance(primitive, dict):
        raise EngineSerializationError(
            "Audit-context mapping did not normalize to a JSON object.",
            component="context",
            operation="normalize_mapping",
            field=field,
            context={"received_type": type(primitive).__name__},
        )
    return MappingProxyType(dict(primitive))


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Immutable normalized evidence view consumed by deterministic audit logic.

    ``evidence_groups`` is a semantic index only: it stores stable evidence IDs,
    not duplicate EvidenceItem objects.  Group names are intentionally not
    hard-coded here because Family Evidence and Project Evidence expose different
    canonical section vocabularies and future product engines may create derived
    views without changing the evidence model.
    """

    product_code: ProductCode | str
    evidence_items: tuple[EvidenceItem, ...] = ()
    evidence_groups: Mapping[str, tuple[str, ...]] = dataclass_field(default_factory=dict)
    project_id: str | None = None
    family_evidence_refs: tuple[str, ...] = ()
    source_manifest: Mapping[str, Any] = dataclass_field(default_factory=dict)
    metadata: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component="context",
            action="Validating audit context",
            event="audit_context_validate_start",
            context={"product_code": str(self.product_code)},
        )

        try:
            product_code = ProductCode.parse(self.product_code)
        except DomainError as exc:
            raise EngineValidationError(
                "AuditContext contains an invalid BIMAP product code.",
                component="context",
                operation="validate",
                field="product_code",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        evidence_items = _normalize_evidence_items(self.evidence_items)
        evidence_ids = {item.evidence_id for item in evidence_items}
        groups = _normalize_group_index(self.evidence_groups, evidence_ids=evidence_ids)

        project_id: str | None
        if self.project_id is None:
            project_id = None
        else:
            project_id = require_engine_text(
                self.project_id,
                field="project_id",
                error_type=EngineValidationError,
            )

        if isinstance(self.family_evidence_refs, (str, bytes, bytearray, Mapping)):
            raise EngineValidationError(
                "family_evidence_refs must be an iterable of identifiers.",
                component="context",
                operation="validate",
                field="family_evidence_refs",
                context={"received_type": type(self.family_evidence_refs).__name__},
            )
        family_refs: list[str] = []
        seen_family_refs: set[str] = set()
        try:
            family_iterator = iter(self.family_evidence_refs)
        except TypeError as exc:
            raise EngineValidationError(
                "family_evidence_refs must be iterable.",
                component="context",
                operation="validate",
                field="family_evidence_refs",
                cause=exc,
            ) from exc
        for index, raw_ref in enumerate(family_iterator):
            ref = require_engine_text(
                raw_ref,
                field=f"family_evidence_refs[{index}]",
                error_type=EngineValidationError,
            )
            if ref not in seen_family_refs:
                seen_family_refs.add(ref)
                family_refs.append(ref)

        source_manifest = _normalize_json_mapping(self.source_manifest, field="source_manifest")
        metadata = _normalize_json_mapping(self.metadata, field="metadata")

        object.__setattr__(self, "product_code", product_code)
        object.__setattr__(self, "evidence_items", evidence_items)
        object.__setattr__(self, "evidence_groups", groups)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "family_evidence_refs", tuple(family_refs))
        object.__setattr__(self, "source_manifest", source_manifest)
        object.__setattr__(self, "metadata", metadata)

        logger.debug(
            {
                "event": "audit_context_validated",
                "product_code": product_code.value,
                "evidence_count": len(evidence_items),
                "source_count": len({item.source_file_id for item in evidence_items}),
                "group_count": len(groups),
                "has_project_id": project_id is not None,
                "family_evidence_ref_count": len(family_refs),
            }
        )

    @property
    def evidence_count(self) -> int:
        """Return the number of normalized evidence records."""
        return len(self.evidence_items)

    @property
    def source_count(self) -> int:
        """Return the number of distinct source-file identities represented."""
        return len({item.source_file_id for item in self.evidence_items})

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """Return evidence identifiers in deterministic context order."""
        return tuple(item.evidence_id for item in self.evidence_items)

    def get_evidence(self, evidence_id: str) -> EvidenceItem | None:
        """Resolve one evidence item by its stable identifier."""
        announce_engine_action(
            printer,
            logger,
            component="context",
            action="Resolving audit-context evidence",
            event="audit_context_get_evidence_start",
        )
        target = require_engine_text(
            evidence_id,
            field="evidence_id",
            error_type=EngineValidationError,
        )
        for item in self.evidence_items:
            if item.evidence_id == target:
                return item
        return None

    def require_evidence(self, evidence_id: str) -> EvidenceItem:
        """Resolve required evidence or fail with an engine integrity error."""
        announce_engine_action(
            printer,
            logger,
            component="context",
            action="Requiring audit-context evidence",
            event="audit_context_require_evidence_start",
        )
        item = self.get_evidence(evidence_id)
        if item is not None:
            return item
        raise EngineIntegrityError(
            "Required evidence identifier is absent from AuditContext.",
            component="context",
            operation="require_evidence",
            field="evidence_id",
            context={"evidence_id": str(evidence_id).strip()},
        )

    def group(self, name: str) -> tuple[EvidenceItem, ...]:
        """Return evidence belonging to a named semantic group."""
        announce_engine_action(
            printer,
            logger,
            component="context",
            action="Resolving audit-context evidence group",
            event="audit_context_group_start",
        )
        normalized = require_engine_text(
            name,
            field="group",
            error_type=EngineValidationError,
        )
        ids = self.evidence_groups.get(normalized)
        if ids is None:
            raise EngineValidationError(
                "Unknown AuditContext evidence group.",
                component="context",
                operation="group",
                field="group",
                context={"group": normalized, "available": tuple(self.evidence_groups)},
            )
        index = {item.evidence_id: item for item in self.evidence_items}
        return tuple(index[evidence_id] for evidence_id in ids)

    def for_source(self, source_file_id: str) -> tuple[EvidenceItem, ...]:
        """Return all normalized evidence originating from one source object."""
        announce_engine_action(
            printer,
            logger,
            component="context",
            action="Resolving evidence by source identity",
            event="audit_context_for_source_start",
        )
        target = require_engine_text(
            source_file_id,
            field="source_file_id",
            error_type=EngineValidationError,
        )
        return tuple(item for item in self.evidence_items if item.source_file_id == target)

    def to_grounded_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe context suitable for downstream grounding.

        This method intentionally contains evidence values because its output is
        the audit data product, not a log message.  Callers must still apply any
        outer data-minimization policy before passing the result to SLAI.
        """
        announce_engine_action(
            printer,
            logger,
            component="context",
            action="Serializing grounded audit context",
            event="audit_context_to_grounded_dict_start",
            context={
                "product_code": (
                    self.product_code.value
                    if isinstance(self.product_code, ProductCode)
                    else self.product_code
                ),
                "evidence_count": self.evidence_count,
                "group_count": len(self.evidence_groups),
            },
        )

        payload = {
            "product_code": (
                self.product_code.value
                if isinstance(self.product_code, ProductCode)
                else self.product_code
            ),
            "project_id": self.project_id,
            "family_evidence_refs": list(self.family_evidence_refs),
            "evidence": [item.to_dict() for item in self.evidence_items],
            "evidence_groups": {
                name: list(ids) for name, ids in self.evidence_groups.items()
            },
            "source_manifest": dict(self.source_manifest),
            "metadata": dict(self.metadata),
        }
        primitive = to_engine_primitive(payload, field="audit_context")
        if not isinstance(primitive, dict):
            raise EngineSerializationError(
                "AuditContext did not serialize to a JSON object.",
                component="context",
                operation="to_grounded_dict",
                field="audit_context",
            )
        return primitive


__all__ = ["AuditContext"]


if __name__ == "__main__":
    from datetime import datetime, timezone
    import hashlib

    from ..domain.evidence.provenance import ContentHash, Provenance, SourceIdentity

    print("\n=== Running Audit Context Self-Test ===\n")
    printer.status("TEST", "Audit context module initialized", "info")

    digest = hashlib.sha256(b"audit-context").hexdigest()
    evidence = EvidenceItem(
        evidence_id="EV-CONTEXT-1",
        provenance=Provenance(
            source=SourceIdentity(source_file_id="SRC-CONTEXT-1", source_type="json"),
            content_hash=ContentHash(value=digest, algorithm="sha256"),
            extracted_at=datetime.now(timezone.utc),
        ),
        extracted_value={"normalized": True},
        confidence=1.0,
    )
    context = AuditContext(
        product_code="family_audit",
        evidence_items=(evidence,),
        evidence_groups={"family_identity": ("EV-CONTEXT-1",)},
    )
    assert context.require_evidence("EV-CONTEXT-1") == evidence
    assert context.group("family_identity") == (evidence,)
    assert context.to_grounded_dict()["product_code"] == "family_audit"
    printer.status("PASS", "AuditContext normalization and lookup", "success")

    print("\n=== Test ran successfully ===\n")
