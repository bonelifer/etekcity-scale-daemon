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

### Quick install

```bash
git clone https://github.com/bonelifer/etekcity-scale-daemon.git
cd etekcity-scale-daemon
sudo ./install.sh
```

This creates a venv at `/opt/etekcity-scale-daemon`, installs the package from the checkout, seeds `/etc/etekcity-scale-daemon/config.ini` (if it doesn't already exist), creates an `etekcity-scale-daemon` system user, and installs and enables the systemd service. It's safe to re-run — it skips steps that are already done. Edit the config and `sudo systemctl restart etekcity-scale-daemon` afterward.

### Manual install

If you'd rather do it by hand or want to customize a step:

```bash
python3 -m venv /opt/etekcity-scale-daemon/venv
/opt/etekcity-scale-daemon/venv/bin/pip install /path/to/etekcity-scale-daemon  # this checkout
```

#### Config file

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
| `report` | `layout` | PDF layout: `full` (one row per reading) or `simple` (date/weight only, in side-by-side columns). |
| `report` | `include_address` | Show the Address column in the `full` layout: `yes` or `no`. |
| `report` | `include_model` | Show the Model column in the `full` layout: `yes` or `no`. |
| `report` | `include_impedance` | Show the Impedance column in the `full` layout: `yes` or `no`. |
| `report` | `include_heart_rate` | Show the Heart Rate column in the `full` layout: `yes` or `no`. Only EFS-A591S reports heart rate; defaults to `no`. |
| `report` | `weight_unit` | Unit to render the Weight column in: `kg`, `lb`, or `st`. |
| `report` | `date_format` | `us` (MM/DD/YYYY, 12-hour) or `world` (DD/MM/YYYY, 24-hour). |
| `report` | `page_size` | PDF page size: `letter` or `a4`. |
| `report` | `include_summary` | Print a min/max/average/net-change summary line for Weight below the title: `yes` or `no`. Defaults to `no`. |
| `patient` | `name` | Patient name printed below the title in PDF reports. Leave blank to omit. |
| `patient` | `email` | Patient email printed below the title in PDF reports. Leave blank to omit. |

#### systemd service

```bash
sudo useradd --system --no-create-home --group etekcity-scale-daemon
sudo cp systemd/etekcity-scale-daemon.service /etc/systemd/system/
sudo ln -s /opt/etekcity-scale-daemon/venv/bin/etekcity-scale-daemon /usr/bin/etekcity-scale-daemon
sudo ln -s /opt/etekcity-scale-daemon/venv/bin/etekcity-scale-report /usr/bin/etekcity-scale-report
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

Validate a config file (all sections) without starting the daemon:

```bash
etekcity-scale-daemon --config /etc/etekcity-scale-daemon/config.ini --check-config
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

## Reports

`etekcity-scale-report` reads the database and writes a table of readings to a PDF or CSV file:

```bash
# Every reading on record
etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --output report.pdf

# Preset ranges: 7d, 30d, 90d, 1y, all (default: all)
etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --period 30d --output last-30-days.pdf

# Explicit date range (--to defaults to now if omitted)
etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --from 2026-01-01 --to 2026-03-31 --output q1.pdf

# Point directly at a database file instead of a config
etekcity-scale-report --db /var/lib/etekcity-scale-daemon/measurements.db --output report.pdf
```

Add `--address AA:BB:CC:DD:EE:FF` to restrict the report to one scale if the database has readings from more than one.

Add `--format csv` for a CSV file instead of a PDF (default output path becomes `measurements-report.csv`):

```bash
etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --format csv --output report.csv
```

CSV export always uses the `full` layout's column set (respecting `include_address`/`include_model`/`include_impedance`/`include_heart_rate`, `weight_unit`, and `date_format`) — `layout`, `page_size`, `include_summary`, and `[patient]` are PDF-only and have no effect on CSV.

The layout, which columns appear, the weight unit, and the date/time format are all controlled by the `[report]` section of the config file (see the table above) — `--config` reads them, `--db` always uses the defaults (`full` layout, all columns, kilograms, `world` date format).

The `simple` layout drops every column except Date/Time and Weight and lays readings out in several side-by-side column pairs (filling one pair top-to-bottom before starting the next) instead of a single narrow two-column table.

See [samples/](samples/) for a rendered PDF of every layout/unit/date-format combination.

Set `[patient] name` and/or `email` (only read from `--config`, not `--db`) to print that identifying info below the title — handy when handing a report to a doctor. Leave either blank to omit it; leave both blank and no patient line is printed at all.

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
