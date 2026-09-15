"""
ReportLab renderer for the human-readable BIMAP audit report.

This module is intentionally presentation-only. It consumes the validated,
JSON-ready render context assembled by ``reporting.report_builder.ReportBuilder``
and produces ``R3D_Audit_Report.pdf`` bytes.

It does not execute audit rules, reinterpret finding/evidence semantics,
evaluate governance policy, persist artifacts, or regenerate machine-readable
reporting artifacts.

The class is structurally compatible with ``ReportRenderer`` without importing
``reporting.report_builder``. That keeps dependency direction one-way and avoids
an unnecessary infra -> reporting orchestration cycle.
"""

from __future__ import annotations

import json

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors  # type: ignore
from reportlab.lib.enums import TA_LEFT  # type: ignore
from reportlab.lib.pagesizes import A4  # type: ignore
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
from reportlab.lib.units import mm  # type: ignore
from reportlab.platypus import (  # type: ignore
    Flowable,
    HRFlowable,
    Image as ReportLabImage,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..reporting.utils.reporting_errors import ReportTemplateError, ReportingValidationError
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Audit Report PDF Renderer")
printer = PrettyPrinter()

_COMPONENT = "reportlab_audit_report_renderer"
_TITLE = "R3D BIMAP Audit Report"
_SUBTITLE = "Building Information Model Audit Platform"

_PAGE_WIDTH, _PAGE_HEIGHT = A4
_TITLE_FONT_SIZE = 22.0
_BODY_FONT_SIZE = 8.5
_SMALL_FONT_SIZE = 7.4
_MAX_DISPLAY_CHARS = 4_000

_BORDER = colors.HexColor("#D9D9D9")
_SOFT = colors.HexColor("#F4F4F4")
_MUTED = colors.HexColor("#666666")
_TEXT = colors.HexColor("#222222")

_LOGO_PATH = (
    Path(__file__).resolve().parents[1]
    / "reporting"
    / "templates"
    / "bimap-logo.png"
)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray, memoryview),
    )


def _as_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReportingValidationError(
            f"{field} must be a mapping.",
            component=_COMPONENT,
            field=field,
            context={"received_type": type(value).__name__},
        )
    return value


def _mapping_sequence(value: Any, *, field: str) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if not _is_sequence(value):
        raise ReportingValidationError(
            f"{field} must be a sequence of mappings.",
            component=_COMPONENT,
            field=field,
            context={"received_type": type(value).__name__},
        )

    normalized: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ReportingValidationError(
                f"{field} contains a non-mapping value.",
                component=_COMPONENT,
                field=f"{field}[{index}]",
                context={"received_type": type(item).__name__},
            )
        normalized.append(item)
    return tuple(normalized)


def _text(value: Any) -> str:
    if value is None:
        return "—"

    if isinstance(value, bool):
        return "Yes" if value else "No"

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, float):
        if value != value:  # NaN
            return "—"
        if value in (float("inf"), float("-inf")):
            return "—"

    if isinstance(value, Mapping) or _is_sequence(value):
        try:
            rendered = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(", ", ": "),
                default=str,
            )
        except (TypeError, ValueError):
            rendered = str(value)
    else:
        rendered = str(value)

    if len(rendered) > _MAX_DISPLAY_CHARS:
        omitted = len(rendered) - _MAX_DISPLAY_CHARS
        rendered = (
            f"{rendered[:_MAX_DISPLAY_CHARS]} "
            f"[… {omitted:,} additional character(s) available in the "
            "machine-readable artifact]"
        )
    return rendered


def _safe_text(value: Any) -> str:
    return escape(_text(value)).replace("\n", "<br/>")


def _humanize(value: Any) -> str:
    rendered = _text(value)
    if rendered == "—":
        return rendered
    return rendered.replace("_", " ").replace("-", " ").strip().title()


def _identifier_list(value: Any) -> str:
    if value is None:
        return "—"
    if _is_sequence(value):
        items = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(items) if items else "—"
    return _text(value)


