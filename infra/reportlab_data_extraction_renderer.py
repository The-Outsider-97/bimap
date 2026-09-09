"""ReportLab PDF renderer for BIMAP data-extraction summaries."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any

from reportlab.lib import colors  # type: ignore
from reportlab.lib.enums import TA_LEFT  # type: ignore
from reportlab.lib.pagesizes import A4  # type: ignore
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore
from reportlab.lib.units import mm  # type: ignore
from reportlab.platypus import (  # type: ignore
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..app.ports.data_extraction import DataExtractionPDFRenderer
from ..app.utils.app_errors import *
from ..app.utils.app_helpers import *
from logs.logger import PrettyPrinter, get_logger  # type: ignore


logger = get_logger("BIMAP Data Extraction PDF Renderer")
printer = PrettyPrinter()

_COMPONENT = "reportlab_data_extraction_renderer"


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _short_hash(value: Any) -> str:
    text = _text(value)
    if len(text) <= 40:
        return text
    return f"{text[:20]}…{text[-16:]}"


class ReportLabDataExtractionPDFRenderer(DataExtractionPDFRenderer):
    """Render a compact summary; the JSON file remains the complete dataset."""

    def render(self, *, document: Mapping[str, Any]) -> bytes:
        printer.status("EXTRACT", "Rendering data-extraction PDF", "info")

        if not isinstance(document, Mapping):
            raise UnsupportedAppInputError(
                "Data-extraction PDF document must be a mapping.",
                component=_COMPONENT,
                operation="render",
                field="document",
                context={"received_type": type(document).__name__},
            )

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title="R3D BIMAP Data Extraction Report",
            author="R3D BIM Audit Platform",
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "BIMAPTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            alignment=TA_LEFT,
            spaceAfter=10,
        )
        h1 = ParagraphStyle(
            "BIMAPH1",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=8,
        )
        body = ParagraphStyle(
            "BIMAPBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            spaceAfter=5,
        )
        mono = ParagraphStyle(
            "BIMAPMono",
            parent=body,
            fontName="Courier",
            fontSize=7.5,
            leading=10,
        )

        extraction = dict(document.get("extraction") or {})
        source = dict(document.get("source") or {})
        model = dict(document.get("model") or {})
        project = dict(model.get("project") or {})
        counts = dict(model.get("counts") or {})
        class_counts = dict(model.get("ifc_class_counts") or {})
        selected = tuple(extraction.get("datasets") or ())

        story = [
            Paragraph("R3D BIMAP Data Extraction Report", title_style),
            Paragraph(
                "Human-readable summary of the structured IFC extraction. "
                "The JSON file in the same package is the authoritative complete "
                "machine-readable result.",
                body,
            ),
            Spacer(1, 4 * mm),
            Paragraph("Extraction identity", h1),
        ]

        identity_rows = [
            ["Extraction ID", _text(extraction.get("extraction_id"))],
            ["Generated", _text(extraction.get("generated_at"))],
            ["IFC schema", _text(source.get("ifc_schema"))],
            ["Project", _text(project.get("name"))],
            ["Source file", _text(source.get("filename"))],
            ["Source size", _text(source.get("size_bytes")) + " bytes"],
            ["Products", _text(source.get("product_count"))],
            ["Datasets", ", ".join(map(str, selected)) if selected else "—"],
        ]

        identity = Table(identity_rows, colWidths=[42 * mm, 118 * mm])
        identity.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F4F4")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(identity)

        story.extend(
            [
                Paragraph("Integrity", h1),
                Paragraph(
                    f"Source SHA-256: {_short_hash(source.get('sha256'))}",
                    mono,
                ),
                Paragraph("Dataset counts", h1),
            ]
        )

        count_rows = [["Dataset", "Rows"]]
        for name in ("elements", "properties", "quantities", "materials"):
            if name in counts:
                count_rows.append(
                    [name.replace("_", " ").title(), _text(counts[name])]
                )

        count_table = Table(
            count_rows,
            colWidths=[100 * mm, 40 * mm],
            repeatRows=1,
        )
        count_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(count_table)

        story.append(Paragraph("IFC class distribution", h1))
        class_rows = [["IFC class", "Products"]]
        for ifc_class, count in sorted(
            class_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        ):
            class_rows.append([_text(ifc_class), _text(count)])

        class_table = Table(
            class_rows,
            colWidths=[100 * mm, 40 * mm],
            repeatRows=1,
        )
        class_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(class_table)

        units = tuple(model.get("units") or ())
        if units:
            story.extend([PageBreak(), Paragraph("Project units", h1)])
            unit_rows = [["Type", "Name", "Prefix", "IFC class"]]
            for unit in units:
                if not isinstance(unit, Mapping):
                    continue
                unit_rows.append(
                    [
                        _text(unit.get("unit_type")),
                        _text(unit.get("name")),
                        _text(unit.get("prefix")),
                        _text(unit.get("ifc_class")),
                    ]
                )

            unit_table = Table(
                unit_rows,
                colWidths=[42 * mm, 42 * mm, 30 * mm, 48 * mm],
                repeatRows=1,
            )
            unit_table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            story.append(unit_table)

        story.extend(
            [
                Spacer(1, 5 * mm),
                Paragraph("Scope note", h1),
                Paragraph(
                    "This PDF intentionally summarizes the extraction instead of "
                    "reproducing every property, quantity, and material row. "
                    "Refer to the JSON artifact in this package for the complete "
                    "normalized dataset and stable extraction metadata.",
                    body,
                ),
            ]
        )

        try:
            doc.build(story)
        except Exception as exc:
            raise AppIntegrityError(
                "ReportLab could not render the data-extraction PDF.",
                component=_COMPONENT,
                operation="render",
                context=lower_error_context(exc),
                cause=exc,
            ) from exc

        payload = buffer.getvalue()
        if not payload.startswith(b"%PDF-"):
            raise AppIntegrityError(
                "Rendered data-extraction artifact is not a valid PDF byte stream.",
                component=_COMPONENT,
                operation="render",
                field="pdf",
            )

        logger.info(
            {
                "event": "data_extraction_pdf_rendered",
                "size_bytes": len(payload),
            }
        )
        return payload


__all__ = ["ReportLabDataExtractionPDFRenderer"]
