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
| `report` | `layout` | PDF layout: `full` (one row per reading), `simple` (date/weight only, in side-by-side columns), or `chart` (a line chart of weight over time). |
| `report` | `include_address` | Show the Address column in the `full` layout: `yes` or `no`. |
| `report` | `include_model` | Show the Model column in the `full` layout: `yes` or `no`. |
| `report` | `include_impedance` | Show the Impedance column in the `full` layout: `yes` or `no`. |
| `report` | `include_heart_rate` | Show the Heart Rate column in the `full` layout: `yes` or `no`. Only EFS-A591S reports heart rate; defaults to `no`. |
| `report` | `weight_unit` | Unit to render the Weight column in: `kg`, `lb`, or `st`. |
| `report` | `date_format` | `us` (MM/DD/YYYY, 12-hour) or `world` (DD/MM/YYYY, 24-hour). |
| `report` | `page_size` | PDF page size: `letter` or `a4`. |
| `report` | `include_summary` | Print a min/max/average/net-change summary line for Weight below the title: `yes` or `no`. Defaults to `no`. |
| `report` | `include_body_metrics` | Print a BMI/body-fat/etc. snapshot for the latest impedance reading: `yes` or `no`. Requires `[patient] height_m`/`birthdate`/`sex`. PDF only. Defaults to `no`. |
| `patient` | `name` | Patient name printed below the title in PDF reports. Leave blank to omit. |
| `patient` | `email` | Patient email printed below the title in PDF reports. Leave blank to omit. |
| `patient` | `height_m` | Height in meters. Required if `include_body_metrics = yes`. |
| `patient` | `birthdate` | `YYYY-MM-DD`. Required if `include_body_metrics = yes` (age is computed fresh from this on every report). |
| `patient` | `sex` | `male` or `female`. Required if `include_body_metrics = yes`. |
| `patient` | `athlete` | `yes` or `no`. Affects the body-fat-percentage calculation. Defaults to `no`. |

#### systemd service

```bash
sudo useradd --system --no-create-home --group etekcity-scale-daemon
sudo cp systemd/etekcity-scale-daemon.service /etc/systemd/system/
sudo ln -s /opt/etekcity-scale-daemon/venv/bin/etekcity-scale-daemon /usr/bin/etekcity-scale-daemon
sudo ln -s /opt/etekcity-scale-daemon/venv/bin/etekcity-scale-report /usr/bin/etekcity-scale-report
sudo ln -s /opt/etekcity-scale-daemon/venv/bin/etekcity-scale-prune /usr/bin/etekcity-scale-prune
sudo systemctl daemon-reload
sudo systemctl enable --now etekcity-scale-daemon
```

Watch the discovery step (first run) with:

```bash
sudo journalctl -u etekcity-scale-daemon -f
```

### Docker

**⚠️ Unverified.** Docker wasn't available in the environment this was written in, so the image has only been checked for "does `pip install .` succeed with these files" — the container has never actually been built, started, or tested against real BLE hardware. Treat this as a starting point to debug, not a working install path, until someone confirms it end-to-end.

BLE access from inside a container needs the host's D-Bus system bus and Bluetooth adapter, which is why `docker-compose.yml` uses `network_mode: host` plus a bind mount of `/var/run/dbus` — bridge networking would isolate the container from both.

```bash
mkdir -p config data
cp config/etekcity-scale-daemon.ini.example config/config.ini
"$EDITOR" config/config.ini   # set storage.db_path = /var/lib/etekcity-scale-daemon/measurements.db
docker compose up -d --build
docker compose logs -f
```

Run `etekcity-scale-report` or `etekcity-scale-prune` inside the running container:

```bash
docker compose exec etekcity-scale-daemon etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --output /var/lib/etekcity-scale-daemon/report.pdf
```

Without Compose, the equivalent is:

