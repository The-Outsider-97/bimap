"""ReportLab PDF renderer for BIMAP data-extraction reports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors  # type: ignore
from reportlab.lib.enums import TA_LEFT  # type: ignore
from reportlab.lib.pagesizes import A4  # type: ignore
from reportlab.lib.styles import (  # type: ignore
    ParagraphStyle,
    getSampleStyleSheet,
)
from reportlab.lib.units import mm  # type: ignore
from reportlab.platypus import (  # type: ignore
    Flowable,
    Image as ReportLabImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..app.ports.data_extraction import (
    DataExtractionPDFRenderer,
)
from ..app.utils.app_errors import *
from ..app.utils.app_helpers import *
from logs.logger import (  # type: ignore
    PrettyPrinter,
    get_logger,
)


logger = get_logger(
    "BIMAP Data Extraction PDF Renderer"
)
printer = PrettyPrinter()

_COMPONENT = (
    "reportlab_data_extraction_renderer"
)

_TITLE_FONT_SIZE = 22.0

_LOGO_PATH = (
    Path(__file__)
    .resolve()
    .parents[1]
    / "reporting"
    / "templates"
    / "bimap-logo.png"
)


def _text(
    value: Any,
) -> str:
    if value is None:
        return "—"

    if isinstance(
        value,
        bool,
    ):
        return (
            "Yes"
            if value
            else "No"
        )

    return str(value)


def _safe_text(
    value: Any,
) -> str:
    return escape(
        _text(value)
    ).replace(
        "\n",
        "<br/>",
    )


def _format_source_size(
    value: Any,
) -> str:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    ):
        return (
            f"{value:,} bytes"
        )

    return "—"


def _schema_or_format(
    source: Mapping[
        str,
        Any,
    ],
) -> str:
    schema = (
        source.get("schema")
        or source.get(
            "ifc_schema"
        )
    )

    if schema:
        return str(schema)

    source_format = (
        source.get(
            "source_format"
        )
    )

    if source_format:
        return str(
            source_format
        ).upper()

    return "—"


def _footer(
    canvas: Any,
    doc: Any,
) -> None:
    page_width, _ = A4

    canvas.saveState()

    try:
        canvas.setFont(
            "Helvetica",
            7.5,
        )

        canvas.setFillColor(
            colors.HexColor(
                "#666666"
            )
        )

        footer_y = (
            8 * mm
        )

        canvas.drawString(
            doc.leftMargin,
            footer_y,
            "BIMAP",
        )

        canvas.drawCentredString(
            page_width / 2,
            footer_y,
            str(
                canvas
                .getPageNumber()
            ),
        )

        canvas.drawRightString(
            page_width
            - doc.rightMargin,
            footer_y,
            "Powered by SLAI",
        )

    finally:
        canvas.restoreState()


class ReportLabDataExtractionPDFRenderer(
    DataExtractionPDFRenderer
):
    """Render the BIMAP extraction PDF."""

    def render(
        self,
        *,
        document: Mapping[
            str,
            Any,
        ],
        preview_png:
            bytes | None = None,
    ) -> bytes:
        printer.status(
            "EXTRACT",
            (
                "Rendering "
                "data-extraction PDF"
            ),
            "info",
        )

        if not isinstance(
            document,
            Mapping,
        ):
            raise UnsupportedAppInputError(
                (
                    "Data-extraction PDF "
                    "document must be a mapping."
                ),
                component=_COMPONENT,
                operation="render",
                field="document",
                context={
                    "received_type":
                        type(
                            document
                        ).__name__,
                },
            )

        extraction = dict(
            document.get(
                "extraction"
            )
            or {}
        )

        requested_by = dict(
            extraction.get(
                "requested_by"
            )
            or {}
        )

        source = dict(
            document.get(
                "source"
            )
            or {}
        )

        model = dict(
            document.get(
                "model"
            )
            or {}
        )

        counts = dict(
            model.get(
                "counts"
            )
            or {}
        )

        class_counts = dict(
            model.get(
                "ifc_class_counts"
            )
            or {}
        )

        geometry = dict(
            model.get(
                "geometry_summary"
            )
            or {}
        )

        datasets = extraction.get(
            "datasets"
        )
        selected: tuple[Any, ...] = (
            tuple(datasets)
            if isinstance(
                datasets,
                Sequence,
            )
            and not isinstance(
                datasets,
                (str, bytes),
            )
            else ()
        )

        buffer = BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=16 * mm,
            bottomMargin=18 * mm,
            title=(
                "R3D BIMAP "
                "Data Extraction Report"
            ),
            author=(
                "R3D BIM Audit Platform"
            ),
        )

        styles = (
            getSampleStyleSheet()
        )

        title_style = (
            ParagraphStyle(
                "BIMAPTitle",
                parent=
                    styles["Title"],
                fontName=
                    "Helvetica-Bold",
                fontSize=
                    _TITLE_FONT_SIZE,
                leading=26,
                alignment=TA_LEFT,
                spaceAfter=0,
            )
        )

        h1 = ParagraphStyle(
            "BIMAPH1",
            parent=
                styles["Heading1"],
            fontName=
                "Helvetica-Bold",
            fontSize=14,
            leading=18,
            spaceBefore=10,
            spaceAfter=8,
        )

        label_style = (
            ParagraphStyle(
                "BIMAPLabel",
                parent=
                    styles["BodyText"],
                fontName=
                    "Helvetica-Bold",
                fontSize=8.5,
                leading=11,
            )
        )

        cell_style = (
            ParagraphStyle(
                "BIMAPCell",
                parent=
                    styles["BodyText"],
                fontName=
                    "Helvetica",
                fontSize=8.5,
                leading=11,
            )
        )

        mono_style = (
            ParagraphStyle(
                "BIMAPMono",
                parent=cell_style,
                fontName="Courier",
                fontSize=7.3,
                leading=9.5,
            )
        )

        body_style = (
            ParagraphStyle(
                "BIMAPBody",
                parent=
                    styles["BodyText"],
                fontName=
                    "Helvetica",
                fontSize=8.5,
                leading=12,
            )
        )

        def label(
            value: Any,
        ) -> Paragraph:
            return Paragraph(
                _safe_text(value),
                label_style,
            )

        def cell(
            value: Any,
        ) -> Paragraph:
            return Paragraph(
                _safe_text(value),
                cell_style,
            )

        def mono(
            value: Any,
        ) -> Paragraph:
            return Paragraph(
                _safe_text(value),
                mono_style,
            )

        def two_column_table(
            rows: Sequence[
                list[
                    Flowable
                ]
                | tuple[
                    Flowable,
                    ...,
                ]
            ],
        ) -> Table:
            table = Table(
                rows,
                colWidths=[
                    48 * mm,
                    doc.width
                    - 48 * mm,
                ],
            )

            table.setStyle(
                TableStyle(
                    [
                        (
                            "VALIGN",
                            (0, 0),
                            (-1, -1),
                            "TOP",
                        ),
                        (
                            "GRID",
                            (0, 0),
                            (-1, -1),
                            0.25,
                            colors.HexColor(
                                "#D9D9D9"
                            ),
                        ),
                        (
                            "BACKGROUND",
                            (0, 0),
                            (0, -1),
                            colors.HexColor(
                                "#F4F4F4"
                            ),
                        ),
                        (
                            "LEFTPADDING",
                            (0, 0),
                            (-1, -1),
                            5,
                        ),
                        (
                            "RIGHTPADDING",
                            (0, 0),
                            (-1, -1),
                            5,
                        ),
                        (
                            "TOPPADDING",
                            (0, 0),
                            (-1, -1),
                            4,
                        ),
                        (
                            "BOTTOMPADDING",
                            (0, 0),
                            (-1, -1),
                            4,
                        ),
                    ]
                )
            )

            return table

        story: list[
            Flowable
        ] = []

        #
        # Title + packaged BIMAP logo.
        #
        logo: ReportLabImage | None = None

        if _LOGO_PATH.is_file():
            try:
                logo = ReportLabImage(
                    str(
                        _LOGO_PATH
                    )
                )

                scale = (
                    _TITLE_FONT_SIZE
                    / float(
                        logo.imageHeight
                    )
                )

                logo.drawHeight = (
                    _TITLE_FONT_SIZE
                )

                logo.drawWidth = (
                    float(
                        logo.imageWidth
                    )
                    * scale
                )

            except Exception as exc:
                logger.warning(
                    {
                        "event":
                            "data_extraction_logo_unavailable",
                        "error":
                            lower_error_context(
                                exc
                            ),
                    }
                )

                logo = None

        title_paragraph = (
            Paragraph(
                (
                    "R3D BIMAP "
                    "Data Extraction Report"
                ),
                title_style,
            )
        )

        if logo is not None:
            logo_column = (
                logo.drawWidth
                + 3 * mm
            )

            title_table = Table(
                [
                    [
                        logo,
                        title_paragraph,
                    ]
                ],
                colWidths=[
                    logo_column,
                    doc.width
                    - logo_column,
                ],
            )

            title_table.setStyle(
                TableStyle(
                    [
                        (
                            "VALIGN",
                            (0, 0),
                            (-1, -1),
                            "MIDDLE",
                        ),
                        (
                            "LEFTPADDING",
                            (0, 0),
                            (-1, -1),
                            0,
                        ),
                        (
                            "RIGHTPADDING",
                            (0, 0),
                            (-1, -1),
                            0,
                        ),
                        (
                            "TOPPADDING",
                            (0, 0),
                            (-1, -1),
                            0,
                        ),
                        (
                            "BOTTOMPADDING",
                            (0, 0),
                            (-1, -1),
                            0,
                        ),
                    ]
                )
            )

            story.append(
                title_table
            )
        else:
            story.append(
                title_paragraph
            )

        story.append(
            Spacer(
                1,
                4 * mm,
            )
        )

        #
        # Model PNG immediately beneath title.
        #
        if preview_png:
            try:
                preview = (
                    ReportLabImage(
                        BytesIO(
                            bytes(
                                preview_png
                            )
                        )
                    )
                )

                max_width = (
                    doc.width
                )

                max_height = (
                    80 * mm
                )

                scale = min(
                    max_width
                    / float(
                        preview
                        .imageWidth
                    ),
                    max_height
                    / float(
                        preview
                        .imageHeight
                    ),
                )

                preview.drawWidth = (
                    float(
                        preview
                        .imageWidth
                    )
                    * scale
                )

                preview.drawHeight = (
                    float(
                        preview
                        .imageHeight
                    )
                    * scale
                )

                preview.hAlign = (
                    "CENTER"
                )

                story.append(
                    preview
                )

                story.append(
                    Spacer(
                        1,
                        4 * mm,
                    )
                )

            except Exception as exc:
                logger.warning(
                    {
                        "event":
                            "data_extraction_preview_omitted",
                        "error":
                            lower_error_context(
                                exc
                            ),
                    }
                )

        else:
            story.append(
                Paragraph(
                    (
                        "Model preview unavailable "
                        "from the configured "
                        "source adapter."
                    ),
                    body_style,
                )
            )

            story.append(
                Spacer(
                    1,
                    2 * mm,
                )
            )

        #
        # Extraction identity.
        #
        story.append(
            Paragraph(
                "Extraction identity",
                h1,
            )
        )

        generated_local = (
            extraction.get(
                "generated_at_local"
            )
            or extraction.get(
                "generated_at"
            )
        )

        datasets_text = (
            ", ".join(
                str(item)
                for item
                in selected
            )
            if selected
            else "—"
        )

        identity_rows = [
            [
                label(
                    "Extracted by"
                ),
                cell(
                    requested_by.get(
                        "display_name"
                    )
                ),
            ],
            [
                label(
                    (
                        "Generated "
                        "(local time)"
                    )
                ),
                cell(
                    generated_local
                ),
            ],
            [
                label(
                    "Extraction ID"
                ),
                cell(
                    extraction.get(
                        "extraction_id"
                    )
                ),
            ],
            [
                label(
                    "Project"
                ),
                cell(
                    extraction.get(
                        "project_name"
                    )
                ),
            ],
            [
                label(
                    (
                        "IFC schema / "
                        "source format"
                    )
                ),
                cell(
                    _schema_or_format(
                        source
                    )
                ),
            ],
            [
                label(
                    "Source file"
                ),
                cell(
                    source.get(
                        "filename"
                    )
                ),
            ],
            [
                label(
                    "Source size"
                ),
                cell(
                    _format_source_size(
                        source.get(
                            "size_bytes"
                        )
                    )
                ),
            ],
            [
                label(
                    "Products"
                ),
                cell(
                    source.get(
                        "product_count"
                    )
                ),
            ],
            [
                label(
                    "Datasets"
                ),
                cell(
                    datasets_text
                ),
            ],
        ]

        story.append(
            two_column_table(
                identity_rows # type: ignore
            )
        )

        #
        # Integrity.
        #
        story.append(
            Paragraph(
                "Integrity",
                h1,
            )
        )

        integrity_rows = [
            [
                label(
                    "Source SHA-256"
                ),
                mono(
                    source.get(
                        "sha256"
                    )
                ),
            ]
        ]

        story.append(
            two_column_table(
                integrity_rows # type: ignore
            )
        )

        #
        # IFC/source class distribution.
        #
        story.append(
            Paragraph(
                (
                    "IFC class "
                    "distribution"
                ),
                h1,
            )
        )

        class_rows: list[
            list[Flowable]
        ] = [
            [
                label(
                    "IFC class"
                ),
                label(
                    "Products"
                ),
            ]
        ]

        def class_sort_key(
            item: tuple[
                Any,
                Any,
            ],
        ) -> tuple[
            int,
            str,
        ]:
            try:
                count_value = int(
                    item[1]
                )
            except (
                TypeError,
                ValueError,
            ):
                count_value = 0

            return (
                -count_value,
                str(
                    item[0]
                ),
            )

        for (
            source_class,
            count,
        ) in sorted(
            class_counts.items(),
            key=class_sort_key,
        ):
            class_rows.append(
                [
                    cell(
                        source_class
                    ),
                    cell(
                        count
                    ),
                ]
            )

        if len(
            class_rows
        ) == 1:
            class_rows.append(
                [
                    cell(
                        (
                            "Not available "
                            "from source adapter"
                        )
                    ),
                    cell(
                        "—"
                    ),
                ]
            )

        class_table = Table(
            class_rows,
            colWidths=[
                doc.width
                - 42 * mm,
                42 * mm,
            ],
            repeatRows=1,
        )

        class_table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor(
                            "#EEEEEE"
                        ),
                    ),
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        0.25,
                        colors.HexColor(
                            "#D9D9D9"
                        ),
                    ),
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "TOP",
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        4,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        4,
                    ),
                ]
            )
        )

        story.append(
            class_table
        )

        #
        # Dataset counts.
        #
        story.append(
            Paragraph(
                "Dataset counts",
                h1,
            )
        )

        selected_names = {
            str(item)
            for item
            in selected
        }

        def dataset_count(
            name: str,
        ) -> Any:
            if name in counts:
                return counts[
                    name
                ]

            if name in (
                selected_names
            ):
                return (
                    "Not available"
                )

            return (
                "Not extracted"
            )

        count_rows: list[
            list[Flowable]
        ] = [
            [
                label(
                    "Dataset"
                ),
                label(
                    "Rows"
                ),
            ],
            [
                cell(
                    "Elements"
                ),
                cell(
                    dataset_count(
                        "elements"
                    )
                ),
            ],
            [
                cell(
                    "Properties"
                ),
                cell(
                    dataset_count(
                        "properties"
                    )
                ),
            ],
            [
                cell(
                    "Quantities"
                ),
                cell(
                    dataset_count(
                        "quantities"
                    )
                ),
            ],
            [
                cell(
                    "Materials"
                ),
                cell(
                    dataset_count(
                        "materials"
                    )
                ),
            ],
        ]

        count_table = Table(
            count_rows,
            colWidths=[
                doc.width
                - 42 * mm,
                42 * mm,
            ],
            repeatRows=1,
        )

        count_table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor(
                            "#EEEEEE"
                        ),
                    ),
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        0.25,
                        colors.HexColor(
                            "#D9D9D9"
                        ),
                    ),
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "TOP",
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        4,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        4,
                    ),
                ]
            )
        )

        story.append(
            count_table
        )

        #
        # Geometry summary.
        #
        story.append(
            Paragraph(
                "Geometry summary",
                h1,
            )
        )

        def geometry_value(
            key: str,
        ) -> Any:
            value = geometry.get(
                key
            )

            if value is None:
                return (
                    "Not available "
                    "from source adapter"
                )

            return value

        geometry_count = (
            geometry.get(
                "geometry_count"
            )
        )

        watertight_count = (
            geometry.get(
                "watertight_geometry_count"
            )
        )

        volume = (
            geometry.get(
                "volume"
            )
        )

        volume_complete = bool(
            geometry.get(
                "volume_complete",
                False,
            )
        )

        volume_unit = (
            geometry.get(
                "volume_unit"
            )
        )

        if (
            volume_complete
            and isinstance(
                volume,
                (
                    int,
                    float,
                ),
            )
            and not isinstance(
                volume,
                bool,
            )
        ):
            volume_text = (
                f"{float(volume):.8g}"
            )

            if volume_unit:
                volume_text += (
                    f" {volume_unit}"
                )

        elif geometry_count is None:
            volume_text = (
                "Not available "
                "from source adapter"
            )

        elif geometry_count == 0:
            volume_text = (
                "Not available — "
                "no tessellated geometry"
            )

        else:
            volume_text = (
                "Not available — "
                "model is not fully "
                "watertight"
            )

        if (
            geometry_count
            is not None
            and watertight_count
            is not None
        ):
            watertight_text = (
                f"{watertight_count}"
                f" / "
                f"{geometry_count}"
            )
        else:
            watertight_text = (
                "Not available "
                "from source adapter"
            )

        geometry_rows = [
            [
                label(
                    "Metric"
                ),
                label(
                    "Value"
                ),
            ],
            [
                cell(
                    "Total polygons"
                ),
                cell(
                    geometry_value(
                        "total_polygons"
                    )
                ),
            ],
            [
                cell(
                    "Total vertices"
                ),
                cell(
                    geometry_value(
                        "total_vertices"
                    )
                ),
            ],
            [
                cell(
                    (
                        "Total Unique "
                        "edges"
                    )
                ),
                cell(
                    geometry_value(
                        "total_edges"
                    )
                ),
            ],
            [
                cell(
                    "Volume"
                ),
                cell(
                    volume_text
                ),
            ],
            [
                cell(
                    (
                        "Watertight "
                        "geometries"
                    )
                ),
                cell(
                    watertight_text
                ),
            ],
        ]

        geometry_table = Table(
            geometry_rows,
            colWidths=[
                72 * mm,
                doc.width
                - 72 * mm,
            ],
            repeatRows=1,
        )

        geometry_table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor(
                            "#EEEEEE"
                        ),
                    ),
                    (
                        "GRID",
                        (0, 0),
                        (-1, -1),
                        0.25,
                        colors.HexColor(
                            "#D9D9D9"
                        ),
                    ),
                    (
                        "VALIGN",
                        (0, 0),
                        (-1, -1),
                        "TOP",
                    ),
                    (
                        "LEFTPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "RIGHTPADDING",
                        (0, 0),
                        (-1, -1),
                        5,
                    ),
                    (
                        "TOPPADDING",
                        (0, 0),
                        (-1, -1),
                        4,
                    ),
                    (
                        "BOTTOMPADDING",
                        (0, 0),
                        (-1, -1),
                        4,
                    ),
                ]
            )
        )

        story.append(
            geometry_table
        )

        try:
            doc.build(
                story,
                onFirstPage=
                    _footer,
                onLaterPages=
                    _footer,
            )

        except Exception as exc:
            raise AppIntegrityError(
                (
                    "ReportLab could not "
                    "render the "
                    "data-extraction PDF."
                ),
                component=_COMPONENT,
                operation="render",
                context=
                    lower_error_context(
                        exc
                    ),
                cause=exc,
            ) from exc

        payload = (
            buffer.getvalue()
        )

        if not payload:
            raise AppIntegrityError(
                (
                    "Rendered "
                    "data-extraction PDF "
                    "is empty."
                ),
                component=_COMPONENT,
                operation="render",
                field="pdf",
            )

        return payload


__all__ = [
    "ReportLabDataExtractionPDFRenderer",
]
