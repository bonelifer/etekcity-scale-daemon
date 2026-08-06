"""Generate a PDF table report of scale measurements from the SQLite database."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from etekcity_esf551_ble import BodyMetrics, Sex, calc_age
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ._version import __version__
from .config import (
    DEFAULT_PATIENT_CONFIG,
    DEFAULT_REPORT_CONFIG,
    ConfigError,
    PatientConfig,
    ReportConfig,
    load_config,
    load_patient_config,
    load_profile_biometrics,
    load_report_config,
)

_PERIOD_DAYS = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "1y": 365,
}

# kg -> (conversion factor, unit label)
_WEIGHT_CONVERSIONS = {
    "kg": (1.0, "kg"),
    "lb": (2.2046226218487757, "lb"),
    "st": (0.15747304441776975, "st"),
}

_PAGE_SIZES = {
    "letter": letter,
    "a4": A4,
}

# Date/time strftime patterns for each date_format preset.
_DATE_TIME_FORMATS = {
    "us": "%m/%d/%Y %I:%M:%S %p",
    "world": "%d/%m/%Y %H:%M:%S",
}

# Number of side-by-side Date/Weight column pairs in the "simple" layout.
_SIMPLE_LAYOUT_COLUMN_PAIRS = 3

# Maximum number of x-axis date labels to show on the "chart" layout before
# thinning them out, so labels don't overlap when there are many readings.
_CHART_MAX_LABELS = 10

# BodyMetrics.as_dict() key -> (display label, formatting kind).
_BODY_METRICS_FIELDS = [
    ("body_mass_index", "BMI", "bmi"),
    ("body_fat_percentage", "Body Fat", "percent"),
    ("fat_free_weight", "Fat-Free Weight", "weight"),
    ("subcutaneous_fat_percentage", "Subcutaneous Fat", "percent"),
    ("visceral_fat_value", "Visceral Fat", "plain"),
    ("body_water_percentage", "Body Water", "percent"),
    ("basal_metabolic_rate", "Basal Metabolic Rate", "calories"),
    ("skeletal_muscle_percentage", "Skeletal Muscle", "percent"),
    ("muscle_mass", "Muscle Mass", "weight"),
    ("bone_mass", "Bone Mass", "weight"),
    ("protein_percentage", "Protein", "percent"),
    ("weight_score", "Weight Score", "score"),
    ("fat_score", "Fat Score", "score"),
    ("bmi_score", "BMI Score", "score"),
    ("health_score", "Health Score", "score"),
    ("metabolic_age", "Metabolic Age", "years"),
]


def _format_datetime(recorded_at: datetime, date_format: str) -> str:
    """Format a UTC timestamp in local time using the given date_format preset.

    Args:
        recorded_at: A timezone-aware UTC datetime.
        date_format: "us" (MM/DD/YYYY, 12-hour) or "world" (DD/MM/YYYY, 24-hour).

    Returns:
        The formatted local date/time string.
    """
    return recorded_at.astimezone().strftime(_DATE_TIME_FORMATS[date_format])


@dataclass
class ReportRow:
    """One measurement row as read back from the database."""

    recorded_at: datetime
    address: str
    model: str
    weight_kg: float | None
    impedance_ohms: float | None
    heart_rate_bpm: float | None


def _resolve_range(
    period: str, from_date: str | None, to_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve the requested period/from/to options into a UTC datetime range.

    Args:
        period: One of "7d", "30d", "90d", "1y", "all".
        from_date: Explicit start date (YYYY-MM-DD), overrides ``period``.
        to_date: Explicit end date (YYYY-MM-DD), inclusive. Defaults to now
            if omitted while ``from_date`` is set.

    Returns:
        A ``(start, end)`` tuple of timezone-aware UTC datetimes. Both are
        None when the range is unbounded ("all" with no explicit dates).
    """
    if from_date:
        start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = (
            datetime.strptime(to_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            + timedelta(days=1)
            if to_date
            else datetime.now(timezone.utc)
        )
        return start, end

    if period == "all":
        return None, None

    days = _PERIOD_DAYS[period]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def fetch_rows(
    db_path: str,
    address: str | None,
    start: datetime | None,
    end: datetime | None,
    profile: str | None = None,
) -> list[ReportRow]:
    """Query measurements from the database within an optional address/date range.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single scale's BLE address, if given.
        start: Inclusive UTC start of the date range, or None for no lower bound.
        end: Exclusive UTC end of the date range, or None for no upper bound.
        profile: Restrict to readings tagged with this profile name, if given.

    Returns:
        Matching rows ordered oldest first.
    """
    query = (
        "SELECT recorded_at, address, model, weight_kg, impedance_ohms, "
        "heart_rate_bpm FROM measurements"
    )
    clauses: list[str] = []
    params: list[str] = []

    if address:
        clauses.append("address = ?")
        params.append(address)
    if profile:
        clauses.append("profile = ?")
        params.append(profile)
    if start is not None:
        clauses.append("recorded_at >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("recorded_at < ?")
        params.append(end.isoformat())

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY recorded_at ASC"

    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(query, params)
        return [
            ReportRow(
                recorded_at=datetime.fromisoformat(row[0]),
                address=row[1],
                model=row[2],
                weight_kg=row[3],
                impedance_ohms=row[4],
                heart_rate_bpm=row[5],
            )
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def fetch_addresses(
    db_path: str, start: datetime | None, end: datetime | None
) -> list[str]:
    """Return distinct scale addresses with at least one reading in range.

    Args:
        db_path: Path to the SQLite database file.
        start: Inclusive UTC start of the date range, or None for no lower bound.
        end: Exclusive UTC end of the date range, or None for no upper bound.

    Returns:
        Distinct addresses, ordered by their earliest reading in range.
    """
    query = "SELECT address FROM measurements"
    clauses: list[str] = []
    params: list[str] = []

    if start is not None:
        clauses.append("recorded_at >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("recorded_at < ?")
        params.append(end.isoformat())

    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " GROUP BY address ORDER BY MIN(recorded_at) ASC"

    connection = sqlite3.connect(db_path)
    try:
        return [row[0] for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def _table_style(align_cols: list[int]) -> TableStyle:
    """Build the shared header/grid/zebra-stripe style for a report table.

    Args:
        align_cols: Column indices to right-align (numeric columns).

    Returns:
        A TableStyle applying header styling, a grid, zebra-striped rows,
        and right-alignment of the given columns.
    """
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        (
            "ROWBACKGROUNDS",
            (0, 1),
            (-1, -1),
            [colors.white, colors.HexColor("#f0f0f0")],
        ),
    ]
    style_commands.extend(("ALIGN", (idx, 1), (idx, -1), "RIGHT") for idx in align_cols)
    return TableStyle(style_commands)


@dataclass
class _FullColumns:
    """Header and raw (unformatted) data shared by the full table and CSV export."""

    header: list[str]
    rows: list[list[object]]  # str for text columns; float | None for numeric ones


def _full_columns(rows: list[ReportRow], report_config: ReportConfig) -> _FullColumns:
    """Build the header and raw values for whichever columns are enabled.

    Numeric columns (weight, impedance, heart rate) are left as ``float |
    None`` rather than formatted strings, so PDF rendering (dashes for
    missing values, fixed decimals) and CSV export (blank for missing
    values) can each format them appropriately.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Controls which columns are shown, the weight unit,
            and the date/time format.

    Returns:
        The shared header and per-row values.
    """
    weight_factor, weight_label = _WEIGHT_CONVERSIONS[report_config.weight_unit]
    weight_header = f"Weight ({weight_label})"

    header = ["Date/Time (local)"]
    if report_config.include_address:
        header.append("Address")
    if report_config.include_model:
        header.append("Model")
    header.append(weight_header)
    if report_config.include_impedance:
        header.append("Impedance (Ω)")
    if report_config.include_heart_rate:
        header.append("Heart Rate (bpm)")

    data: list[list[object]] = []
    for row in rows:
        line: list[object] = [_format_datetime(row.recorded_at, report_config.date_format)]
        if report_config.include_address:
            line.append(row.address)
        if report_config.include_model:
            line.append(row.model)
        line.append(row.weight_kg * weight_factor if row.weight_kg is not None else None)
        if report_config.include_impedance:
            line.append(row.impedance_ohms)
        if report_config.include_heart_rate:
            line.append(row.heart_rate_bpm)
        data.append(line)

    return _FullColumns(header=header, rows=data)


def _build_full_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the full-detail table: date/time plus whichever columns are enabled.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Controls which columns are shown, the weight unit,
            and the date/time format.

    Returns:
        A styled reportlab Table.
    """
    columns = _full_columns(rows, report_config)
    header = columns.header
    weight_idx = header.index(f"Weight ({_WEIGHT_CONVERSIONS[report_config.weight_unit][1]})")
    impedance_idx = header.index("Impedance (Ω)") if report_config.include_impedance else None
    heart_rate_idx = (
        header.index("Heart Rate (bpm)") if report_config.include_heart_rate else None
    )

    data = [header]
    for line in columns.rows:
        formatted = list(line)
        formatted[weight_idx] = (
            f"{line[weight_idx]:.2f}" if line[weight_idx] is not None else "-"
        )
        if impedance_idx is not None:
            formatted[impedance_idx] = (
                f"{line[impedance_idx]:.0f}" if line[impedance_idx] is not None else "-"
            )
        if heart_rate_idx is not None:
            formatted[heart_rate_idx] = (
                f"{line[heart_rate_idx]:.0f}" if line[heart_rate_idx] is not None else "-"
            )
        data.append(formatted)

    align_cols = [weight_idx]
    if impedance_idx is not None:
        align_cols.append(impedance_idx)
    if heart_rate_idx is not None:
        align_cols.append(heart_rate_idx)

    table = Table(data, repeatRows=1)
    table.setStyle(_table_style(align_cols))
    return table


def build_csv(
    rows: list[ReportRow],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
) -> None:
    """Write measurement rows as CSV.

    Only the column toggles, weight unit, and date/time format apply;
    layout, page size, the summary line, and patient info are PDF-only
    concerns and have no effect here.

    Args:
        rows: Measurement rows to include, oldest first.
        output_path: Filesystem path to write the CSV to.
        report_config: Controls which columns are shown, the weight unit,
            and the date/time format.
    """
    columns = _full_columns(rows, report_config)
    header = columns.header
    weight_idx = header.index(f"Weight ({_WEIGHT_CONVERSIONS[report_config.weight_unit][1]})")
    impedance_idx = header.index("Impedance (Ω)") if report_config.include_impedance else None
    heart_rate_idx = (
        header.index("Heart Rate (bpm)") if report_config.include_heart_rate else None
    )

    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        for line in columns.rows:
            formatted = list(line)
            formatted[weight_idx] = (
                f"{line[weight_idx]:.2f}" if line[weight_idx] is not None else ""
            )
            if impedance_idx is not None:
                formatted[impedance_idx] = (
                    f"{line[impedance_idx]:.0f}" if line[impedance_idx] is not None else ""
                )
            if heart_rate_idx is not None:
                formatted[heart_rate_idx] = (
                    f"{line[heart_rate_idx]:.0f}" if line[heart_rate_idx] is not None else ""
                )
            writer.writerow(formatted)


def _build_simple_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the simple layout: date/weight only, in side-by-side column pairs.

    Readings fill one Date/Weight column pair top-to-bottom before moving to
    the next pair, so a full page of readings doesn't leave most of the page
    width empty the way a single narrow two-column table would.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Controls the weight unit and date/time format.

    Returns:
        A styled reportlab Table.
    """
    weight_factor, weight_label = _WEIGHT_CONVERSIONS[report_config.weight_unit]
    pairs = min(_SIMPLE_LAYOUT_COLUMN_PAIRS, len(rows))
    rows_per_column = -(-len(rows) // pairs)  # ceil division

    header = ["Date/Time (local)", f"Weight ({weight_label})"] * pairs
    data = [header]
    for r in range(rows_per_column):
        line: list[str] = []
        for p in range(pairs):
            idx = p * rows_per_column + r
            if idx < len(rows):
                row = rows[idx]
                weight_value = (
                    row.weight_kg * weight_factor if row.weight_kg is not None else None
                )
                line.append(_format_datetime(row.recorded_at, report_config.date_format))
                line.append(f"{weight_value:.2f}" if weight_value is not None else "-")
            else:
                line.extend(["", ""])
        data.append(line)

    align_cols = [i for i in range(len(header)) if i % 2 == 1]
    table = Table(data, repeatRows=1)
    table.setStyle(_table_style(align_cols))
    return table


def _build_chart(rows: list[ReportRow], report_config: ReportConfig) -> Drawing:
    """Build a line chart of weight over time.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Supplies the weight unit and date/time format.

    Returns:
        A reportlab Drawing containing the chart, or just a "not enough
        data" note if fewer than two readings have a weight value.
    """
    weight_factor, weight_label = _WEIGHT_CONVERSIONS[report_config.weight_unit]
    points = [
        (row.recorded_at, row.weight_kg * weight_factor)
        for row in rows
        if row.weight_kg is not None
    ]

    drawing = Drawing(480, 260)

    if len(points) < 2:
        drawing.add(String(10, 130, "Not enough weight data to plot a chart."))
        return drawing

    values = [point[1] for point in points]
    date_pattern = "%m/%d" if report_config.date_format == "us" else "%d/%m"
    date_labels = [point[0].astimezone().strftime(date_pattern) for point in points]

    step = max(1, len(date_labels) // _CHART_MAX_LABELS)
    thinned_labels = [
        label if i % step == 0 else "" for i, label in enumerate(date_labels)
    ]

    chart = HorizontalLineChart()
    chart.x = 50
    chart.y = 40
    chart.width = 400
    chart.height = 180
    chart.data = [values]
    chart.categoryAxis.categoryNames = thinned_labels
    chart.categoryAxis.labels.angle = 30
    chart.categoryAxis.labels.dx = -8
    chart.categoryAxis.labels.dy = -10
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = min(values) - 1
    chart.valueAxis.valueMax = max(values) + 1
    chart.lines[0].strokeColor = colors.HexColor("#2f5d8a")
    chart.lines[0].strokeWidth = 1.5

    drawing.add(chart)
    drawing.add(
        String(
            chart.x,
            chart.y + chart.height + 15,
            f"Weight ({weight_label}) over time",
            fontName="Helvetica-Bold",
            fontSize=10,
        )
    )
    return drawing


def _summary_line(rows: list[ReportRow], report_config: ReportConfig) -> str | None:
    """Build a min/max/average/net-change summary line for the Weight column.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Supplies the weight unit to render values in.

    Returns:
        The summary text, or None if no row has a weight value.
    """
    weight_factor, weight_label = _WEIGHT_CONVERSIONS[report_config.weight_unit]
    values = [
        row.weight_kg * weight_factor for row in rows if row.weight_kg is not None
    ]
    if not values:
        return None

    net_change = values[-1] - values[0]
    return (
        f"Weight summary: min {min(values):.2f} {weight_label} &middot; "
        f"max {max(values):.2f} {weight_label} &middot; "
        f"avg {sum(values) / len(values):.2f} {weight_label} &middot; "
        f"net change {net_change:+.2f} {weight_label} (oldest &rarr; newest)"
    )


def _format_body_metric(kind: str, value: float, weight_factor: float, weight_label: str) -> str:
    """Format one BodyMetrics value for display, based on its unit kind."""
    if kind == "weight":
        return f"{value * weight_factor:.2f} {weight_label}"
    if kind == "percent":
        return f"{value:.1f}%"
    if kind == "calories":
        return f"{value:.0f} cal"
    if kind == "score":
        return f"{value:.0f}/100"
    if kind == "years":
        return f"{value:.0f} years"
    if kind == "bmi":
        return f"{value:.1f}"
    return str(value)


def _build_body_metrics_elements(
    rows: list[ReportRow],
    report_config: ReportConfig,
    patient_config: PatientConfig,
    styles,
) -> list:
    """Build a "Body Composition" heading and table for the latest reading.

    Uses the most recent row with both a weight and impedance value (older
    rows are ignored -- this is a current snapshot, not a per-reading
    history). Requires ``patient_config.height_m``/``birthdate``/``sex`` to
    already be validated as set by the caller.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Supplies the weight unit to render weight-based
            metrics in.
        patient_config: Supplies height, birthdate, sex, and athlete status.
        styles: The document's reportlab stylesheet.

    Returns:
        Flowables to append: either a heading + metrics table, or a single
        note paragraph if no row has both weight and impedance data.
    """
    latest = next(
        (
            row
            for row in reversed(rows)
            if row.weight_kg is not None and row.impedance_ohms is not None
        ),
        None,
    )
    if latest is None:
        return [
            Paragraph(
                "Body composition: no reading with impedance data available "
                "in this report's range.",
                styles["Normal"],
            ),
            Spacer(1, 0.15 * inch),
        ]

    weight_factor, weight_label = _WEIGHT_CONVERSIONS[report_config.weight_unit]
    metrics = BodyMetrics(
        weight_kg=latest.weight_kg,
        height_m=patient_config.height_m,
        age=calc_age(patient_config.birthdate),
        sex=Sex.Male if patient_config.sex == "male" else Sex.Female,
        impedance=latest.impedance_ohms,
        athlete=patient_config.athlete,
    ).as_dict()

    data = [["Metric", "Value"]]
    data.extend(
        [label, _format_body_metric(kind, metrics[key], weight_factor, weight_label)]
        for key, label, kind in _BODY_METRICS_FIELDS
    )

    table = Table(data, colWidths=[220, 120])
    table.setStyle(_table_style([1]))

    date_label = _format_datetime(latest.recorded_at, report_config.date_format)
    return [
        Paragraph(f"Body Composition (as of {date_label})", styles["Heading2"]),
        Spacer(1, 0.05 * inch),
        table,
        Spacer(1, 0.15 * inch),
    ]


def build_pdf(
    rows: list[ReportRow],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    patient_config: PatientConfig = DEFAULT_PATIENT_CONFIG,
) -> None:
    """Render measurement rows as a table in a PDF file.

    Args:
        rows: Measurement rows to include, oldest first.
        output_path: Filesystem path to write the PDF to.
        report_config: Controls the layout, which columns are shown, the
            weight unit, the date/time format, the page size, whether a
            min/max/average/net-change summary line is printed, and whether
            a body composition snapshot is included.
        patient_config: Optional patient name/email to print below the
            title; fields left blank are omitted. Height/birthdate/sex are
            required (validated by the caller) if
            ``report_config.include_body_metrics`` is set.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=_PAGE_SIZES[report_config.page_size])
    elements = [
        Paragraph("Etekcity Scale Measurement Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {len(rows)} reading(s)",
            styles["Normal"],
        ),
    ]
    if patient_config.name:
        elements.append(Paragraph(f"Patient: {escape(patient_config.name)}", styles["Normal"]))
    if patient_config.email:
        elements.append(Paragraph(f"Email: {escape(patient_config.email)}", styles["Normal"]))
    if report_config.include_summary:
        summary = _summary_line(rows, report_config)
        if summary:
            elements.append(Paragraph(summary, styles["Normal"]))
    elements.append(Spacer(1, 0.25 * inch))

    if report_config.include_body_metrics:
        elements.extend(
            _build_body_metrics_elements(rows, report_config, patient_config, styles)
        )

    if report_config.layout == "simple":
        elements.append(_build_simple_table(rows, report_config))
    elif report_config.layout == "chart":
        elements.append(_build_chart(rows, report_config))
    else:
        elements.append(_build_full_table(rows, report_config))

    doc.build(elements)


def build_multi_scale_pdf(
    sections: list[tuple[str, list[ReportRow]]],
    output_path: str,
    report_config: ReportConfig = DEFAULT_REPORT_CONFIG,
    patient_config: PatientConfig = DEFAULT_PATIENT_CONFIG,
) -> None:
    """Render one PDF with a separate section (own table/chart) per scale.

    Args:
        sections: (address, rows) pairs, one per scale, each already
            filtered to that address and sorted oldest-first. Every list
            of rows must be non-empty.
        output_path: Filesystem path to write the PDF to.
        report_config: Controls the layout, which columns are shown, the
            weight unit, the date/time format, the page size, and whether
            a min/max/average/net-change summary line is printed per section.
        patient_config: Optional patient name/email to print below the
            title; fields left blank are omitted.
    """
    total_rows = sum(len(rows) for _, rows in sections)
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=_PAGE_SIZES[report_config.page_size])
    elements = [
        Paragraph("Etekcity Scale Measurement Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {total_rows} reading(s) across {len(sections)} scale(s)",
            styles["Normal"],
        ),
    ]
    if patient_config.name:
        elements.append(Paragraph(f"Patient: {escape(patient_config.name)}", styles["Normal"]))
    if patient_config.email:
        elements.append(Paragraph(f"Email: {escape(patient_config.email)}", styles["Normal"]))
    elements.append(Spacer(1, 0.25 * inch))

    for index, (address, rows) in enumerate(sections):
        if index > 0:
            elements.append(PageBreak())
        elements.append(
            Paragraph(f"Scale: {escape(address)} ({escape(rows[0].model)})", styles["Heading2"])
        )
        elements.append(Spacer(1, 0.1 * inch))
        if report_config.include_summary:
            summary = _summary_line(rows, report_config)
            if summary:
                elements.append(Paragraph(summary, styles["Normal"]))
                elements.append(Spacer(1, 0.1 * inch))

        if report_config.layout == "simple":
            elements.append(_build_simple_table(rows, report_config))
        elif report_config.layout == "chart":
            elements.append(_build_chart(rows, report_config))
        else:
            elements.append(_build_full_table(rows, report_config))

    doc.build(elements)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-scale-report",
        description=(
            "Generate a PDF table report from the daemon's measurement database."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-c",
        "--config",
        help="Path to the daemon's INI config file (reads db_path from it)",
    )
    source.add_argument(
        "-d",
        "--db",
        help="Path to the SQLite database file, bypassing the config file",
    )
    parser.add_argument(
        "-F",
        "--format",
        choices=["pdf", "csv"],
        default="pdf",
        help="Output format (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path (default: measurements-report.<format>)",
    )
    parser.add_argument(
        "-p",
        "--period",
        choices=["7d", "30d", "90d", "1y", "all"],
        default="all",
        help="Preset date range (default: %(default)s)",
    )
    parser.add_argument(
        "-f",
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        help="Explicit start date, overrides --period",
    )
    parser.add_argument(
        "-t",
        "--to",
        dest="to_date",
        metavar="YYYY-MM-DD",
        help="Explicit end date (inclusive), defaults to now",
    )
    parser.add_argument(
        "-a", "--address", help="Restrict the report to one scale's BLE address"
    )
    parser.add_argument(
        "-m",
        "--multi-scale",
        action="store_true",
        help=(
            "One PDF with a separate section per scale address, instead of "
            "mixing every scale into one table (PDF only; mutually exclusive "
            "with --address, ignored for --format csv)"
        ),
    )
    parser.add_argument(
        "-P",
        "--profile",
        help=(
            "Restrict to readings tagged with this profile name (requires "
            "--config); with report.include_body_metrics, also switches body "
            "metrics to that profile's [profile.<name>] biometrics instead "
            "of [patient]"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)

    if args.multi_scale and args.address:
        print("Error: --multi-scale and --address are mutually exclusive")
        return 1

    if args.profile and not args.config:
        print("Error: --profile requires --config (profile biometrics live in the config file)")
        return 1

    db_path = args.db
    report_config = DEFAULT_REPORT_CONFIG
    patient_config = DEFAULT_PATIENT_CONFIG
    if args.config:
        try:
            db_path = load_config(args.config).db_path
            report_config = load_report_config(args.config)
            patient_config = load_patient_config(args.config)
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    # --profile swaps in that profile's own biometrics (and name, for the
    # "Patient: ..." line) instead of the global [patient] section -- never
    # falls back silently, since defaulting to someone else's height/sex
    # would be a correctness bug, not a convenience.
    effective_patient_config = patient_config
    biometrics_section = "patient"
    if args.profile:
        try:
            effective_patient_config = load_profile_biometrics(args.config, args.profile)
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1
        biometrics_section = f"profile.{args.profile}"

    # Body metrics only render on the single-scale PDF path (not CSV, not
    # --multi-scale, which has no single patient profile to apply) -- only
    # require the fields it needs there, so e.g. a CSV export isn't blocked
    # by patient info it will never use.
    renders_body_metrics = (
        report_config.include_body_metrics
        and args.format != "csv"
        and not args.multi_scale
    )
    if renders_body_metrics:
        missing = [
            name
            for name, value in (
                ("height_m", effective_patient_config.height_m),
                ("birthdate", effective_patient_config.birthdate),
                ("sex", effective_patient_config.sex),
            )
            if not value
        ]
        if missing:
            print(
                "Error: report.include_body_metrics is enabled but "
                f"[{biometrics_section}] {', '.join(missing)} must be set"
            )
            return 1

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    output = args.output or f"measurements-report.{args.format}"

    if args.multi_scale and args.format != "csv":
        addresses = fetch_addresses(db_path, start, end)
        sections = [(a, fetch_rows(db_path, a, start, end)) for a in addresses]
        if not sections:
            print("No measurements found for the given range/filters.")
            return 1
        build_multi_scale_pdf(sections, output, report_config, patient_config)
        total_rows = sum(len(rows) for _, rows in sections)
        print(f"Wrote {total_rows} reading(s) across {len(sections)} scale(s) to {output}")
        return 0

    rows = fetch_rows(db_path, args.address, start, end, args.profile)

    if not rows:
        print("No measurements found for the given range/filters.")
        return 1

    if args.format == "csv":
        build_csv(rows, output, report_config)
    else:
        build_pdf(rows, output, report_config, effective_patient_config)
    print(f"Wrote {len(rows)} reading(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
