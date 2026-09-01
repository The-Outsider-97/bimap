"""
Serialize BIMAP finding contracts to ``findings.json``.

``findings.json`` is an external customer/downstream artifact and therefore
serializes the authoritative ``FindingContract`` rather than the smaller
canonical domain ``Finding``. The current domain Finding intentionally lacks
rule_id, scope, automation type, status, observed/expected state, evidence
references, remediation, and verification method; this serializer does not
fabricate those fields.
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


logger = get_logger("BIMAP Findings JSON")
printer = PrettyPrinter()

_COMPONENT = "findings_json"


class FindingJSON:
    """Generate deterministic JSON representations of BIMAP finding contracts."""

    def __init__(self, *, sort_records: bool = True) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing findings JSON serializer",
            event="findings_json_init",
            context={"sort_records": sort_records},
        )
        self.sort_records = bool(sort_records)
        logger.info({"event": "findings_json_initialized"})

    @staticmethod
    def _require_contract(value: Any, *, field: str) -> FindingContract:
        if isinstance(value, FindingContract):
            return value

        if isinstance(value, Finding):
            raise FindingJSONError(
                "A domain Finding cannot be emitted as the external findings.json contract. "
                "Map it to FindingContract after rule/scope/evidence/remediation data is available.",
                component=_COMPONENT,
                field=field,
                context={
                    "finding_id": value.finding_id,
                    "received_type": type(value).__name__,
                },
            )

        raise FindingJSONError(
            "findings.json accepts FindingContract records only.",
            component=_COMPONENT,
            field=field,
            context={"received_type": type(value).__name__},
        )

    def validate_many(self, findings: Iterable[FindingContract]) -> tuple[FindingContract, ...]:
        """Validate finding record types and stable identifier uniqueness."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating finding contracts",
            event="findings_json_validate_start",
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

            if self.sort_records:
                contracts = tuple(sorted(contracts, key=lambda item: item.finding_id))

            logger.debug(
                {
                    "event": "findings_json_validated",
                    "finding_count": len(contracts),
                }
            )
            return contracts
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise FindingJSONError(
                "Finding contract validation failed during reporting.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def to_payload(self, findings: Iterable[FindingContract]) -> list[dict[str, Any]]:
        """Return JSON-ready finding objects without encoding them to text."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Building findings JSON payload",
            event="findings_json_payload_start",
        )

        try:
            contracts = self.validate_many(findings)
            return [item.to_dict() for item in contracts]
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise FindingJSONError(
                "Unable to build findings JSON payload.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def serialize(self, finding: FindingContract, *, pretty: bool = False) -> str:
        """Serialize one external finding contract to canonical JSON text."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing finding to JSON",
            event="findings_json_serialize_one_start",
            context={"pretty": pretty},
        )

        try:
            contract = self._require_contract(finding, field="finding")
            result = canonical_reporting_json(contract.to_dict(), pretty=pretty)
            logger.debug(
                {
                    "event": "finding_json_serialized",
                    "finding_id": contract.finding_id,
                }
            )
            return result
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise FindingJSONError(
                "Finding JSON serialization failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def serialize_many(self, findings: Iterable[FindingContract], *, pretty: bool = True) -> str:
        """
        Serialize the complete ``findings.json`` artifact as a JSON array.

        An array is used intentionally instead of inventing a second document
        schema around already-versioned ``FindingContract`` records.
        """
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing findings.json",
            event="findings_json_serialize_many_start",
            context={"pretty": pretty},
        )

        try:
            payload = self.to_payload(findings)
            result = canonical_reporting_json(payload, pretty=pretty)
            logger.info(
                {
                    "event": "findings_json_generated",
                    "finding_count": len(payload),
                }
            )
            return result
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise FindingJSONError(
                "findings.json generation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc


__all__ = ["FindingJSON"]