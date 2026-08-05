#!/usr/bin/bash
# Installs the package into the active environment and exercises all three
# console scripts against a fixture database, to catch packaging/import
# regressions that unit-level checks might miss. Assumes `pip` on PATH
# points at the environment to test.
set -e

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing package from ${REPO_DIR}"
pip install --quiet "${REPO_DIR}"

echo "==> Creating fixture database and config"
python3 - "${WORKDIR}/measurements.db" <<'EOF'
import sqlite3
import sys
from datetime import datetime, timezone

con = sqlite3.connect(sys.argv[1])
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
EOF

cat > "${WORKDIR}/config.ini" <<EOF
[scale]
address = AA:BB:CC:DD:EE:FF
model = ESF-551

[storage]
db_path = ${WORKDIR}/measurements.db

[daemon]
log_level = INFO
EOF

echo "==> etekcity-scale-daemon"
etekcity-scale-daemon --version
etekcity-scale-daemon --help > /dev/null
etekcity-scale-daemon --config "${WORKDIR}/config.ini" --check-config

echo "==> etekcity-scale-report"
etekcity-scale-report --version
etekcity-scale-report --help > /dev/null
etekcity-scale-report --config "${WORKDIR}/config.ini" --output "${WORKDIR}/out.pdf"
test -s "${WORKDIR}/out.pdf"
etekcity-scale-report --config "${WORKDIR}/config.ini" --format csv --output "${WORKDIR}/out.csv"
grep -q "Date/Time" "${WORKDIR}/out.csv"

echo "==> etekcity-scale-prune"
etekcity-scale-prune --version
etekcity-scale-prune --help > /dev/null
etekcity-scale-prune --config "${WORKDIR}/config.ini" --older-than 9999 | grep -q "Would delete 0"

echo "==> Smoke test passed"
