"""Lightweight local HTTP API: fetch the latest reading or generate a report on demand.

Reads from the same SQLite database as everything else in this package --
it's a standalone read-only view onto that data, not part of the daemon's
BLE connection lifecycle, so it works whether or not the daemon is
currently running.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile

from aiohttp import web

from ._version import __version__
from .config import (
    ApiConfig,
    ConfigError,
    load_api_config,
    load_config,
    load_patient_config,
    load_report_config,
)
from .report import _resolve_range, build_csv, build_pdf, fetch_rows

_VALID_FORMATS = ("pdf", "csv")
_VALID_PERIODS = ("7d", "30d", "90d", "1y", "all")


def _latest_readings(db_path: str, address: str | None) -> list[dict[str, object]]:
    """Return the most recent reading for each scale address.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single scale's BLE address, if given.

    Returns:
        One dict per scale, each with the same fields stored in the
        database.
    """
    query = (
        "SELECT recorded_at, address, model, weight_kg, impedance_ohms, "
        "impedance_500khz_ohms, heart_rate_bpm, display_unit FROM measurements m1 "
        "WHERE recorded_at = ("
        "    SELECT MAX(recorded_at) FROM measurements m2 WHERE m2.address = m1.address"
        ")"
    )
    params: list[str] = []
    if address:
        query += " AND address = ?"
        params.append(address)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    return [
        {
            "recorded_at": row[0],
            "address": row[1],
            "model": row[2],
            "weight_kg": row[3],
            "impedance_ohms": row[4],
            "impedance_500khz_ohms": row[5],
            "heart_rate_bpm": row[6],
            "display_unit": row[7],
        }
        for row in rows
    ]


def _require_auth(request: web.Request) -> web.Response | None:
    """Return a 401 response if a token is configured and missing/wrong.

    Args:
        request: The incoming request. Reads the configured token from
            ``request.app["api_token"]``.

    Returns:
        A 401 JSON response if unauthorized, or None if the request may
        proceed.
    """
    token = request.app["api_token"]
    if not token:
        return None
    if request.headers.get("Authorization", "") != f"Bearer {token}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def handle_health(request: web.Request) -> web.Response:
    """GET /health -- unauthenticated liveness check."""
    return web.json_response({"status": "ok", "version": __version__})


async def handle_latest(request: web.Request) -> web.Response:
    """GET /latest[?address=...] -- most recent reading per scale, as JSON."""
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    readings = _latest_readings(request.app["db_path"], request.query.get("address"))
    if not readings:
        return web.json_response({"error": "no readings found"}, status=404)
    return web.json_response(readings)


async def handle_report(request: web.Request) -> web.Response:
    """GET /report[?format=pdf|csv&period=...&from=...&to=...&address=...].

    Generates a report on demand using the same config-driven settings as
    ``etekcity-scale-report`` and returns it as a file download.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    fmt = request.query.get("format", "pdf")
    if fmt not in _VALID_FORMATS:
        return web.json_response(
            {"error": f"format must be one of {_VALID_FORMATS}"}, status=400
        )

    period = request.query.get("period", "all")
    if period not in _VALID_PERIODS:
        return web.json_response(
            {"error": f"period must be one of {_VALID_PERIODS}"}, status=400
        )

    try:
        start, end = _resolve_range(
            period, request.query.get("from"), request.query.get("to")
        )
    except ValueError as exc:
        return web.json_response({"error": f"invalid date: {exc}"}, status=400)

    rows = fetch_rows(request.app["db_path"], request.query.get("address"), start, end)
    if not rows:
        return web.json_response(
            {"error": "no measurements found for the given range/filters"}, status=404
        )

    fd, temp_path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        if fmt == "csv":
            build_csv(rows, temp_path, request.app["report_config"])
            content_type = "text/csv"
        else:
            build_pdf(
                rows,
                temp_path,
                request.app["report_config"],
                request.app["patient_config"],
            )
            content_type = "application/pdf"
        with open(temp_path, "rb") as report_file:
            body = report_file.read()
    finally:
        os.remove(temp_path)

    return web.Response(
        body=body,
        content_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="measurements-report.{fmt}"'
        },
    )


def build_app(
    db_path: str, api_config: ApiConfig, report_config, patient_config
) -> web.Application:
    """Build the aiohttp application with routes and shared state attached.

    Args:
        db_path: Path to the SQLite database file.
        api_config: Supplies the auth token.
        report_config: Used for on-demand report generation.
        patient_config: Used for on-demand PDF report generation.

    Returns:
        A configured, unstarted aiohttp Application.
    """
    app = web.Application()
    app["db_path"] = db_path
    app["api_token"] = api_config.token
    app["report_config"] = report_config
    app["patient_config"] = patient_config
    app.router.add_get("/health", handle_health)
    app.router.add_get("/latest", handle_latest)
    app.router.add_get("/report", handle_report)
    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-scale-api",
        description=(
            "Lightweight local HTTP API: fetch the latest reading or "
            "generate a report on demand."
        ),
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI config file"
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code. Only returns while disabled or on a config
        error -- otherwise blocks forever serving requests.
    """
    args = _parse_args(argv)

    try:
        db_path = load_config(args.config).db_path
        api_config = load_api_config(args.config)
        report_config = load_report_config(args.config)
        patient_config = load_patient_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not api_config.enabled:
        print("API is disabled (api.enabled = no).")
        return 0

    app = build_app(db_path, api_config, report_config, patient_config)
    print(f"Listening on http://{api_config.host}:{api_config.port}")
    web.run_app(app, host=api_config.host, port=api_config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
