"""
Grounded cross-scope evidence/finding graph for BIMAP Combined Audit.

The graph is structural rather than inferential. It records only relationships
already present in accepted BIMAP data:

    FindingContract.evidence_refs -> AuditContext.evidence_items

It does not invent semantic links, infer causality, rerun product auditors,
inspect raw files, or duplicate normalized evidence content. Higher-order
Combined Audit semantics remain the responsibility of an explicit correlation
policy in ``combined/auditor.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...contracts.finding import *
from ...domain.evidence.models import EvidenceItem
from ..context import AuditContext
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore

logger = get_logger("BIMAP Combined Evidence Graph")
printer = PrettyPrinter()
_COMPONENT = "combined_evidence_graph"


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    """Content-free evidence identity/provenance node."""

    evidence_id: str
    source_file_id: str
    source_type: str
    source_hash: str
    hash_algorithm: str

    @classmethod
    def from_evidence(cls, evidence: EvidenceItem) -> "EvidenceNode":
        """Create one graph evidence node from canonical normalized evidence."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating Combined Audit evidence node",
            event="combined_graph_evidence_node_start",
        )
        if not isinstance(evidence, EvidenceItem):
            raise UnsupportedEngineInputError(
                "Evidence graph nodes require canonical EvidenceItem values.",
                component=_COMPONENT,
                operation="from_evidence",
                field="evidence",
                context={"received_type": type(evidence).__name__},
            )
        return cls(
            evidence_id=evidence.evidence_id,
            source_file_id=evidence.source_file_id,
            source_type=evidence.source_type,
            source_hash=evidence.source_hash,
            hash_algorithm=evidence.hash_algorithm,
        )

    def to_dict(self) -> dict[str, str]:
        """Return deterministic graph metadata without extracted evidence values."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing Combined Audit evidence node",
            event="combined_graph_evidence_node_to_dict_start",
            context={"evidence_id": self.evidence_id},
        )
        return {
            "evidence_id": self.evidence_id,
            "source_file_id": self.source_file_id,
            "source_type": self.source_type,
            "source_hash": self.source_hash,
            "hash_algorithm": self.hash_algorithm,
        }


@dataclass(frozen=True, slots=True)
class FindingNode:
    """Graph projection of an already-produced BIMAP finding."""

    finding_id: str
    scope: FindingScope
    rule_id: str
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_finding(cls, finding: FindingContract) -> "FindingNode":
        """Create a structural finding node without duplicating finding policy."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Creating Combined Audit finding node",
            event="combined_graph_finding_node_start",
        )
        if not isinstance(finding, FindingContract):
            raise UnsupportedEngineInputError(
                "Evidence graph finding nodes require FindingContract values.",
                component=_COMPONENT,
                operation="from_finding",
                field="finding",
                context={"received_type": type(finding).__name__},
            )
        return cls(
            finding_id=finding.finding_id,
            scope=FindingScope(finding.scope),
            rule_id=finding.rule_id,
            evidence_refs=tuple(finding.evidence_refs),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic identity/scope metadata for this finding node."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing Combined Audit finding node",
            event="combined_graph_finding_node_to_dict_start",
            context={"finding_id": self.finding_id},
        )
        return {
            "finding_id": self.finding_id,
            "scope": self.scope.value,
            "rule_id": self.rule_id,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, order=True, slots=True)