```bash
docker build -t etekcity-scale-daemon .
docker run -d --name etekcity-scale-daemon \
  --network host \
  -v /var/run/dbus:/var/run/dbus \
  -v "$(pwd)/config:/etc/etekcity-scale-daemon" \
  -v "$(pwd)/data:/var/lib/etekcity-scale-daemon" \
  --restart unless-stopped \
  etekcity-scale-daemon
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

### Cron-driven polling instead of a long-running service

`--once` records a single measurement and exits, instead of running until stopped — an alternative to the systemd service for setups that prefer periodic polling (e.g. cron) over a persistent process. It waits up to `--once-timeout` seconds (default 60) for a reading and exits non-zero if none arrives in time:

```bash
etekcity-scale-daemon --config /etc/etekcity-scale-daemon/config.ini --once --once-timeout 30
```

Example crontab entry, polling every 15 minutes:

```
*/15 * * * * /usr/bin/etekcity-scale-daemon --config /etc/etekcity-scale-daemon/config.ini --once >> /var/log/etekcity-scale-daemon.log 2>&1
```

On the very first run, if `[scale] address`/`model` are still empty, `--once` also uses `--once-timeout` as the scale-discovery timeout (instead of a separate 60-second default) — worst case, an undiscovered scale on a cron job can take up to `2 * --once-timeout` before giving up. Once discovered, the address is saved back into the config (as always), so every run after that only waits `--once-timeout` for the measurement itself.

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

If the database has readings from more than one scale (e.g. different family members each with their own), add `--multi-scale` instead to get one PDF with a separate heading, its own table/chart, and its own summary line per scale, each starting on a fresh page — rather than one table mixing everyone's readings together:

```bash
etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --multi-scale --output all-scales.pdf
```

`--multi-scale` is mutually exclusive with `--address` and only affects PDF output (`--format csv` ignores it, since the CSV's `Address` column already differentiates scales in one flat file).

Add `--format csv` for a CSV file instead of a PDF (default output path becomes `measurements-report.csv`):

```bash
etekcity-scale-report --config /etc/etekcity-scale-daemon/config.ini --format csv --output report.csv
```

CSV export always uses the `full` layout's column set (respecting `include_address`/`include_model`/`include_impedance`/`include_heart_rate`, `weight_unit`, and `date_format`) — `layout`, `page_size`, `include_summary`, `include_body_metrics`, and `[patient]` are PDF-only and have no effect on CSV.

Set `report.include_body_metrics = yes` (plus `[patient] height_m`/`birthdate`/`sex`) for a "Body Composition" section — BMI, body fat %, muscle mass, bone mass, and the rest of the upstream library's `BodyMetrics` calculations, computed from the single most recent reading that has impedance data. It's a current snapshot, not a per-reading history, and only applies to single-scale PDF reports — it's skipped for `--format csv` (no [patient] context there) and for `--multi-scale` (which can represent readings from different people, so one shared height/birthdate/sex wouldn't make sense).

The layout, which columns appear, the weight unit, and the date/time format are all controlled by the `[report]` section of the config file (see the table above) — `--config` reads them, `--db` always uses the defaults (`full` layout, all columns, kilograms, `world` date format).

The `simple` layout drops every column except Date/Time and Weight and lays readings out in several side-by-side column pairs (filling one pair top-to-bottom before starting the next) instead of a single narrow two-column table.

The `chart` layout replaces the table with a line chart of weight over time (x-axis labels thin themselves out automatically when there are many readings). It needs at least two readings with a weight value; with fewer, the page prints a "not enough data" note instead. `include_address`/`include_model`/`include_impedance`/`include_heart_rate` have no effect on this layout — only `weight_unit` and `date_format` apply.

See [samples/](samples/) for a rendered PDF of every layout/unit/date-format combination.

Set `[patient] name` and/or `email` (only read from `--config`, not `--db`) to print that identifying info below the title — handy when handing a report to a doctor. Leave either blank to omit it; leave both blank and no patient line is printed at all.

## Pruning old data

`etekcity-scale-prune` deletes measurements older than a given number of days. It's manual only — nothing in the daemon deletes data automatically. It's a **dry run by default**: it reports how many rows match without touching anything, until you pass `--yes`.

```bash
# See how many readings older than 365 days would be deleted
etekcity-scale-prune --config /etc/etekcity-scale-daemon/config.ini --older-than 365

# Actually delete them (also reclaims disk space with VACUUM)
etekcity-scale-prune --config /etc/etekcity-scale-daemon/config.ini --older-than 365 --yes
```

Add `--address AA:BB:CC:DD:EE:FF` to restrict pruning to one scale. `--db` works the same as with `etekcity-scale-report`, bypassing the config file.

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

CI (`.github/workflows/ci.yml`) runs `flake8` and `scripts/smoke-test.sh` on every PR. Run them locally before pushing:

```bash
pip install flake8
flake8 --config .flake8 src
./scripts/smoke-test.sh
```

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