def _confidence(value: Any) -> str:
    if isinstance(value, bool):
        return _text(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if 0.0 <= numeric <= 1.0:
            return f"{numeric * 100:.1f}%"
        return f"{numeric:.3f}"
    return _text(value)


def _count_by(
    records: Sequence[Mapping[str, Any]],
    field: str,
) -> Counter[str]:
    counter: Counter[str] = Counter()
    for record in records:
        value = record.get(field)
        key = _text(value)
        if key != "—":
            counter[key] += 1
    return counter


def _distribution_text(counter: Counter[str]) -> str:
    if not counter:
        return "—"
    return ", ".join(
        f"{_humanize(key)}: {counter[key]}"
        for key in sorted(counter, key=lambda item: item.casefold())
    )


def _location_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "—"

    parts: list[str] = []
    if value.get("page") is not None:
        parts.append(f"page {value['page']}")
    if value.get("row") is not None:
        parts.append(f"row {value['row']}")
    if value.get("element"):
        parts.append(f"element {value['element']}")
    if value.get("path"):
        parts.append(f"path {value['path']}")

    return "; ".join(parts) if parts else "—"


def _full_hash_text(source: Mapping[str, Any]) -> str:
    value = source.get("source_hash")
    if not value:
        return "—"
    algorithm = _text(source.get("hash_algorithm"))
    if algorithm == "—":
        algorithm = "hash"
    return f"{algorithm}: {value}"


def _current_review_outcome(review: Mapping[str, Any]) -> str:
    decisions = review.get("decisions")
    if not isinstance(decisions, Mapping):
        return "Pending"

    history = decisions.get("decisions")
    if not _is_sequence(history) or not history:
        return "Pending"

    current = history[-1]
    if not isinstance(current, Mapping):
        return "Pending"

    outcome = current.get("outcome")
    return _humanize(outcome) if outcome is not None else "Pending"


def _footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    try:
        canvas.setStrokeColor(_BORDER)
        canvas.setLineWidth(0.25)
        line_y = 12 * mm
        canvas.line(
            doc.leftMargin,
            line_y,
            _PAGE_WIDTH - doc.rightMargin,
            line_y,
        )

        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_MUTED)
        footer_y = 8 * mm
        canvas.drawString(doc.leftMargin, footer_y, "BIMAP")
        canvas.drawCentredString(
            _PAGE_WIDTH / 2,
            footer_y,
            str(canvas.getPageNumber()),
        )
        canvas.drawRightString(
            _PAGE_WIDTH - doc.rightMargin,
            footer_y,
            "Powered by SLAI",
        )
    finally:
        canvas.restoreState()


