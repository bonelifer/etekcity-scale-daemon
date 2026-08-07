"""SQLite storage backend for scale measurements."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    address TEXT NOT NULL,
    model TEXT NOT NULL,
    weight_kg REAL,
    impedance_ohms REAL,
    impedance_500khz_ohms REAL,
    heart_rate_bpm REAL,
    display_unit TEXT
);
"""


def _ensure_profile_column(connection: sqlite3.Connection) -> None:
    """Add the ``profile`` column if it's missing (migrates pre-profiles databases).

    Args:
        connection: An open connection with the ``measurements`` table
            already created.
    """
    columns = {row[1] for row in connection.execute("PRAGMA table_info(measurements)")}
    if "profile" not in columns:
        connection.execute("ALTER TABLE measurements ADD COLUMN profile TEXT")
        connection.commit()


def ensure_schema(db_path: str) -> None:
    """Create the measurements table and apply migrations if needed.

    Safe to call from any entry point (daemon, API server, etc.) regardless
    of whether the database file already exists or which one touches it
    first.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(_SCHEMA)
        connection.commit()
        _ensure_profile_column(connection)
    finally:
        connection.close()


def get_measurement_recorded_at(db_path: str, row_id: int) -> str | None:
    """Look up a measurement's recorded_at timestamp, without modifying it.

    Args:
        db_path: Filesystem path to the SQLite database file.
        row_id: The measurement's primary key, as returned by ``record()``.

    Returns:
        The stored ISO-8601 ``recorded_at`` string, or None if no row
        matches ``row_id``.
    """
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT recorded_at FROM measurements WHERE id = ?", (row_id,)
        ).fetchone()
        return row[0] if row is not None else None
    finally:
        connection.close()


def set_measurement_profile(db_path: str, row_id: int, profile: str) -> bool:
    """Tag a previously recorded measurement with a profile name.

    Args:
        db_path: Filesystem path to the SQLite database file.
        row_id: The measurement's primary key, as returned by ``record()``.
        profile: The profile name to assign.

    Returns:
        True if a row was updated, False if no row matched ``row_id``.
    """
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(
            "UPDATE measurements SET profile = ? WHERE id = ?", (profile, row_id)
        )
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


class MeasurementStore:
    """Persists scale measurements to a local SQLite database.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """

    def __init__(self, db_path: str) -> None:
        ensure_schema(db_path)
        self._connection = sqlite3.connect(db_path)

    def record(
        self,
        recorded_at: str,
        address: str,
        model: str,
        weight_kg: float | None,
        impedance_ohms: float | None,
        impedance_500khz_ohms: float | None,
        heart_rate_bpm: float | None,
        display_unit: str | None,
    ) -> int:
        """Insert one measurement row.

        Args:
            recorded_at: ISO-8601 UTC timestamp of the measurement.
            address: BLE MAC address of the scale that produced it.
            model: Scale model identifier (``ScaleModel.value``).
            weight_kg: Weight in kilograms, if reported.
            impedance_ohms: Bio-impedance in ohms, if reported.
            impedance_500khz_ohms: ESF-24 500 kHz impedance, if reported.
            heart_rate_bpm: Heart rate in bpm, if reported (EFS-A591S only).
            display_unit: Name of the scale's current display unit.

        Returns:
            The inserted row's primary key, usable with
            ``set_measurement_profile()`` to tag it later.
        """
        cursor = self._connection.execute(
            """
            INSERT INTO measurements (
                recorded_at, address, model, weight_kg, impedance_ohms,
                impedance_500khz_ohms, heart_rate_bpm, display_unit
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recorded_at,
                address,
                model,
                weight_kg,
                impedance_ohms,
                impedance_500khz_ohms,
                heart_rate_bpm,
                display_unit,
            ),
        )
        self._connection.commit()
        return cursor.lastrowid

    def close(self) -> None:
        """Close the underlying database connection."""
        self._connection.close()
