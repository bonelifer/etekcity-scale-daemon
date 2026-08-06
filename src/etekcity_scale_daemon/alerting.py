"""Check for stale readings or large weight swings and notify via Apprise."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import apprise

from ._version import __version__
from .config import AlertConfig, ConfigError, load_alert_config, load_config

# Minimum time between repeat staleness alerts for the same scale, so a
# once-hourly check doesn't re-notify every single run while data stays old.
_STALE_ALERT_THROTTLE = timedelta(days=1)


def _load_state(state_path: str) -> dict[str, dict[str, str]]:
    """Load per-address alert state, tolerating a missing or corrupt file."""
    path = Path(state_path)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: str, state: dict[str, dict[str, str]]) -> None:
    """Persist per-address alert state, creating the parent directory if needed."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _all_addresses(db_path: str) -> list[str]:
    """Return every distinct scale address with at least one reading."""
    connection = sqlite3.connect(db_path)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT address FROM measurements"
            ).fetchall()
        ]
    finally:
        connection.close()


def _latest_two_readings(db_path: str, address: str) -> list[tuple[str, float | None]]:
    """Return up to the two most recent (recorded_at, weight_kg) rows, newest first."""
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT recorded_at, weight_kg FROM measurements WHERE address = ? "
            "ORDER BY recorded_at DESC LIMIT 2",
            (address,),
        ).fetchall()
    finally:
        connection.close()


def check_alerts(
    db_path: str, alert_config: AlertConfig, now: datetime | None = None
) -> list[str]:
    """Evaluate staleness and weight-swing conditions for every known scale.

    Reading order per address is always newest-first. A weight-swing alert
    only fires the first time a given "latest reading" is seen, so it isn't
    repeated on every subsequent run until a new reading arrives. A
    staleness alert repeats at most once per ``_STALE_ALERT_THROTTLE``
    while the condition persists.

    Args:
        db_path: Path to the SQLite database file.
        alert_config: Parsed [alerting] configuration.
        now: Current UTC time; injectable for testing. Defaults to
            ``datetime.now(timezone.utc)``.

    Returns:
        Triggered alert messages (empty if nothing was triggered). The
        caller is responsible for actually sending them.
    """
    now = now or datetime.now(timezone.utc)
    state = _load_state(alert_config.state_path)
    messages: list[str] = []

    for address in _all_addresses(db_path):
        address_state = state.get(address, {})
        rows = _latest_two_readings(db_path, address)
        if not rows:
            continue

        latest_recorded_at, latest_weight = rows[0]
        latest_dt = datetime.fromisoformat(latest_recorded_at)

        if alert_config.stale_after_days > 0:
            if now - latest_dt > timedelta(days=alert_config.stale_after_days):
                last_alert = address_state.get("last_stale_alert_at")
                last_alert_dt = datetime.fromisoformat(last_alert) if last_alert else None
                if last_alert_dt is None or now - last_alert_dt > _STALE_ALERT_THROTTLE:
                    messages.append(
                        f"No reading from {address} in over "
                        f"{alert_config.stale_after_days} day(s) "
                        f"(last: {latest_recorded_at})"
                    )
                    address_state["last_stale_alert_at"] = now.isoformat()
            else:
                address_state.pop("last_stale_alert_at", None)

        if alert_config.weight_swing_threshold_kg > 0 and len(rows) == 2:
            already_seen = address_state.get("last_seen_recorded_at") == latest_recorded_at
            previous_weight = rows[1][1]
            if (
                not already_seen
                and latest_weight is not None
                and previous_weight is not None
            ):
                diff = abs(latest_weight - previous_weight)
                if diff > alert_config.weight_swing_threshold_kg:
                    messages.append(
                        f"Weight for {address} changed by {diff:.2f} kg between "
                        f"consecutive readings ({previous_weight:.2f} kg -> "
                        f"{latest_weight:.2f} kg)"
                    )

        address_state["last_seen_recorded_at"] = latest_recorded_at
        state[address] = address_state

    _save_state(alert_config.state_path, state)
    return messages


def send_alerts(apprise_urls: list[str], messages: list[str]) -> None:
    """Send each message via Apprise to every configured notification URL.

    Args:
        apprise_urls: Apprise service URLs to notify.
        messages: One notification is sent per message.
    """
    notifier = apprise.Apprise()
    for url in apprise_urls:
        notifier.add(url)
    for message in messages:
        notifier.notify(title="Etekcity Scale Alert", body=message)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-scale-alert-check",
        description=(
            "Check for stale readings or large weight swings and notify via Apprise."
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
        Process exit code.
    """
    args = _parse_args(argv)

    try:
        db_path = load_config(args.config).db_path
        alert_config = load_alert_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not alert_config.enabled:
        print("Alerting is disabled (alerting.enabled = no).")
        return 0

    messages = check_alerts(db_path, alert_config)
    if not messages:
        print("No alerts triggered.")
        return 0

    send_alerts(alert_config.apprise_urls, messages)
    for message in messages:
        print(f"ALERT: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
