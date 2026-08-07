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
    DEFAULT_PATIENT_CONFIG,
    ApiConfig,
    ConfigError,
    load_api_config,
    load_config,
    load_profile_biometrics,
    load_profiles_config,
    load_report_config,
)
from .report import _resolve_range, build_csv, build_pdf, fetch_rows
from .storage import ensure_schema, set_measurement_profile

_VALID_FORMATS = ("pdf", "csv")
_VALID_PERIODS = ("7d", "30d", "90d", "1y", "all")


def _latest_readings(
    db_path: str, address: str | None, profile: str | None = None
) -> list[dict[str, object]]:
    """Return the most recent reading for each scale address.

    Args:
        db_path: Path to the SQLite database file.
        address: Restrict to a single scale's BLE address, if given.
        profile: Restrict to readings tagged with this profile name, if given.

    Returns:
        One dict per scale, each with the same fields stored in the
        database.
    """
    query = (
        "SELECT id, recorded_at, address, model, weight_kg, impedance_ohms, "
        "impedance_500khz_ohms, heart_rate_bpm, display_unit, profile FROM measurements m1 "
        "WHERE recorded_at = ("
        "    SELECT MAX(recorded_at) FROM measurements m2 WHERE m2.address = m1.address"
        ")"
    )
    params: list[str] = []
    if address:
        query += " AND address = ?"
        params.append(address)
    if profile:
        query += " AND profile = ?"
        params.append(profile)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    return [
        {
            "id": row[0],
            "recorded_at": row[1],
            "address": row[2],
            "model": row[3],
            "weight_kg": row[4],
            "impedance_ohms": row[5],
            "impedance_500khz_ohms": row[6],
            "heart_rate_bpm": row[7],
            "display_unit": row[8],
            "profile": row[9],
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
    """GET /latest[?address=...&profile=...] -- most recent reading per scale, as JSON."""
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    readings = _latest_readings(
        request.app["db_path"],
        request.query.get("address"),
        request.query.get("profile"),
    )
    if not readings:
        return web.json_response({"error": "no readings found"}, status=404)
    return web.json_response(readings)


async def handle_assign_profile(request: web.Request) -> web.Response:
    """POST /assign-profile?id=...&profile=... -- tag a reading, e.g. from an ntfy action.

    Accepts GET too, since notification action buttons (ntfy's http action
    in particular) are simplest to configure as a bare URL hit rather than
    a POST with a body.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    profiles_config = request.app["profiles_config"]
    profile = request.query.get("profile", "")
    if profile not in profiles_config.names:
        return web.json_response(
            {"error": f"profile must be one of {profiles_config.names}"}, status=400
        )

    row_id_raw = request.query.get("id", "")
    try:
        row_id = int(row_id_raw)
    except ValueError:
        return web.json_response({"error": "id must be an integer"}, status=400)

    updated = set_measurement_profile(request.app["db_path"], row_id, profile)
    if not updated:
        return web.json_response({"error": f"no reading with id {row_id}"}, status=404)
    return web.json_response({"status": "ok", "id": row_id, "profile": profile})


async def handle_report(request: web.Request) -> web.Response:
    """GET /report[?format=pdf|csv&period=...&from=...&to=...&address=...&profile=...].

    Generates a report on demand using the same config-driven settings as
    ``etekcity-scale-report`` and returns it as a file download. Biometrics
    only ever come from ``profile``'s own ``[profile.<name>]`` section --
    there's no shared fallback, since defaulting to someone else's
    biometrics would be a correctness bug, not a convenience.
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

    profile = request.query.get("profile")
    patient_config = DEFAULT_PATIENT_CONFIG
    if profile:
        try:
            patient_config = load_profile_biometrics(request.app["config_path"], profile)
        except ConfigError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    report_config = request.app["report_config"]
    if report_config.include_body_metrics and fmt != "csv":
        if not profile:
            return web.json_response(
                {
                    "error": (
                        "report.include_body_metrics is enabled but no "
                        "?profile= was given -- biometrics come from that "
                        "profile's [profile.<name>] section"
                    )
                },
                status=400,
            )
        if not patient_config.skip_body_metrics:
            missing = [
                name
                for name, value in (
                    ("height", patient_config.height_m),
                    ("birthdate", patient_config.birthdate),
                    ("sex", patient_config.sex),
                )
                if not value
            ]
            if missing:
                return web.json_response(
                    {
                        "error": (
                            f"report.include_body_metrics is enabled but "
                            f"[profile.{profile}] {', '.join(missing)} must be set"
                        )
                    },
                    status=400,
                )

    rows = fetch_rows(request.app["db_path"], request.query.get("address"), start, end, profile)
    if not rows:
        return web.json_response(
            {"error": "no measurements found for the given range/filters"}, status=404
        )

    fd, temp_path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        if fmt == "csv":
            build_csv(rows, temp_path, report_config)
            content_type = "text/csv"
        else:
            build_pdf(rows, temp_path, report_config, patient_config)
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
    config_path: str,
    db_path: str,
    api_config: ApiConfig,
    report_config,
    profiles_config,
) -> web.Application:
    """Build the aiohttp application with routes and shared state attached.

    Args:
        config_path: Path to the INI configuration file, used to load a
            specific profile's biometrics on demand.
        db_path: Path to the SQLite database file.
        api_config: Supplies the auth token.
        report_config: Used for on-demand report generation.
        profiles_config: Supplies the valid profile names for /assign-profile.

    Returns:
        A configured, unstarted aiohttp Application.
    """
    app = web.Application()
    app["config_path"] = config_path
    app["db_path"] = db_path
    app["api_token"] = api_config.token
    app["report_config"] = report_config
    app["profiles_config"] = profiles_config
    app.router.add_get("/health", handle_health)
    app.router.add_get("/latest", handle_latest)
    app.router.add_get("/report", handle_report)
    app.router.add_get("/assign-profile", handle_assign_profile)
    app.router.add_post("/assign-profile", handle_assign_profile)
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
        profiles_config = load_profiles_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not api_config.enabled:
        print("API is disabled (api.enabled = no).")
        return 0

    ensure_schema(db_path)
    app = build_app(args.config, db_path, api_config, report_config, profiles_config)
    print(f"Listening on http://{api_config.host}:{api_config.port}")
    web.run_app(app, host=api_config.host, port=api_config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