class FindingEvidenceEdge:
    """Explicit finding-to-evidence reference already present in a finding."""

    finding_id: str
    evidence_id: str

    def to_dict(self) -> dict[str, str]:
        """Return deterministic edge metadata."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing Combined Audit evidence edge",
            event="combined_graph_edge_to_dict_start",
            context={"finding_id": self.finding_id, "evidence_id": self.evidence_id},
        )
        return {"finding_id": self.finding_id, "evidence_id": self.evidence_id}


@dataclass(frozen=True, slots=True)
class EvidenceGraphResult:
    """Immutable structural graph used by Combined Audit correlation policy."""

    evidence_nodes: Mapping[str, EvidenceNode]
    finding_nodes: Mapping[str, FindingNode]
    edges: tuple[FindingEvidenceEdge, ...]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating Combined Audit evidence graph result",
            event="combined_graph_result_validate_start",
        )
        evidence_nodes = dict(self.evidence_nodes)
        finding_nodes = dict(self.finding_nodes)
        edges = tuple(self.edges)

        for key, node in evidence_nodes.items():
            if not isinstance(node, EvidenceNode):
                raise EngineIntegrityError(
                    "Evidence graph contains a non-EvidenceNode value.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="evidence_nodes",
                    context={"key": str(key), "received_type": type(node).__name__},
                )
            if key != node.evidence_id:
                raise EngineIntegrityError(
                    "Evidence graph index key does not match evidence node identity.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="evidence_nodes",
                    context={"key": str(key), "evidence_id": node.evidence_id},
                )

        for key, node in finding_nodes.items():
            if not isinstance(node, FindingNode):
                raise EngineIntegrityError(
                    "Evidence graph contains a non-FindingNode value.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="finding_nodes",
                    context={"key": str(key), "received_type": type(node).__name__},
                )
            if key != node.finding_id:
                raise EngineIntegrityError(
                    "Evidence graph index key does not match finding node identity.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="finding_nodes",
                    context={"key": str(key), "finding_id": node.finding_id},
                )

        seen_edges: set[tuple[str, str]] = set()
        for edge in edges:
            if not isinstance(edge, FindingEvidenceEdge):
                raise EngineIntegrityError(
                    "Evidence graph contains an unsupported edge value.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="edges",
                    context={"received_type": type(edge).__name__},
                )
            signature = (edge.finding_id, edge.evidence_id)
            if signature in seen_edges:
                raise EngineIntegrityError(
                    "Evidence graph contains duplicate finding-evidence edges.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="edges",
                    context={"finding_id": edge.finding_id, "evidence_id": edge.evidence_id},
                )
            seen_edges.add(signature)
            finding = finding_nodes.get(edge.finding_id)
            evidence = evidence_nodes.get(edge.evidence_id)
            if finding is None or evidence is None:
                raise EngineIntegrityError(
                    "Evidence graph edge references a missing graph node.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="edges",
                    context={
                        "finding_id": edge.finding_id,
                        "evidence_id": edge.evidence_id,
                        "has_finding": finding is not None,
                        "has_evidence": evidence is not None,
                    },
                )
            if edge.evidence_id not in finding.evidence_refs:
                raise EngineIntegrityError(
                    "Evidence graph edge is not grounded in its FindingContract reference set.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field="edges",
                    context={"finding_id": edge.finding_id, "evidence_id": edge.evidence_id},
                )

        object.__setattr__(self, "evidence_nodes", MappingProxyType(evidence_nodes))
        object.__setattr__(self, "finding_nodes", MappingProxyType(finding_nodes))
        object.__setattr__(self, "edges", tuple(sorted(edges)))

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_nodes)

    @property
    def finding_count(self) -> int:
        return len(self.finding_nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def evidence_for_finding(self, finding_id: str) -> tuple[EvidenceNode, ...]:
        """Return evidence nodes explicitly referenced by one finding."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving evidence for Combined Audit finding",
            event="combined_graph_evidence_for_finding_start",
        )
        target = require_engine_text(finding_id, field="finding_id", error_type=EngineValidationError)
        node = self.finding_nodes.get(target)
        if node is None:
            raise EngineValidationError(
                "Unknown finding identifier in Combined Audit evidence graph.",
                component=_COMPONENT,
                operation="evidence_for_finding",
                field="finding_id",
                context={"finding_id": target},
            )
        return tuple(self.evidence_nodes[item] for item in node.evidence_refs)

    def findings_for_evidence(self, evidence_id: str) -> tuple[FindingNode, ...]:
        """Return findings that explicitly cite one evidence identifier."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving findings for Combined Audit evidence",
            event="combined_graph_findings_for_evidence_start",
        )
        target = require_engine_text(evidence_id, field="evidence_id", error_type=EngineValidationError)
        if target not in self.evidence_nodes:
            raise EngineValidationError(
                "Unknown evidence identifier in Combined Audit evidence graph.",
                component=_COMPONENT,
                operation="findings_for_evidence",
                field="evidence_id",
                context={"evidence_id": target},
            )
        return tuple(
            self.finding_nodes[edge.finding_id]
            for edge in self.edges
            if edge.evidence_id == target
        )

    def shared_evidence(
        self,
        left_finding_id: str,
        right_finding_id: str,
    ) -> tuple[EvidenceNode, ...]:
        """Return evidence explicitly cited by both named findings."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving shared Combined Audit evidence",
            event="combined_graph_shared_evidence_start",
        )
        left = self.evidence_for_finding(left_finding_id)
        right_ids = {node.evidence_id for node in self.evidence_for_finding(right_finding_id)}
        return tuple(node for node in left if node.evidence_id in right_ids)

    def source_scopes_for_evidence_refs(
        self,
        evidence_refs: Iterable[str],
    ) -> frozenset[FindingScope]:
        """Return source-finding scopes that cite any supplied evidence."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving source scopes for Combined Audit evidence",
            event="combined_graph_source_scopes_start",
        )
        if isinstance(evidence_refs, (str, bytes, bytearray, Mapping)):
            raise EngineValidationError(
                "evidence_refs must be an iterable of evidence identifiers.",
                component=_COMPONENT,
                operation="source_scopes_for_evidence_refs",
                field="evidence_refs",
                context={"received_type": type(evidence_refs).__name__},
            )
        try:
            refs = tuple(
                require_engine_text(value, field="evidence_refs", error_type=EngineValidationError)
                for value in evidence_refs
            )
        except TypeError as exc:
            raise EngineValidationError(
                "evidence_refs must be iterable.",
                component=_COMPONENT,
                operation="source_scopes_for_evidence_refs",
                field="evidence_refs",
                cause=exc,
            ) from exc

        scopes: set[FindingScope] = set()
        for evidence_id in refs:
            if evidence_id not in self.evidence_nodes:
                raise EngineIntegrityError(
                    "Combined Audit correlation references evidence absent from the graph.",
                    component=_COMPONENT,
                    operation="source_scopes_for_evidence_refs",
                    field="evidence_refs",
                    context={"evidence_id": evidence_id},
                )
            scopes.update(node.scope for node in self.findings_for_evidence(evidence_id))
        return frozenset(scopes)

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic, content-free graph data for traceability."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing Combined Audit evidence graph",
            event="combined_graph_to_dict_start",
            context={
                "evidence_count": self.evidence_count,
                "finding_count": self.finding_count,
                "edge_count": self.edge_count,
            },
        )
        payload = {
            "evidence_nodes": [self.evidence_nodes[key].to_dict() for key in sorted(self.evidence_nodes)],
            "finding_nodes": [self.finding_nodes[key].to_dict() for key in sorted(self.finding_nodes)],
            "edges": [edge.to_dict() for edge in self.edges],
        }
        primitive = to_engine_primitive(payload, field="combined_evidence_graph")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "Combined Audit evidence graph did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="combined_evidence_graph",
            )
        return primitive


class EvidenceGraph:
    """Build grounded finding-to-evidence relationships for Combined Audit."""

    def __init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing Combined Audit evidence graph builder",
            event="combined_graph_init_start",
        )
        logger.info({"event": "combined_graph_initialized"})

    def build(
        self,
        context: AuditContext,
        findings: Iterable[FindingContract],
    ) -> EvidenceGraphResult:
        """Build an immutable graph from normalized evidence and finished findings."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building Combined Audit evidence graph",
            event="combined_graph_build_start",
        )
        if not isinstance(context, AuditContext):
            raise UnsupportedEngineInputError(
                "EvidenceGraph.build requires an AuditContext.",
                component=_COMPONENT,
                operation="build",
                field="context",
                context={"received_type": type(context).__name__},
            )
        if isinstance(findings, (str, bytes, bytearray, Mapping)):
            raise UnsupportedEngineInputError(
                "findings must be an iterable of FindingContract values.",
                component=_COMPONENT,
                operation="build",
                field="findings",
                context={"received_type": type(findings).__name__},
            )
        try:
            source_findings = tuple(findings)
        except TypeError as exc:
            raise UnsupportedEngineInputError(
                "findings must be iterable.",
                component=_COMPONENT,
                operation="build",
                field="findings",
                context={"received_type": type(findings).__name__},
                cause=exc,
            ) from exc

        evidence_nodes = {
            item.evidence_id: EvidenceNode.from_evidence(item)
            for item in context.evidence_items
        }
        finding_nodes: dict[str, FindingNode] = {}
        edges: list[FindingEvidenceEdge] = []

        for index, finding in enumerate(source_findings):
            if not isinstance(finding, FindingContract):
                raise UnsupportedEngineInputError(
                    "EvidenceGraph accepts completed FindingContract values only.",
                    component=_COMPONENT,
                    operation="build",
                    field=f"findings[{index}]",
                    context={"received_type": type(finding).__name__},
                )
            if finding.finding_id in finding_nodes:
                raise EngineIntegrityError(
                    "Combined Audit evidence graph received duplicate finding identifiers.",
                    component=_COMPONENT,
                    operation="build",
                    field="finding_id",
                    context={"finding_id": finding.finding_id},
                )
            unresolved = tuple(
                evidence_id for evidence_id in finding.evidence_refs
                if evidence_id not in evidence_nodes
            )
            if unresolved:
                raise EngineIntegrityError(
                    "A completed finding references evidence absent from the Combined Audit context.",
                    component=_COMPONENT,
                    operation="build",
                    field="evidence_refs",
                    context={
                        "finding_id": finding.finding_id,
                        "unresolved_evidence_refs": unresolved,
                    },
                )
            node = FindingNode.from_finding(finding)
            finding_nodes[node.finding_id] = node
            edges.extend(
                FindingEvidenceEdge(finding_id=node.finding_id, evidence_id=evidence_id)
                for evidence_id in node.evidence_refs
            )

        result = EvidenceGraphResult(
            evidence_nodes=evidence_nodes,
            finding_nodes=finding_nodes,
            edges=tuple(edges),
        )
        logger.info(
            {
                "event": "combined_graph_built",
                "evidence_count": result.evidence_count,
                "finding_count": result.finding_count,
                "edge_count": result.edge_count,
            }
        )
        return result


__all__ = [
    "EvidenceNode",
    "FindingNode",
    "FindingEvidenceEdge",
    "EvidenceGraphResult",
    "EvidenceGraph",
]


if __name__ == "__main__":
    print("\n=== Running Combined Evidence Graph Self-Test ===\n")
    printer.status("TEST", "Combined evidence graph module initialized", "info")
    graph_builder = EvidenceGraph()
    empty = graph_builder.build(AuditContext(product_code="combined_audit"), ())
    assert empty.evidence_count == 0
    assert empty.finding_count == 0
    assert empty.edge_count == 0
    printer.status("PASS", "Empty Combined Audit graph", "success")
    print("\n=== Test ran successfully ===\n")