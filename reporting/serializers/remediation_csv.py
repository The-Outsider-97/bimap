"""
Generate the BIMAP ``remediation.csv`` customer artifact.

Remediation is not modeled as a separate domain object in the current BIMAP
repository. The authoritative remediation, verification, evidence, severity,
confidence, and rule context already exist on ``FindingContract``; this module
therefore projects those fields into a fixed CSV column contract instead of
introducing a duplicate Remediation model.

No implicit priority/owner/effort values are invented. Input order is preserved
so an upstream, evidence-based prioritization step can control action order.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...contracts.finding import FindingContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.findings.models import Finding
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Remediation CSV")
printer = PrettyPrinter()

_COMPONENT = "remediation_csv"
_FIELDNAMES = (
    "finding_id",
    "scope",
    "rule_id",
    "title",
    "category",
    "severity",
    "confidence",
    "status",
    "automation_type",
    "observed_value",
    "expected_value",
    "evidence_refs",
    "remediation",
    "verification_method",
)


class RemediationCSV:
    """Project validated FindingContract records into remediation CSV rows."""

    def __init__(self, *, excel_safe: bool = True) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing remediation CSV serializer",
            event="remediation_csv_init",
            context={"excel_safe": excel_safe},
        )
        self.excel_safe = bool(excel_safe)
        logger.info({"event": "remediation_csv_initialized"})

    @staticmethod
    def _require_contract(value: Any, *, field: str) -> FindingContract:
        if isinstance(value, FindingContract):
            return value

        if isinstance(value, Finding):
            raise RemediationCSVError(
                "A domain Finding does not contain the external remediation fields required by remediation.csv. "
                "Map it to FindingContract first.",
                component=_COMPONENT,
                field=field,
                context={
                    "finding_id": value.finding_id,
                    "received_type": type(value).__name__,
                },
            )

        raise RemediationCSVError(
            "remediation.csv accepts FindingContract records only.",
            component=_COMPONENT,
            field=field,
            context={"received_type": type(value).__name__},
        )

    def _validated_contracts(self, findings: Iterable[FindingContract]) -> tuple[FindingContract, ...]:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating remediation findings",
            event="remediation_csv_validate_start",
        )

        try:
            raw_records = require_record_sequence(
                findings,
                accepted_types=(FindingContract, Finding),
                field="findings",
                allow_empty=True,
            )
            contracts = tuple(
                self._require_contract(item, field=f"findings[{index}]")
                for index, item in enumerate(raw_records)
            )
            ensure_unique_records(
                contracts,
                identifier=lambda item: item.finding_id,
                identifier_name="finding_id",
                component=_COMPONENT,
            )
            return contracts
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise RemediationCSVError(
                "Remediation finding validation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    @staticmethod
    def _row(contract: FindingContract) -> dict[str, Any]:
        data = contract.to_dict()
        return {
            "finding_id": data["finding_id"],
            "scope": data["scope"],
            "rule_id": data["rule_id"],
            "title": data["title"],
            "category": data["category"],
            "severity": data["severity"],
            "confidence": data["confidence"],
            "status": data["status"],
            "automation_type": data["automation_type"],
            "observed_value": data["observed_value"],
            "expected_value": data["expected_value"],
            "evidence_refs": data["evidence_refs"],
            "remediation": data["remediation"],
            "verification_method": data["verification_method"],
        }

    def to_rows(self, findings: Iterable[FindingContract]) -> list[dict[str, Any]]:
        """Return deterministic remediation row mappings in caller-defined order."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building remediation rows",
            event="remediation_csv_rows_start",
        )

        try:
            contracts = self._validated_contracts(findings)
            rows = [self._row(contract) for contract in contracts]
            logger.debug(
                {
                    "event": "remediation_rows_built",
                    "row_count": len(rows),
                }
            )
            return rows
        except ReportingError:
            raise
        except (ContractError, DomainError, KeyError, TypeError, ValueError) as exc:
            raise RemediationCSVError(
                "Unable to build remediation CSV rows.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def serialize(self, finding: FindingContract) -> str:
        """Serialize one finding as a header plus one remediation CSV row."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing remediation row to CSV",
            event="remediation_csv_serialize_one_start",
        )

        try:
            contract = self._require_contract(finding, field="finding")
            return build_csv(
                (self._row(contract),),
                fieldnames=_FIELDNAMES,
                component=_COMPONENT,
                excel_safe=self.excel_safe,
            )
        except ReportingError:
            raise
        except (ContractError, DomainError, KeyError, TypeError, ValueError) as exc:
            raise RemediationCSVError(
                "Remediation CSV serialization failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def generate_csv(self, findings: Iterable[FindingContract]) -> str:
        """Generate the complete ``remediation.csv`` artifact."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Generating remediation.csv",
            event="remediation_csv_generate_start",
        )

        try:
            rows = self.to_rows(findings)
            result = build_csv(
                rows,
                fieldnames=_FIELDNAMES,
                component=_COMPONENT,
                excel_safe=self.excel_safe,
            )
            logger.info(
                {
                    "event": "remediation_csv_generated",
                    "row_count": len(rows),
                }
            )
            return result
        except ReportingError:
            raise
        except (ContractError, DomainError, KeyError, TypeError, ValueError) as exc:
            raise RemediationCSVError(
                "remediation.csv generation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc


__all__ = ["RemediationCSV"]