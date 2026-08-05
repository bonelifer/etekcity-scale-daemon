"""Generate a PDF table report of scale measurements from the SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ._version import __version__
from .config import (
    DEFAULT_PATIENT_CONFIG,
    DEFAULT_REPORT_CONFIG,
    ConfigError,
    PatientConfig,
    ReportConfig,
    load_config,
    load_patient_config,
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
) -> list[ReportRow]:
    """Query measurements from the database within an optional address/date range.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single scale's BLE address, if given.
        start: Inclusive UTC start of the date range, or None for no lower bound.
        end: Exclusive UTC end of the date range, or None for no upper bound.

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


def _build_full_table(rows: list[ReportRow], report_config: ReportConfig) -> Table:
    """Build the full-detail table: date/time plus whichever columns are enabled.

    Args:
        rows: Measurement rows to include, oldest first.
        report_config: Controls which columns are shown, the weight unit,
            and the date/time format.

    Returns:
        A styled reportlab Table.
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

    data = [header]
    for row in rows:
        line = [_format_datetime(row.recorded_at, report_config.date_format)]
        if report_config.include_address:
            line.append(row.address)
        if report_config.include_model:
            line.append(row.model)
        weight_value = (
            row.weight_kg * weight_factor if row.weight_kg is not None else None
        )
        line.append(f"{weight_value:.2f}" if weight_value is not None else "-")
        if report_config.include_impedance:
            line.append(
                f"{row.impedance_ohms:.0f}" if row.impedance_ohms is not None else "-"
            )
        if report_config.include_heart_rate:
            line.append(
                f"{row.heart_rate_bpm:.0f}" if row.heart_rate_bpm is not None else "-"
            )
        data.append(line)

    align_cols = [header.index(weight_header)]
    if report_config.include_impedance:
        align_cols.append(header.index("Impedance (Ω)"))
    if report_config.include_heart_rate:
        align_cols.append(header.index("Heart Rate (bpm)"))

    table = Table(data, repeatRows=1)
    table.setStyle(_table_style(align_cols))
    return table


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
            weight unit, and the date/time format.
        patient_config: Optional patient name/email to print below the
            title; fields left blank are omitted.
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
    elements.append(Spacer(1, 0.25 * inch))

    if report_config.layout == "simple":
        elements.append(_build_simple_table(rows, report_config))
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
        "-o",
        "--output",
        default="measurements-report.pdf",
        help="Output PDF file path (default: %(default)s)",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = _parse_args(argv)

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

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    rows = fetch_rows(db_path, args.address, start, end)

    if not rows:
        print("No measurements found for the given range/filters.")
        return 1

    build_pdf(rows, args.output, report_config, patient_config)
    print(f"Wrote {len(rows)} reading(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
