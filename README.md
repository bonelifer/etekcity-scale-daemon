# etekcity-scale-daemon

A standalone Linux daemon that connects to an Etekcity smart fitness scale over Bluetooth Low Energy (BLE) and logs its measurements to a local SQLite database — no cloud account, no companion app, and no Home Assistant required.

It's a thin wrapper around the [`etekcity_esf551_ble`](https://github.com/ronnnnnnnnnnnnn/etekcity_esf551_ble) library, packaged to run unattended as a `systemd` service on something like a Raspberry Pi sitting near the scale.

**Disclaimer: This is an unofficial, community-developed project. It is not affiliated with, officially maintained by, or in any way officially connected with Etekcity, VeSync Co., Ltd., or any of their subsidiaries or affiliates.**

## Supported Scales

Whatever the underlying library supports at the time of installation:

| Model | Status |
|-------|--------|
| ESF-551 | Fully supported |
| EFS-A591S (Apex HR) | Experimental (adds heart rate) |
| ESF-24 | Experimental |
| FIT-8S | Experimental |

## Features

- Scans for any supported scale on first run, then pins its BLE address and model into the config file so future restarts connect directly instead of re-scanning
- Records every measurement (weight, impedance, and heart rate where available) to a local SQLite database
- Runs as a `systemd` service with automatic restart on failure
- No body-metrics calculation or cloud sync — just raw readings, timestamped

## Installation

Requires Python 3.11+.

```bash
python3 -m venv /opt/etekcity-scale-daemon/venv
/opt/etekcity-scale-daemon/venv/bin/pip install etekcity-scale-daemon
```

### Config file

Copy the example config and edit it:

```bash
sudo mkdir -p /etc/etekcity-scale-daemon
sudo cp config/etekcity-scale-daemon.ini.example /etc/etekcity-scale-daemon/config.ini
sudo "$EDITOR" /etc/etekcity-scale-daemon/config.ini
```

Leave `[scale] address` and `model` empty to auto-discover a scale on first run — step on the scale while the daemon is scanning. Once found, the daemon writes the address and model back into this file so it reconnects directly on every future start.

| Section | Key | Description |
|---|---|---|
| `scale` | `address` | BLE MAC address of the scale. Leave empty to auto-discover. |
| `scale` | `model` | Scale model identifier. Filled in automatically after discovery. |
| `scale` | `adapter` | BLE adapter to use (Linux only), e.g. `hci0`. Leave empty for the default. |
| `scale` | `scanning_mode` | `active` or `passive` (Linux only). |
| `scale` | `cooldown_seconds` | Seconds to ignore advertisements after a disconnect (GATT-based scales only). |
| `storage` | `db_path` | Path to the SQLite database file. |
| `daemon` | `log_level` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |

### systemd service

```bash
sudo useradd --system --no-create-home --group etekcity-scale-daemon
sudo cp systemd/etekcity-scale-daemon.service /etc/systemd/system/
sudo ln -s /opt/etekcity-scale-daemon/venv/bin/etekcity-scale-daemon /usr/bin/etekcity-scale-daemon
sudo systemctl daemon-reload
sudo systemctl enable --now etekcity-scale-daemon
```

Watch the discovery step (first run) with:

```bash
sudo journalctl -u etekcity-scale-daemon -f
```

## Manual usage

```bash
etekcity-scale-daemon --config /etc/etekcity-scale-daemon/config.ini
etekcity-scale-daemon --config /etc/etekcity-scale-daemon/config.ini --verbose
```

## Database schema

Each measurement is inserted as one row into the `measurements` table:

| Column | Type | Notes |
|---|---|---|
| `recorded_at` | TEXT | ISO-8601 UTC timestamp |
| `address` | TEXT | Scale's BLE MAC address |
| `model` | TEXT | Scale model identifier |
| `weight_kg` | REAL | Weight in kilograms |
| `impedance_ohms` | REAL | Bio-impedance, if reported |
| `impedance_500khz_ohms` | REAL | ESF-24 only: 500 kHz impedance |
| `heart_rate_bpm` | REAL | EFS-A591S only |
| `display_unit` | TEXT | Scale's displayed unit at time of reading |

Query it directly with `sqlite3`, or point any BI/graphing tool at the file.

## Troubleshooting

On Raspberry Pi (and other BlueZ-based Linux systems), a `org.bluez.Error.InProgress` error usually clears up with:

```
bluetoothctl power off
bluetoothctl power on
bluetoothctl scan on
```

## Acknowledgments

- Scale hardware designed and sold by [Etekcity](https://www.etekcity.com) / [VeSync Co., Ltd.](https://www.vesync.com) — see the Disclaimer above.
- Built on [`etekcity_esf551_ble`](https://github.com/ronnnnnnnnnnnnn/etekcity_esf551_ble) by maintainer [@ronnnnnnnnnnnnn](https://github.com/ronnnnnnnnnnnnn), which does all the BLE protocol and reverse-engineering work.
- Code review, bug fixes, and documentation assisted by [Claude](https://www.anthropic.com/claude).

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/bonelifer/etekcity-scale-daemon/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/bonelifer/etekcity-scale-daemon/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