class ReportLabAuditReportRenderer:
    """
    Render the BIMAP human-readable audit report as PDF bytes.

    Accepted context is the exact projection assembled by ``ReportBuilder``:
    ``report``, ``findings``, ``evidence_manifest``, ``remediation``,
    ``requirement_matrix`` and ``reviews``.

    This class performs representation validation only. It never changes rule
    outcomes, finding severity/status, evidence semantics, or governance state.
    """

    def render(self, *, context: Mapping[str, Any]) -> bytes:
        printer.status("REPORT", "Rendering BIMAP audit PDF", "info")

        if not isinstance(context, Mapping):
            raise ReportingValidationError(
                "Audit report render context must be a mapping.",
                component=_COMPONENT,
                field="context",
                context={"received_type": type(context).__name__},
            )

        try:
            report = _as_mapping(
                context.get("report"),
                field="context.report",
            )
            findings = _mapping_sequence(
                context.get("findings", ()),
                field="context.findings",
            )
            evidence_manifest = _as_mapping(
                context.get("evidence_manifest", {}),
                field="context.evidence_manifest",
            )
            remediation = _mapping_sequence(
                context.get("remediation", ()),
                field="context.remediation",
            )
            requirements = _mapping_sequence(
                context.get("requirement_matrix", ()),
                field="context.requirement_matrix",
            )
            reviews = _mapping_sequence(
                context.get("reviews", ()),
                field="context.reviews",
            )

            sources = _mapping_sequence(
                evidence_manifest.get("sources", ()),
                field="context.evidence_manifest.sources",
            )
            evidence = _mapping_sequence(
                evidence_manifest.get("evidence", ()),
                field="context.evidence_manifest.evidence",
            )

            buffer = BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=18 * mm,
                leftMargin=18 * mm,
                topMargin=16 * mm,
                bottomMargin=18 * mm,
                title=_TITLE,
                author="R3D BIM Audit Platform",
                subject="BIMAP audit report",
                creator="R3D BIMAP",
            )

            styles = self._styles()
            story: list[Flowable] = []

            self._append_title(story, doc=doc, styles=styles)
            self._append_report_identity(
                story,
                report=report,
                doc=doc,
                styles=styles,
            )
            self._append_summary(
                story,
                findings=findings,
                sources=sources,
                evidence=evidence,
                requirements=requirements,
                reviews=reviews,
                doc=doc,
                styles=styles,
            )

            story.append(Spacer(1, 3 * mm))
            story.append(
                Paragraph(
                    (
                        "This PDF is the human-readable audit view. "
                        "The complete machine-readable contract values remain "
                        "authoritative in findings.json, evidence_manifest.json, "
                        "remediation.csv and, when applicable, "
                        "requirement_matrix.csv."
                    ),
                    styles["note"],
                )
            )

            if findings or remediation or evidence or requirements or reviews:
                story.append(PageBreak())

            self._append_findings(
                story,
                findings=findings,
                doc=doc,
                styles=styles,
            )
            self._append_remediation(
                story,
                remediation=remediation,
                doc=doc,
                styles=styles,
            )
            self._append_evidence(
                story,
                evidence_manifest=evidence_manifest,
                sources=sources,
                evidence=evidence,
                doc=doc,
                styles=styles,
            )
            self._append_requirements(
                story,
                requirements=requirements,
                doc=doc,
                styles=styles,
            )
            self._append_reviews(
                story,
                reviews=reviews,
                doc=doc,
                styles=styles,
            )

            doc.build(
                story,
                onFirstPage=_footer,
                onLaterPages=_footer,
            )

            payload = buffer.getvalue()
            if not payload or not payload.startswith(b"%PDF"):
                raise ReportTemplateError(
                    "Audit report renderer did not produce a valid PDF payload.",
                    component=_COMPONENT,
                    field="rendered_pdf",
                )

            logger.info(
                {
                    "event": "audit_report_pdf_rendered",
                    "finding_count": len(findings),
                    "source_count": len(sources),
                    "evidence_count": len(evidence),
                    "requirement_count": len(requirements),
                    "review_count": len(reviews),
                    "size_bytes": len(payload),
                }
            )
            return payload

        except (ReportingValidationError, ReportTemplateError):
            raise
        except Exception as exc:
            raise ReportTemplateError(
                "Unable to render the BIMAP audit report PDF.",
                component=_COMPONENT,
                cause=exc,
            ) from exc

    @staticmethod
    def _styles() -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "BIMAPAuditTitle",
                parent=base["Title"],
                fontName="Helvetica-Bold",
                fontSize=_TITLE_FONT_SIZE,
                leading=26,
                alignment=TA_LEFT,
                textColor=_TEXT,
                spaceAfter=0,
            ),
            "subtitle": ParagraphStyle(
                "BIMAPAuditSubtitle",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9.0,
                leading=12,
                textColor=_MUTED,
                spaceBefore=1,
                spaceAfter=0,
            ),
            "h1": ParagraphStyle(
                "BIMAPAuditH1",
                parent=base["Heading1"],
                fontName="Helvetica-Bold",
                fontSize=14,
                leading=18,
                textColor=_TEXT,
                spaceBefore=8,
                spaceAfter=7,
            ),
            "h2": ParagraphStyle(
                "BIMAPAuditH2",
                parent=base["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=10.5,
                leading=14,
                textColor=_TEXT,
                spaceBefore=6,
                spaceAfter=5,
            ),
            "label": ParagraphStyle(
                "BIMAPAuditLabel",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=_BODY_FONT_SIZE,
                leading=11,
                textColor=_TEXT,
            ),
            "cell": ParagraphStyle(
                "BIMAPAuditCell",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=_BODY_FONT_SIZE,
                leading=11,
                textColor=_TEXT,
            ),
            "small": ParagraphStyle(
                "BIMAPAuditSmall",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=_SMALL_FONT_SIZE,
                leading=9.5,
                textColor=_TEXT,
            ),
            "mono": ParagraphStyle(
                "BIMAPAuditMono",
                parent=base["BodyText"],
                fontName="Courier",
                fontSize=6.8,
                leading=8.6,
                textColor=_TEXT,
            ),
            "body": ParagraphStyle(
                "BIMAPAuditBody",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=_BODY_FONT_SIZE,
                leading=12,
                textColor=_TEXT,
                spaceAfter=3,
            ),
            "note": ParagraphStyle(
                "BIMAPAuditNote",
                parent=base["BodyText"],
                fontName="Helvetica-Oblique",
                fontSize=7.8,
                leading=10.5,
                textColor=_MUTED,
            ),
        }

    @staticmethod
    def _p(value: Any, style: ParagraphStyle) -> Paragraph:
        return Paragraph(_safe_text(value), style)

    @classmethod
    def _two_column_table(
        cls,
        rows: Sequence[tuple[Any, Any] | list[Any]],
        *,
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
        label_width: float = 48 * mm,
        mono_value_rows: set[int] | None = None,
    ) -> Table:
        mono_value_rows = mono_value_rows or set()
        rendered_rows: list[list[Flowable]] = []

        for index, row in enumerate(rows):
            label, value = row
            value_style = styles["mono"] if index in mono_value_rows else styles["cell"]
            rendered_rows.append(
                [
                    cls._p(label, styles["label"]),
                    cls._p(value, value_style),
                ]
            )

        table = Table(
            rendered_rows,
            colWidths=[label_width, doc.width - label_width],
            repeatRows=0,
            splitByRow=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, _BORDER),
                    ("BACKGROUND", (0, 0), (0, -1), _SOFT),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    @classmethod
    def _inventory_table(
        cls,
        *,
        headers: Sequence[str],
        rows: Sequence[Sequence[Any]],
        widths: Sequence[float],
        styles: Mapping[str, ParagraphStyle],
    ) -> LongTable:
        rendered: list[list[Flowable]] = [
            [cls._p(header, styles["label"]) for header in headers]
        ]
        for row in rows:
            rendered.append(
                [cls._p(value, styles["small"]) for value in row]
            )

        table = LongTable(
            rendered,
            colWidths=list(widths),
            repeatRows=1,
            splitByRow=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, _BORDER),
                    ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ]
            )
        )
        return table

    @classmethod
    def _append_title(
        cls,
        story: list[Flowable],
        *,
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        logo: ReportLabImage | None = None

        if _LOGO_PATH.is_file():
            try:
                logo = ReportLabImage(str(_LOGO_PATH))
                if not logo.imageHeight:
                    raise ValueError("BIMAP logo has zero image height.")
                scale = _TITLE_FONT_SIZE / float(logo.imageHeight)
                logo.drawHeight = _TITLE_FONT_SIZE
                logo.drawWidth = float(logo.imageWidth) * scale
            except Exception as exc:
                logger.warning(
                    {
                        "event": "audit_report_logo_unavailable",
                        "cause_type": type(exc).__name__,
                    }
                )
                logo = None

        title_block = [
            Paragraph(_TITLE, styles["title"]),
            Paragraph(_SUBTITLE, styles["subtitle"]),
        ]
        title_table_inner = Table([[title_block]], colWidths=[doc.width])
        title_table_inner.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )

        if logo is not None:
            logo_column = logo.drawWidth + 3 * mm
            title_table = Table(
                [[logo, title_table_inner]],
                colWidths=[logo_column, doc.width - logo_column],
            )
            title_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            story.append(title_table)
        else:
            story.append(title_table_inner)

        story.append(Spacer(1, 4 * mm))
        story.append(
            HRFlowable(
                width="100%",
                thickness=0.6,
                color=_BORDER,
                spaceBefore=0,
                spaceAfter=4 * mm,
            )
        )

    @classmethod
    def _append_report_identity(
        cls,
        story: list[Flowable],
        *,
        report: Mapping[str, Any],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Report identity", styles["h1"]))
        rows = (
            ("Report ID", report.get("report_id")),
            ("Order ID", report.get("order_id")),
            ("Report version", report.get("report_version")),
            ("Generated at", report.get("generated_at")),
            ("Expires at", report.get("expires_at")),
        )
        story.append(cls._two_column_table(rows, doc=doc, styles=styles))

    @classmethod
    def _append_summary(
        cls,
        story: list[Flowable],
        *,
        findings: Sequence[Mapping[str, Any]],
        sources: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        requirements: Sequence[Mapping[str, Any]],
        reviews: Sequence[Mapping[str, Any]],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Audit summary", styles["h1"]))
        rows = (
            ("Findings", len(findings)),
            ("Evidence items", len(evidence)),
            ("Evidence sources", len(sources)),
            ("Requirement assessments", len(requirements)),
            ("Governance reviews", len(reviews)),
            ("Severity distribution", _distribution_text(_count_by(findings, "severity"))),
            ("Finding status distribution", _distribution_text(_count_by(findings, "status"))),
            ("Scope distribution", _distribution_text(_count_by(findings, "scope"))),
        )
        story.append(cls._two_column_table(rows, doc=doc, styles=styles))

    @classmethod
    def _append_findings(
        cls,
        story: list[Flowable],
        *,
        findings: Sequence[Mapping[str, Any]],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Findings", styles["h1"]))

        if not findings:
            story.append(
                Paragraph(
                    "No finding records are present in this report.",
                    styles["body"],
                )
            )
            return

        for index, finding in enumerate(findings, start=1):
            finding_id = finding.get("finding_id") or f"Finding {index}"
            title = finding.get("title")
            heading = f"{finding_id} — {title}" if title else _text(finding_id)
            story.append(Paragraph(_safe_text(heading), styles["h2"]))

            rows = (
                ("Rule ID", finding.get("rule_id")),
                ("Scope", _humanize(finding.get("scope"))),
                ("Category", _humanize(finding.get("category"))),
                ("Automation", _humanize(finding.get("automation_type"))),
                ("Severity", _humanize(finding.get("severity"))),
                ("Confidence", _confidence(finding.get("confidence"))),
                ("Status", _humanize(finding.get("status"))),
                ("Evidence refs", _identifier_list(finding.get("evidence_refs"))),
                ("Observed", finding.get("observed_value")),
                ("Expected", finding.get("expected_value")),
            )
            story.append(cls._two_column_table(rows, doc=doc, styles=styles))

            explanation = finding.get("explanation")
            if explanation not in (None, ""):
                story.append(Spacer(1, 2 * mm))
                story.append(
                    Paragraph(
                        "<b>Explanation</b><br/>" + _safe_text(explanation),
                        styles["body"],
                    )
                )

            story.append(Spacer(1, 2 * mm))
            story.append(
                HRFlowable(
                    width="100%",
                    thickness=0.35,
                    color=_BORDER,
                    spaceBefore=1,
                    spaceAfter=2,
                )
            )

    @classmethod
    def _append_remediation(
        cls,
        story: list[Flowable],
        *,
        remediation: Sequence[Mapping[str, Any]],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Remediation and verification", styles["h1"]))

        if not remediation:
            story.append(
                Paragraph(
                    "No remediation projection rows are present.",
                    styles["body"],
                )
            )
            return

        for index, row in enumerate(remediation, start=1):
            finding_id = row.get("finding_id") or f"Action {index}"
            title = row.get("title")
            heading = f"{finding_id} — {title}" if title else _text(finding_id)
            story.append(Paragraph(_safe_text(heading), styles["h2"]))

            rows = (
                ("Severity", _humanize(row.get("severity"))),
                ("Status", _humanize(row.get("status"))),
                ("Rule ID", row.get("rule_id")),
                ("Remediation", row.get("remediation")),
                ("Verification method", row.get("verification_method")),
                ("Evidence refs", _identifier_list(row.get("evidence_refs"))),
            )
            story.append(cls._two_column_table(rows, doc=doc, styles=styles))
            story.append(Spacer(1, 2 * mm))

    @classmethod
    def _append_evidence(
        cls,
        story: list[Flowable],
        *,
        evidence_manifest: Mapping[str, Any],
        sources: Sequence[Mapping[str, Any]],
        evidence: Sequence[Mapping[str, Any]],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Evidence and provenance", styles["h1"]))

        rows = (
            (
                "Evidence count",
                evidence_manifest.get("evidence_count")
                if evidence_manifest.get("evidence_count") is not None
                else len(evidence),
            ),
            (
                "Source count",
                evidence_manifest.get("source_count")
                if evidence_manifest.get("source_count") is not None
                else len(sources),
            ),
            (
                "Evidence schema versions",
                _identifier_list(evidence_manifest.get("schema_versions")),
            ),
        )
        story.append(cls._two_column_table(rows, doc=doc, styles=styles))

        if sources:
            story.append(Paragraph("Source inventory", styles["h2"]))
            for source in sources:
                source_rows = (
                    ("Source file ID", source.get("source_file_id")),
                    ("Original filename", source.get("original_filename")),
                    ("Source type", source.get("source_type")),
                    ("Source version", source.get("source_version")),
                    ("Hash", _full_hash_text(source)),
                    ("Extractor names", _identifier_list(source.get("extractor_names"))),
                    ("Extractor versions", _identifier_list(source.get("extractor_versions"))),
                    ("Evidence IDs", _identifier_list(source.get("evidence_ids"))),
                )
                story.append(
                    cls._two_column_table(
                        source_rows,
                        doc=doc,
                        styles=styles,
                        mono_value_rows={4},
                    )
                )
                story.append(Spacer(1, 2 * mm))

        if evidence:
            story.append(Paragraph("Evidence inventory", styles["h2"]))
            inventory_rows: list[list[Any]] = []
            for item in evidence:
                inventory_rows.append(
                    [
                        item.get("evidence_id"),
                        item.get("source_file_id"),
                        _location_text(item.get("logical_location")),
                        item.get("extractor_name") or item.get("extractor_version"),
                        _confidence(item.get("confidence")),
                    ]
                )

            story.append(
                cls._inventory_table(
                    headers=(
                        "Evidence ID",
                        "Source ID",
                        "Logical location",
                        "Extractor",
                        "Confidence",
                    ),
                    rows=inventory_rows,
                    widths=(35 * mm, 35 * mm, 51 * mm, 29 * mm, 20 * mm),
                    styles=styles,
                )
            )
            story.append(Spacer(1, 2 * mm))
            story.append(
                Paragraph(
                    (
                        "Extracted evidence values are intentionally not duplicated "
                        "in this human-readable inventory; their complete validated "
                        "representations remain in evidence_manifest.json."
                    ),
                    styles["note"],
                )
            )
        elif not sources:
            story.append(
                Paragraph(
                    "No evidence inventory is present.",
                    styles["body"],
                )
            )

    @classmethod
    def _append_requirements(
        cls,
        story: list[Flowable],
        *,
        requirements: Sequence[Mapping[str, Any]],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Requirement–evidence matrix", styles["h1"]))

        if not requirements:
            story.append(
                Paragraph(
                    (
                        "No requirement assessment rows are present. "
                        "This is expected for audit products that do not include "
                        "the BIM QA requirement-matrix scope."
                    ),
                    styles["body"],
                )
            )
            return

        for index, requirement in enumerate(requirements, start=1):
            requirement_id = requirement.get("requirement_id") or f"Requirement {index}"
            story.append(Paragraph(_safe_text(requirement_id), styles["h2"]))

            rows = (
                ("Assessment", _humanize(requirement.get("assessment"))),
                ("Automation", _humanize(requirement.get("automation_type"))),
                ("Confidence", _confidence(requirement.get("confidence"))),
                ("Source requirement", requirement.get("source_requirement")),
                ("Impact", requirement.get("impact")),
                ("Recommended action", requirement.get("recommended_action")),
                ("Evidence refs", _identifier_list(requirement.get("evidence_refs"))),
            )
            story.append(cls._two_column_table(rows, doc=doc, styles=styles))
            story.append(Spacer(1, 2 * mm))

    @classmethod
    def _append_reviews(
        cls,
        story: list[Flowable],
        *,
        reviews: Sequence[Mapping[str, Any]],
        doc: SimpleDocTemplate,
        styles: Mapping[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("Governance reviews", styles["h1"]))

        if not reviews:
            story.append(
                Paragraph(
                    "No governance review records are included in this report.",
                    styles["body"],
                )
            )
            return

        for index, review in enumerate(reviews, start=1):
            review_id = review.get("review_id") or f"Review {index}"
            finding = review.get("finding")
            finding_id = finding.get("finding_id") if isinstance(finding, Mapping) else None

            story.append(Paragraph(_safe_text(review_id), styles["h2"]))
            rows = (
                ("Finding ID", finding_id),
                ("Reason codes", _identifier_list(review.get("reason_codes"))),
                ("Requested at", review.get("requested_at")),
                ("Requested by", review.get("requested_by")),
                ("Current outcome", _current_review_outcome(review)),
            )
            story.append(cls._two_column_table(rows, doc=doc, styles=styles))
            story.append(Spacer(1, 2 * mm))


__all__ = ["ReportLabAuditReportRenderer"]
