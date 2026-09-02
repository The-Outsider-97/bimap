"""
Grounding validation for BIMAP external finding contracts.

The validation layer consumes the authoritative ``FindingContract`` rather than
``domain.findings.Finding``.  This is intentional: the current domain Finding is
a narrower lifecycle/value representation and does not carry the external
contract's rule ID, automation type, assessment status, observed/expected state,
or evidence-ID references.  Reconstructing those fields from the domain object
would fabricate semantics.

Validation therefore focuses on facts that are already represented explicitly:

* stable finding identity uniqueness;
* evidence references resolving to the accepted ``AuditContext``;
* optional deterministic alignment with already-produced ``RuleResult`` values;
* non-scoring counts and reverse indices useful for later coverage/reporting.

When deterministic rule results are supplied, a deterministic finding must match
its rule result's status and observed/expected state, and may only cite evidence
already cited by that result.  This matches the grounding policy already applied
by the current RFA/BIM-QA auditors while centralizing it for reuse.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...contracts.finding import FindingContract
from ...contracts.requirement import AutomationType
from ..utils.engine_errors import *
from ..utils.engine_helpers import *
from ..rules.base import RuleResult
from ..context import AuditContext
from .evidence import EvidenceValidation
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Findings Validation")
printer = PrettyPrinter()

_COMPONENT = "validation_findings"


def _status_value(value: Any) -> str:
    """Return canonical enum/string status text without defining aliases."""
    return str(getattr(value, "value", value))


def _normalize_findings(
    findings: Iterable[FindingContract],
) -> tuple[FindingContract, ...]:
    """Normalize finding collection and enforce unique stable identities."""
    if isinstance(findings, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            "findings must be an iterable of FindingContract values.",
            component=_COMPONENT,
            operation="normalize_findings",
            field="findings",
            context={"received_type": type(findings).__name__},
        )
    try:
        items = tuple(findings)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            "findings must be iterable.",
            component=_COMPONENT,
            operation="normalize_findings",
            field="findings",
            context={"received_type": type(findings).__name__},
            cause=exc,
        ) from exc

    seen_ids: set[str] = set()
    for index, finding in enumerate(items):
        if not isinstance(finding, FindingContract):
            raise UnsupportedEngineInputError(
                "Findings validation accepts FindingContract values only.",
                component=_COMPONENT,
                operation="normalize_findings",
                field=f"findings[{index}]",
                context={"received_type": type(finding).__name__},
            )
        if finding.finding_id in seen_ids:
            raise EngineIntegrityError(
                "Finding collection contains duplicate finding identifiers.",
                component=_COMPONENT,
                operation="normalize_findings",
                field="finding_id",
                context={"finding_id": finding.finding_id},
            )
        seen_ids.add(finding.finding_id)
    return items


def _normalize_rule_results(
    rule_results: Iterable[RuleResult],
) -> Mapping[str, RuleResult]:
    """Index deterministic rule results by unambiguous rule identity."""
    if isinstance(rule_results, (str, bytes, bytearray, Mapping)):
        raise UnsupportedEngineInputError(
            "rule_results must be an iterable of RuleResult values.",
            component=_COMPONENT,
            operation="normalize_rule_results",
            field="rule_results",
            context={"received_type": type(rule_results).__name__},
        )
    try:
        items = tuple(rule_results)
    except TypeError as exc:
        raise UnsupportedEngineInputError(
            "rule_results must be iterable.",
            component=_COMPONENT,
            operation="normalize_rule_results",
            field="rule_results",
            context={"received_type": type(rule_results).__name__},
            cause=exc,
        ) from exc

    indexed: dict[str, RuleResult] = {}
    for index, result in enumerate(items):
        if not isinstance(result, RuleResult):
            raise UnsupportedEngineInputError(
                "rule_results accepts RuleResult values only.",
                component=_COMPONENT,
                operation="normalize_rule_results",
                field=f"rule_results[{index}]",
                context={"received_type": type(result).__name__},
            )
        if result.rule_id in indexed:
            # FindingContract carries rule_id but not rule_version. Two results
            # for one rule ID would therefore be ambiguous for grounding here.
            raise EngineIntegrityError(
                "Finding grounding received multiple RuleResult revisions for one rule ID.",
                component=_COMPONENT,
                operation="normalize_rule_results",
                field="rule_results",
                context={"rule_id": result.rule_id},
            )
        indexed[result.rule_id] = result
    return MappingProxyType(indexed)


@dataclass(frozen=True, slots=True)
class FindingsValidationSummary:
    """Non-scoring structural summary of one validated finding collection."""

    finding_count: int
    unique_rule_count: int
    findings_with_evidence_count: int
    referenced_evidence_count: int
    evidence_link_count: int
    scope_counts: Mapping[str, int]
    status_counts: Mapping[str, int]
    automation_counts: Mapping[str, int]
    severity_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating findings validation summary",
            event="findings_validation_summary_validate_start",
        )
        for field in (
            "finding_count",
            "unique_rule_count",
            "findings_with_evidence_count",
            "referenced_evidence_count",
            "evidence_link_count",
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise EngineIntegrityError(
                    "Findings validation summary contains an invalid count.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={"received_type": type(value).__name__},
                )
        if self.findings_with_evidence_count > self.finding_count:
            raise EngineIntegrityError(
                "Findings-with-evidence count exceeds total finding count.",
                component=_COMPONENT,
                operation="validate_summary",
                field="findings_with_evidence_count",
            )
        if self.unique_rule_count > self.finding_count:
            raise EngineIntegrityError(
                "Unique rule count exceeds total finding count.",
                component=_COMPONENT,
                operation="validate_summary",
                field="unique_rule_count",
            )

        for field in (
            "scope_counts",
            "status_counts",
            "automation_counts",
            "severity_counts",
        ):
            mapping = getattr(self, field)
            if not isinstance(mapping, Mapping):
                raise EngineIntegrityError(
                    "Findings validation summary count tables must be mappings.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={"received_type": type(mapping).__name__},
                )
            normalized_mapping = dict(mapping)
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in normalized_mapping.values()
            ):
                raise EngineIntegrityError(
                    "Findings validation summary contains an invalid table count.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                )
            if sum(normalized_mapping.values()) != self.finding_count:
                raise EngineIntegrityError(
                    "Findings validation summary table does not partition the finding collection.",
                    component=_COMPONENT,
                    operation="validate_summary",
                    field=field,
                    context={
                        "table_count": sum(normalized_mapping.values()),
                        "finding_count": self.finding_count,
                    },
                )
            object.__setattr__(self, field, MappingProxyType(normalized_mapping))

    @property
    def evidence_reference_rate(self) -> float | None:
        """Return finding proportion carrying evidence references.

        This is descriptive only. Non-deterministic/manual findings are not
        automatically invalid merely because the current external contract allows
        them to be evidence-less.
        """
        if self.finding_count == 0:
            return None
        return self.findings_with_evidence_count / self.finding_count

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic primitive summary data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing findings validation summary",
            event="findings_validation_summary_to_dict_start",
        )
        return {
            "finding_count": self.finding_count,
            "unique_rule_count": self.unique_rule_count,
            "findings_with_evidence_count": self.findings_with_evidence_count,
            "referenced_evidence_count": self.referenced_evidence_count,
            "evidence_link_count": self.evidence_link_count,
            "evidence_reference_rate": self.evidence_reference_rate,
            "scope_counts": dict(self.scope_counts),
            "status_counts": dict(self.status_counts),
            "automation_counts": dict(self.automation_counts),
            "severity_counts": dict(self.severity_counts),
        }


@dataclass(frozen=True, slots=True)
class FindingsValidationResult:
    """Immutable validated findings plus traceability indices."""

    findings: tuple[FindingContract, ...]
    summary: FindingsValidationSummary
    evidence_to_findings: Mapping[str, tuple[str, ...]]
    rule_to_findings: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating findings validation result",
            event="findings_validation_result_validate_start",
        )
        if not isinstance(self.summary, FindingsValidationSummary):
            raise EngineIntegrityError(
                "FindingsValidationResult requires FindingsValidationSummary.",
                component=_COMPONENT,
                operation="validate_result",
                field="summary",
                context={"received_type": type(self.summary).__name__},
            )
        if len(self.findings) != self.summary.finding_count:
            raise EngineIntegrityError(
                "Validated findings count does not match validation summary.",
                component=_COMPONENT,
                operation="validate_result",
                field="findings",
            )
        known_finding_ids = {finding.finding_id for finding in self.findings}
        normalized_indices: dict[str, Mapping[str, tuple[str, ...]]] = {}
        for field in ("evidence_to_findings", "rule_to_findings"):
            mapping = getattr(self, field)
            if not isinstance(mapping, Mapping):
                raise EngineIntegrityError(
                    "Finding traceability indices must be mappings.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=field,
                    context={"received_type": type(mapping).__name__},
                )
            normalized_mapping = {
                str(key): tuple(values) for key, values in mapping.items()
            }
            unresolved = tuple(
                finding_id
                for finding_ids in normalized_mapping.values()
                for finding_id in finding_ids
                if finding_id not in known_finding_ids
            )
            if unresolved:
                raise EngineIntegrityError(
                    "Finding traceability index references an unknown finding ID.",
                    component=_COMPONENT,
                    operation="validate_result",
                    field=field,
                    context={"unresolved_finding_ids": tuple(dict.fromkeys(unresolved))},
                )
            normalized_indices[field] = MappingProxyType(normalized_mapping)
            object.__setattr__(self, field, normalized_indices[field])

        evidence_index = normalized_indices["evidence_to_findings"]
        rule_index = normalized_indices["rule_to_findings"]
        if len(evidence_index) != self.summary.referenced_evidence_count:
            raise EngineIntegrityError(
                "Evidence-to-finding index count does not match validation summary.",
                component=_COMPONENT,
                operation="validate_result",
                field="evidence_to_findings",
            )
        if sum(len(ids) for ids in evidence_index.values()) != self.summary.evidence_link_count:
            raise EngineIntegrityError(
                "Evidence-to-finding link count does not match validation summary.",
                component=_COMPONENT,
                operation="validate_result",
                field="evidence_to_findings",
            )
        if len(rule_index) != self.summary.unique_rule_count:
            raise EngineIntegrityError(
                "Rule-to-finding index count does not match validation summary.",
                component=_COMPONENT,
                operation="validate_result",
                field="rule_to_findings",
            )

    def get(self, finding_id: str) -> FindingContract | None:
        """Resolve one validated finding by stable identifier."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving validated finding",
            event="findings_validation_result_get_start",
        )
        target = require_engine_text(
            finding_id,
            field="finding_id",
            error_type=EngineValidationError,
        )
        for finding in self.findings:
            if finding.finding_id == target:
                return finding
        return None

    def for_evidence(self, evidence_id: str) -> tuple[FindingContract, ...]:
        """Return validated findings that cite one evidence identifier."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Resolving findings by evidence reference",
            event="findings_validation_result_for_evidence_start",
        )
        target = require_engine_text(
            evidence_id,
            field="evidence_id",
            error_type=EngineValidationError,
        )
        finding_ids = self.evidence_to_findings.get(target, ())
        if not finding_ids:
            return ()
        index = {finding.finding_id: finding for finding in self.findings}
        return tuple(index[finding_id] for finding_id in finding_ids)

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic JSON-safe finding-validation data."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing findings validation result",
            event="findings_validation_result_to_dict_start",
            context={"finding_count": len(self.findings)},
        )
        payload = {
            "findings": [finding.to_dict() for finding in self.findings],
            "summary": self.summary.to_dict(),
            "evidence_to_findings": {
                evidence_id: list(finding_ids)
                for evidence_id, finding_ids in self.evidence_to_findings.items()
            },
            "rule_to_findings": {
                rule_id: list(finding_ids)
                for rule_id, finding_ids in self.rule_to_findings.items()
            },
        }
        primitive = to_engine_primitive(payload, field="findings_validation_result")
        if not isinstance(primitive, dict):
            raise EngineIntegrityError(
                "Findings validation result did not serialize to a JSON object.",
                component=_COMPONENT,
                operation="to_dict",
                field="findings_validation_result",
            )
        return primitive


class FindingsValidation:
    """Validate finding identity, grounding, and optional rule-result alignment."""

    def __init__(self, *, evidence_validation: EvidenceValidation | None = None) -> None:
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing findings validation",
            event="findings_validation_init_start",
        )
        if evidence_validation is not None and not isinstance(
            evidence_validation,
            EvidenceValidation,
        ):
            raise EngineConfigurationError(
                "evidence_validation must be EvidenceValidation or None.",
                component=_COMPONENT,
                operation="initialize",
                field="evidence_validation",
                context={"received_type": type(evidence_validation).__name__},
            )
        self._evidence_validation = evidence_validation or EvidenceValidation()
        logger.debug({"event": "findings_validation_initialized"})

    @property
    def evidence_validation(self) -> EvidenceValidation:
        """Return the shared evidence-reference validator."""
        return self._evidence_validation

    def _validate_rule_alignment(
        self,
        finding: FindingContract,
        result: RuleResult,
    ) -> None:
        """Require one deterministic finding to remain grounded in RuleResult."""
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating deterministic finding rule alignment",
            event="findings_validation_rule_alignment_start",
            context={"finding_id": finding.finding_id, "rule_id": finding.rule_id},
        )
        if finding.rule_id != result.rule_id:
            raise EngineIntegrityError(
                "Deterministic finding rule ID does not match its RuleResult.",
                component=_COMPONENT,
                operation="validate_rule_alignment",
                field="rule_id",
                context={
                    "finding_id": finding.finding_id,
                    "expected_rule_id": result.rule_id,
                    "received_rule_id": finding.rule_id,
                },
            )
        if _status_value(finding.status) != _status_value(result.status):
            raise EngineIntegrityError(
                "Deterministic finding status does not match its RuleResult.",
                component=_COMPONENT,
                operation="validate_rule_alignment",
                field="status",
                context={
                    "finding_id": finding.finding_id,
                    "rule_id": finding.rule_id,
                    "expected_status": _status_value(result.status),
                    "received_status": _status_value(finding.status),
                },
            )

        expected_observed = to_engine_primitive(
            result.observed_value,
            field="rule_result.observed_value",
        )
        expected_expected = to_engine_primitive(
            result.expected_value,
            field="rule_result.expected_value",
        )
        if finding.observed_value != expected_observed:
            raise EngineIntegrityError(
                "Deterministic finding observed_value does not match its RuleResult.",
                component=_COMPONENT,
                operation="validate_rule_alignment",
                field="observed_value",
                context={"finding_id": finding.finding_id, "rule_id": finding.rule_id},
            )
        if finding.expected_value != expected_expected:
            raise EngineIntegrityError(
                "Deterministic finding expected_value does not match its RuleResult.",
                component=_COMPONENT,
                operation="validate_rule_alignment",
                field="expected_value",
                context={"finding_id": finding.finding_id, "rule_id": finding.rule_id},
            )

        rule_refs = set(result.evidence_refs)
        outside_rule_result = tuple(
            evidence_id
            for evidence_id in finding.evidence_refs
            if evidence_id not in rule_refs
        )
        if outside_rule_result:
            raise EngineIntegrityError(
                "Deterministic finding cites evidence not cited by its RuleResult.",
                component=_COMPONENT,
                operation="validate_rule_alignment",
                field="evidence_refs",
                context={
                    "finding_id": finding.finding_id,
                    "rule_id": finding.rule_id,
                    "unresolved_evidence_refs": outside_rule_result,
                },
            )

    def validate(
        self,
        findings: Iterable[FindingContract],
        *,
        context: AuditContext,
        rule_results: Iterable[RuleResult] | None = None,
    ) -> FindingsValidationResult:
        """Validate a complete finding collection against accepted evidence.

        ``rule_results`` is optional because inferred/manual findings need not be
        produced by the deterministic rules executor.  If supplied, every
        deterministic finding must resolve to exactly one RuleResult and satisfy
        the repository's existing deterministic grounding policy.
        """
        announce_engine_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating BIMAP findings",
            event="findings_validation_validate_start",
        )
        if not isinstance(context, AuditContext):
            raise UnsupportedEngineInputError(
                "Findings validation requires an AuditContext.",
                component=_COMPONENT,
                operation="validate",
                field="context",
                context={"received_type": type(context).__name__},
            )

        items = _normalize_findings(findings)
        rule_index = (
            None if rule_results is None else _normalize_rule_results(rule_results)
        )

        evidence_to_findings_mutable: dict[str, list[str]] = {}
        rule_to_findings_mutable: dict[str, list[str]] = {}
        referenced_evidence: set[str] = set()
        evidence_link_count = 0
        findings_with_evidence_count = 0

        for finding in items:
            resolved_refs = self._evidence_validation.resolve_references(
                finding.evidence_refs,
                context=context,
                field=f"findings.{finding.finding_id}.evidence_refs",
                allow_empty=True,
            )
            if resolved_refs:
                findings_with_evidence_count += 1
            for evidence_id in resolved_refs:
                evidence_link_count += 1
                referenced_evidence.add(evidence_id)
                evidence_to_findings_mutable.setdefault(evidence_id, []).append(
                    finding.finding_id
                )
            rule_to_findings_mutable.setdefault(finding.rule_id, []).append(
                finding.finding_id
            )

            if (
                rule_index is not None
                and finding.automation_type is AutomationType.DETERMINISTIC
            ):
                result = rule_index.get(finding.rule_id)
                if result is None:
                    raise EngineIntegrityError(
                        "Deterministic finding has no matching RuleResult.",
                        component=_COMPONENT,
                        operation="validate",
                        field="rule_id",
                        context={
                            "finding_id": finding.finding_id,
                            "rule_id": finding.rule_id,
                        },
                    )
                self._validate_rule_alignment(finding, result)

        scope_counts = Counter(_status_value(finding.scope) for finding in items)
        status_counts = Counter(_status_value(finding.status) for finding in items)
        automation_counts = Counter(
            _status_value(finding.automation_type) for finding in items
        )
        severity_counts = Counter(str(finding.severity) for finding in items)

        summary = FindingsValidationSummary(
            finding_count=len(items),
            unique_rule_count=len(rule_to_findings_mutable),
            findings_with_evidence_count=findings_with_evidence_count,
            referenced_evidence_count=len(referenced_evidence),
            evidence_link_count=evidence_link_count,
            scope_counts=MappingProxyType(dict(sorted(scope_counts.items()))),
            status_counts=MappingProxyType(dict(sorted(status_counts.items()))),
            automation_counts=MappingProxyType(dict(sorted(automation_counts.items()))),
            severity_counts=MappingProxyType(dict(sorted(severity_counts.items()))),
        )
        result = FindingsValidationResult(
            findings=items,
            summary=summary,
            evidence_to_findings=MappingProxyType(
                {
                    evidence_id: tuple(finding_ids)
                    for evidence_id, finding_ids in evidence_to_findings_mutable.items()
                }
            ),
            rule_to_findings=MappingProxyType(
                {
                    rule_id: tuple(finding_ids)
                    for rule_id, finding_ids in rule_to_findings_mutable.items()
                }
            ),
        )
        logger.info(
            {
                "event": "bimap_findings_validated",
                "finding_count": summary.finding_count,
                "unique_rule_count": summary.unique_rule_count,
                "referenced_evidence_count": summary.referenced_evidence_count,
            }
        )
        return result


# Backward-compatible name retained from the initial scaffold. New code should
# prefer the semantically explicit ``FindingsValidation``.
CoverageFindings = FindingsValidation


__all__ = [
    "FindingsValidationSummary",
    "FindingsValidationResult",
    "FindingsValidation",
    "CoverageFindings",
]


if __name__ == "__main__":
    from ...domain.products.models import ProductCode

    print("\n=== Running Findings Validation Self-Test ===\n")
    printer.status("TEST", "Findings validation module initialized", "info")

    context = AuditContext(product_code=ProductCode.FAMILY_AUDIT)
    result = FindingsValidation().validate((), context=context)
    assert result.summary.finding_count == 0
    assert result.summary.evidence_reference_rate is None
    printer.status("PASS", "Empty finding collection validation", "success")

    print("\n=== Test ran successfully ===\n")