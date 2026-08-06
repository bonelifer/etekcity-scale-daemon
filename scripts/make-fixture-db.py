#!/usr/bin/env python3
"""Create a tiny fixture SQLite database for smoke/CI testing."""

import sqlite3
import sys
from datetime import datetime, timezone


def main() -> None:
    db_path = sys.argv[1]
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE measurements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            address TEXT NOT NULL,
            model TEXT NOT NULL,
            weight_kg REAL,
            impedance_ohms REAL,
            impedance_500khz_ohms REAL,
            heart_rate_bpm REAL,
            display_unit TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO measurements "
        "(recorded_at, address, model, weight_kg, impedance_ohms, display_unit) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), "AA:BB:CC:DD:EE:FF", "ESF-551", 80.0, 500.0, "KG"),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
