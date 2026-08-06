"""Command-line entry point and daemon run loop."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakScanner
from etekcity_esf551_ble import (
    HEART_RATE_KEY,
    IMPEDANCE_500KHZ_KEY,
    IMPEDANCE_KEY,
    SCALE_CLASSES,
    WEIGHT_KEY,
    BluetoothScanningMode,
    ScaleData,
    ScaleModel,
    detect_model,
)

from ._version import __version__
from .config import (
    ConfigError,
    DaemonConfig,
    load_config,
    load_patient_config,
    load_report_config,
    persist_discovered_scale,
)
from .storage import MeasurementStore

_LOGGER = logging.getLogger("etekcity_scale_daemon")

_GATT_MODELS = (ScaleModel.ESF551, ScaleModel.ESF24, ScaleModel.EFSA591S)


async def discover_scale(
    adapter: str | None, timeout: float = 60.0
) -> tuple[str, ScaleModel]:
    """Scan for the first advertisement matching a supported scale.

    Args:
        adapter: Optional BLE adapter to scan with (Linux only).
        timeout: Seconds to scan before giving up.

    Returns:
        The discovered scale's BLE address and model.

    Raises:
        TimeoutError: If no supported scale is found within ``timeout``.
    """
    found: asyncio.Future[tuple[str, ScaleModel]] = (
        asyncio.get_running_loop().create_future()
    )

    def on_advertisement(device, adv) -> None:
        model = detect_model(adv.local_name, adv.manufacturer_data, device.address)
        if model is not None and not found.done():
            found.set_result((device.address, model))

    scanner_kwargs = {"adapter": adapter} if adapter else {}
    _LOGGER.info(
        "No scale configured yet - scanning for a supported scale "
        "(step on it now)..."
    )
    async with BleakScanner(on_advertisement, **scanner_kwargs):
        return await asyncio.wait_for(found, timeout)


def _measurement_to_row(
    data: ScaleData, address: str, model: str
) -> dict[str, object]:
    """Flatten a ScaleData notification into storage-ready fields."""
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "address": address,
        "model": model,
        "weight_kg": data.measurements.get(WEIGHT_KEY),
        "impedance_ohms": data.measurements.get(IMPEDANCE_KEY),
        "impedance_500khz_ohms": data.measurements.get(IMPEDANCE_500KHZ_KEY),
        "heart_rate_bpm": data.measurements.get(HEART_RATE_KEY),
        "display_unit": (
            data.display_unit.name if data.display_unit is not None else None
        ),
    }


async def run_daemon(
    config: DaemonConfig, once: bool = False, once_timeout: int = 60
) -> bool:
    """Connect to the configured (or newly discovered) scale and log measurements.

    Args:
        config: Loaded daemon configuration.
        once: If True, exit after recording a single measurement (or after
            ``once_timeout`` seconds without one) instead of running until a
            stop signal -- for cron-driven polling instead of a long-running
            service.
        once_timeout: Seconds to wait for one measurement before giving up.
            Only used when ``once`` is True.

    Returns:
        True if at least one measurement was recorded. Always True for a
        normal (non-``once``) run, which only returns via a stop signal.

    Raises:
        ConfigError: If the config names an unrecognized scale model.
    """
    address = config.address
    model_value = config.model

    if not address or not model_value:
        discovery_timeout = float(once_timeout) if once else 60.0
        address, model = await discover_scale(config.adapter or None, discovery_timeout)
        model_value = model.value
        persist_discovered_scale(config.config_path, address, model_value)
        _LOGGER.info(
            "Discovered %s at %s - saved to %s",
            model_value,
            address,
            config.config_path,
        )
    else:
        try:
            model = ScaleModel(model_value)
        except ValueError as exc:
            raise ConfigError(f"Unknown scale.model in config: {model_value!r}") from exc

    store = MeasurementStore(config.db_path)
    scanning_mode = (
        BluetoothScanningMode.PASSIVE
        if config.scanning_mode == "passive"
        else BluetoothScanningMode.ACTIVE
    )

    stop_event = asyncio.Event()
    measurement_received = False

    def on_measurement(data: ScaleData) -> None:
        nonlocal measurement_received
        row = _measurement_to_row(data, address, model_value)
        store.record(**row)
        measurement_received = True
        _LOGGER.info(
            "Recorded measurement from %s: weight=%s kg impedance=%s",
            address,
            row["weight_kg"],
            row["impedance_ohms"],
        )
        if once:
            stop_event.set()

    scale_kwargs: dict[str, object] = {"scanning_mode": scanning_mode}
    if config.adapter:
        scale_kwargs["adapter"] = config.adapter
    if model in _GATT_MODELS:
        scale_kwargs["cooldown_seconds"] = config.cooldown_seconds

    scale = SCALE_CLASSES[model](address, on_measurement, **scale_kwargs)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    _LOGGER.info(
        "Starting etekcity-scale-daemon %s for %s scale at %s%s",
        __version__,
        model_value,
        address,
        f" (once, {once_timeout}s timeout)" if once else "",
    )
    await scale.async_start()
    try:
        if once:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=once_timeout)
            except TimeoutError:
                _LOGGER.warning(
                    "No measurement received within %s seconds", once_timeout
                )
        else:
            await stop_event.wait()
    finally:
        _LOGGER.info("Shutting down")
        await scale.async_stop()
        store.close()

    return measurement_received


def _check_config(config_path: str) -> int:
    """Validate a config file against every section loader, without running.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        0 if the file is valid (a summary is printed), 1 otherwise (each
        error is printed).
    """
    if not Path(config_path).is_file():
        print(f"Error: Config file not found: {config_path}")
        return 1

    errors: list[str] = []
    daemon_config = report_config = patient_config = None

    try:
        daemon_config = load_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        report_config = load_report_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        patient_config = load_patient_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))

    if errors:
        print(f"{config_path}: INVALID")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"{config_path}: OK")
    print(
        "  scale: address="
        f"{daemon_config.address or '(auto-discover)'} model="
        f"{daemon_config.model or '(auto-discover)'} adapter="
        f"{daemon_config.adapter or '(default)'}"
    )
    print(f"  storage: db_path={daemon_config.db_path}")
    print(f"  daemon: log_level={daemon_config.log_level}")
    print(
        "  report: layout="
        f"{report_config.layout} weight_unit={report_config.weight_unit} "
        f"date_format={report_config.date_format} page_size={report_config.page_size}"
    )
    print(
        "  patient: name="
        f"{'(set)' if patient_config.name else '(blank)'} email="
        f"{'(set)' if patient_config.email else '(blank)'}"
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="etekcity-scale-daemon",
        description=(
            "Standalone BLE daemon that logs Etekcity smart scale "
            "measurements to a local SQLite database."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to the daemon's INI configuration file",
    )
    parser.add_argument(
        "-k",
        "--check-config",
        action="store_true",
        help="Validate the config file and exit, without starting the daemon",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (overrides the config file's log level)",
    )
    parser.add_argument(
        "-o",
        "--once",
        action="store_true",
        help=(
            "Record one measurement and exit, instead of running until "
            "stopped (for cron-driven polling instead of a long-running service)"
        ),
    )
    parser.add_argument(
        "-w",
        "--once-timeout",
        dest="once_timeout",
        type=int,
        default=60,
        metavar="SECONDS",
        help="Seconds to wait for a measurement in --once mode (default: %(default)s)",
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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

    if args.check_config:
        return _check_config(args.config)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logging.basicConfig(level=logging.ERROR)
        _LOGGER.error(str(exc))
        return 1

    log_level = "DEBUG" if args.verbose else config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        measurement_received = asyncio.run(
            run_daemon(config, once=args.once, once_timeout=args.once_timeout)
        )
    except (TimeoutError, ConfigError) as exc:
        _LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        return 0

    if args.once and not measurement_received:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
