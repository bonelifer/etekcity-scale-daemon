"""Manually delete old measurements from the SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from ._version import __version__
from .config import ConfigError, load_config


def count_old_rows(db_path: str, cutoff: datetime, address: str | None) -> int:
    """Count measurements recorded before ``cutoff``.

    Args:
        db_path: Path to the SQLite database file.
        cutoff: Rows with ``recorded_at`` before this UTC datetime match.
        address: Restrict to a single scale's BLE address, if given.

    Returns:
        The number of matching rows.
    """
    query = "SELECT COUNT(*) FROM measurements WHERE recorded_at < ?"
    params: list[str] = [cutoff.isoformat()]
    if address:
        query += " AND address = ?"
        params.append(address)

    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(query, params).fetchone()[0]
    finally:
        connection.close()


def delete_old_rows(db_path: str, cutoff: datetime, address: str | None) -> int:
    """Delete measurements recorded before ``cutoff`` and reclaim disk space.

    Args:
        db_path: Path to the SQLite database file.
        cutoff: Rows with ``recorded_at`` before this UTC datetime are deleted.
        address: Restrict to a single scale's BLE address, if given.

    Returns:
        The number of rows deleted.
    """
    query = "DELETE FROM measurements WHERE recorded_at < ?"
    params: list[str] = [cutoff.isoformat()]
    if address:
        query += " AND address = ?"
        params.append(address)

    connection = sqlite3.connect(db_path)
    try:
        deleted = connection.execute(query, params).rowcount
        connection.commit()
        connection.execute("VACUUM")
        return deleted
    finally:
        connection.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-scale-prune",
        description=(
            "Delete measurements older than a given number of days. "
            "Dry-run by default -- pass --yes to actually delete."
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
        "--older-than",
        dest="older_than",
        type=int,
        required=True,
        metavar="DAYS",
        help="Delete readings older than this many days",
    )
    parser.add_argument(
        "-a", "--address", help="Restrict pruning to one scale's BLE address"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Actually delete matching rows (omit for a dry run)",
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

    if args.older_than < 0:
        print("Error: --older-than must be zero or a positive number of days")
        return 1

    db_path = args.db
    if args.config:
        try:
            db_path = load_config(args.config).db_path
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than)

    if not args.yes:
        count = count_old_rows(db_path, cutoff, args.address)
        print(
            f"Would delete {count} reading(s) recorded before "
            f"{cutoff.strftime('%Y-%m-%d %H:%M UTC')}. Re-run with --yes to delete."
        )
        return 0

    deleted = delete_old_rows(db_path, cutoff, args.address)
    print(
        f"Deleted {deleted} reading(s) recorded before "
        f"{cutoff.strftime('%Y-%m-%d %H:%M UTC')}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
