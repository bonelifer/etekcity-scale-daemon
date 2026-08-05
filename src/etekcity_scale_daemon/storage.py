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


class MeasurementStore:
    """Persists scale measurements to a local SQLite database.

    Args:
        db_path: Filesystem path to the SQLite database file. Parent
            directories are created automatically if missing.
    """

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(db_path)
        self._connection.execute(_SCHEMA)
        self._connection.commit()

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
    ) -> None:
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
        """
        self._connection.execute(
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

    def close(self) -> None:
        """Close the underlying database connection."""
        self._connection.close()
