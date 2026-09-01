"""
Generate BIMAP Requirement-Evidence Matrix reporting data and CSV output.

The Requirement-Evidence Matrix is the core BIM QA artifact. This serializer
consumes the authoritative, versioned ``RequirementContract`` and preserves its
semantics: pass/warn/fail/unknown/not_applicable, automation type, confidence,
evidence references, impact, and recommended action.

It intentionally does not consume findings. A finding can be related to a
requirement elsewhere in the audit model, but it is not a substitute for the
requirement assessment itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..utils.reporting_errors import *
from ..utils.reporting_helpers import *
from ...contracts.requirement import RequirementContract
from ...contracts.utils.contracts_errors import ContractError
from ...domain.utils.domain_errors import DomainError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Requirement Matrix")
printer = PrettyPrinter()

_COMPONENT = "requirement_matrix"
_FIELDNAMES = (
    "schema_version",
    "requirement_id",
    "source_requirement",
    "evidence_refs",
    "assessment",
    "automation_type",
    "confidence",
    "impact",
    "recommended_action",
)


class RequirementMatrix:
    """Serialize versioned RequirementContract rows for BIM QA/Combined reports."""

    def __init__(self, *, excel_safe: bool = True) -> None:
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Initializing requirement matrix serializer",
            event="requirement_matrix_init",
            context={"excel_safe": excel_safe},
        )
        self.excel_safe = bool(excel_safe)
        logger.info({"event": "requirement_matrix_initialized"})

    def validate(self, requirements: Iterable[RequirementContract]) -> tuple[RequirementContract, ...]:
        """Validate matrix record types and requirement identifier uniqueness."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Validating requirement matrix records",
            event="requirement_matrix_validate_start",
        )

        try:
            contracts = require_record_sequence(
                requirements,
                accepted_types=RequirementContract,
                field="requirements",
                allow_empty=True,
            )
            ensure_unique_records(
                contracts,
                identifier=lambda item: item.requirement_id,
                identifier_name="requirement_id",
                component=_COMPONENT,
            )
            logger.debug(
                {
                    "event": "requirement_matrix_validated",
                    "requirement_count": len(contracts),
                }
            )
            return contracts
        except ReportingError:
            raise
        except (ContractError, DomainError, TypeError, ValueError) as exc:
            raise RequirementMatrixError(
                "Requirement matrix validation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    @staticmethod
    def _row(contract: RequirementContract) -> dict[str, Any]:
        data = contract.to_dict()
        return {
            "schema_version": data["schema_version"],
            "requirement_id": data["requirement_id"],
            "source_requirement": data["source_requirement"],
            "evidence_refs": data["evidence_refs"],
            "assessment": data["assessment"],
            "automation_type": data["automation_type"],
            "confidence": data["confidence"],
            "impact": data["impact"],
            "recommended_action": data["recommended_action"],
        }

    def generate_matrix(self, requirements: Iterable[RequirementContract]) -> list[dict[str, Any]]:
        """Return JSON/template-ready Requirement-Evidence Matrix rows."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Generating requirement-evidence matrix",
            event="requirement_matrix_generate_start",
        )

        try:
            contracts = self.validate(requirements)
            rows = [self._row(contract) for contract in contracts]
            logger.info(
                {
                    "event": "requirement_matrix_generated",
                    "requirement_count": len(rows),
                }
            )
            return rows
        except ReportingError:
            raise
        except (ContractError, DomainError, KeyError, TypeError, ValueError) as exc:
            raise RequirementMatrixError(
                "Requirement-Evidence Matrix generation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def serialize(self, requirement: RequirementContract) -> str:
        """Serialize one RequirementContract as a header plus one CSV row."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Serializing requirement matrix row",
            event="requirement_matrix_serialize_one_start",
        )

        try:
            if not isinstance(requirement, RequirementContract):
                raise RequirementMatrixError(
                    "Requirement matrix accepts RequirementContract records only.",
                    component=_COMPONENT,
                    field="requirement",
                    context={"received_type": type(requirement).__name__},
                )
            return build_csv(
                (self._row(requirement),),
                fieldnames=_FIELDNAMES,
                component=_COMPONENT,
                excel_safe=self.excel_safe,
            )
        except ReportingError:
            raise
        except (ContractError, DomainError, KeyError, TypeError, ValueError) as exc:
            raise RequirementMatrixError(
                "Requirement matrix row serialization failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    def generate_csv(self, requirements: Iterable[RequirementContract]) -> str:
        """Generate the complete ``requirement_matrix.csv`` artifact."""
        announce_reporting_action(
            printer,
            logger,
            component=_COMPONENT,
            action="Generating requirement_matrix.csv",
            event="requirement_matrix_csv_start",
        )

        try:
            rows = self.generate_matrix(requirements)
            result = build_csv(
                rows,
                fieldnames=_FIELDNAMES,
                component=_COMPONENT,
                excel_safe=self.excel_safe,
            )
            logger.info(
                {
                    "event": "requirement_matrix_csv_generated",
                    "row_count": len(rows),
                }
            )
            return result
        except ReportingError:
            raise
        except (ContractError, DomainError, KeyError, TypeError, ValueError) as exc:
            raise RequirementMatrixError(
                "requirement_matrix.csv generation failed.",
                component=_COMPONENT,
                cause=exc,
            ) from exc


__all__ = ["RequirementMatrix"]