"""Command-line entry point and daemon run loop."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from datetime import datetime, timezone

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
from .config import ConfigError, DaemonConfig, load_config, persist_discovered_scale
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


async def run_daemon(config: DaemonConfig) -> None:
    """Connect to the configured (or newly discovered) scale and log measurements.

    Args:
        config: Loaded daemon configuration.

    Raises:
        ConfigError: If the config names an unrecognized scale model.
    """
    address = config.address
    model_value = config.model

    if not address or not model_value:
        address, model = await discover_scale(config.adapter or None)
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

    def on_measurement(data: ScaleData) -> None:
        row = _measurement_to_row(data, address, model_value)
        store.record(**row)
        _LOGGER.info(
            "Recorded measurement from %s: weight=%s kg impedance=%s",
            address,
            row["weight_kg"],
            row["impedance_ohms"],
        )

    scale_kwargs: dict[str, object] = {"scanning_mode": scanning_mode}
    if config.adapter:
        scale_kwargs["adapter"] = config.adapter
    if model in _GATT_MODELS:
        scale_kwargs["cooldown_seconds"] = config.cooldown_seconds

    scale = SCALE_CLASSES[model](address, on_measurement, **scale_kwargs)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    _LOGGER.info(
        "Starting etekcity-scale-daemon %s for %s scale at %s",
        __version__,
        model_value,
        address,
    )
    await scale.async_start()
    try:
        await stop_event.wait()
    finally:
        _LOGGER.info("Shutting down")
        await scale.async_stop()
        store.close()


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
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (overrides the config file's log level)",
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
        asyncio.run(run_daemon(config))
    except (TimeoutError, ConfigError) as exc:
        _LOGGER.error(str(exc))
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
