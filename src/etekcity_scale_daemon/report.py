"""Generate a PDF table report of scale measurements from the SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .config import load_config

_PERIOD_DAYS = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "1y": 365,
}


@dataclass
class ReportRow:
    """One measurement row as read back from the database."""

    recorded_at: datetime
    address: str
    model: str
    weight_kg: float | None
    impedance_ohms: float | None
    display_unit: str | None


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
        "display_unit FROM measurements"
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
                display_unit=row[5],
            )
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def build_pdf(rows: list[ReportRow], output_path: str) -> None:
    """Render measurement rows as a table in a PDF file.

    Args:
        rows: Measurement rows to include, oldest first.
        output_path: Filesystem path to write the PDF to.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(output_path, pagesize=letter)
    elements = [
        Paragraph("Etekcity Scale Measurement Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            f" &middot; {len(rows)} reading(s)",
            styles["Normal"],
        ),
        Spacer(1, 0.25 * inch),
    ]

    header = [
        "Date/Time (local)",
        "Address",
        "Model",
        "Weight (kg)",
        "Impedance (Ω)",
        "Unit",
    ]
    data = [header]
    for row in rows:
        local_time = row.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        data.append(
            [
                local_time,
                row.address,
                row.model,
                f"{row.weight_kg:.2f}" if row.weight_kg is not None else "-",
                f"{row.impedance_ohms:.0f}" if row.impedance_ohms is not None else "-",
                row.display_unit or "-",
            ]
        )

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
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
                ("ALIGN", (3, 1), (4, -1), "RIGHT"),
            ]
        )
    )
    elements.append(table)
    doc.build(elements)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-scale-report",
        description=(
            "Generate a PDF table report from the daemon's measurement database."
        ),
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
    if args.config:
        db_path = load_config(args.config).db_path

    start, end = _resolve_range(args.period, args.from_date, args.to_date)
    rows = fetch_rows(db_path, args.address, start, end)

    if not rows:
        print("No measurements found for the given range/filters.")
        return 1

    build_pdf(rows, args.output)
    print(f"Wrote {len(rows)} reading(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
