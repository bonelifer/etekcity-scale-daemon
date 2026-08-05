"""Configuration loading and persistence for the daemon."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class DaemonConfig:
    """Parsed daemon configuration."""

    config_path: Path
    address: str
    model: str
    adapter: str
    scanning_mode: str
    cooldown_seconds: int
    db_path: str
    log_level: str


@dataclass
class ReportConfig:
    """Parsed [report] section controlling PDF report rendering."""

    include_address: bool
    include_model: bool
    include_impedance: bool
    weight_unit: str  # "kg", "lb", or "st"
    date_format: str  # "us" or "world"
    layout: str  # "full" or "simple"


DEFAULT_REPORT_CONFIG = ReportConfig(
    include_address=True,
    include_model=True,
    include_impedance=True,
    weight_unit="kg",
    date_format="world",
    layout="full",
)

_WEIGHT_UNITS = ("kg", "lb", "st")
_DATE_FORMATS = ("us", "world")
_LAYOUTS = ("full", "simple")


def _parse_bool(value: str, key: str) -> bool:
    """Parse a yes/no-style config value.

    Args:
        value: Raw string from the config file.
        key: Dotted key name, used in the error message.

    Returns:
        The parsed boolean.

    Raises:
        ConfigError: If ``value`` isn't a recognized yes/no spelling.
    """
    normalized = value.strip().lower()
    if normalized in ("yes", "true", "1", "on"):
        return True
    if normalized in ("no", "false", "0", "off"):
        return False
    raise ConfigError(f"{key} must be yes/no, got {value!r}")


def load_config(config_path: str) -> DaemonConfig:
    """Load and validate the daemon configuration file.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed configuration.

    Raises:
        ConfigError: If the file is missing or a required value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. Copy "
            "config/etekcity-scale-daemon.ini.example to this path and edit it."
        )

    parser = configparser.ConfigParser()
    parser.read(path)

    scale = parser["scale"] if parser.has_section("scale") else {}
    storage = parser["storage"] if parser.has_section("storage") else {}
    daemon = parser["daemon"] if parser.has_section("daemon") else {}

    try:
        cooldown_seconds = int(scale.get("cooldown_seconds", "5"))
    except ValueError as exc:
        raise ConfigError("scale.cooldown_seconds must be an integer") from exc

    db_path = storage.get("db_path", "").strip()
    if not db_path:
        raise ConfigError("storage.db_path must be set")

    return DaemonConfig(
        config_path=path,
        address=scale.get("address", "").strip(),
        model=scale.get("model", "").strip(),
        adapter=scale.get("adapter", "").strip(),
        scanning_mode=scale.get("scanning_mode", "active").strip().lower(),
        cooldown_seconds=cooldown_seconds,
        db_path=db_path,
        log_level=daemon.get("log_level", "INFO").strip().upper(),
    )


def load_report_config(config_path: str) -> ReportConfig:
    """Load the ``[report]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed report configuration, or ``DEFAULT_REPORT_CONFIG`` (all
        columns shown, weights in kilograms) if the file has no ``[report]``
        section.

    Raises:
        ConfigError: If the file is missing or a ``[report]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("report"):
        return DEFAULT_REPORT_CONFIG

    report = parser["report"]

    weight_unit = report.get("weight_unit", DEFAULT_REPORT_CONFIG.weight_unit).strip().lower()
    if weight_unit not in _WEIGHT_UNITS:
        raise ConfigError(
            f"report.weight_unit must be one of {_WEIGHT_UNITS}, got {weight_unit!r}"
        )

    date_format = report.get("date_format", DEFAULT_REPORT_CONFIG.date_format).strip().lower()
    if date_format not in _DATE_FORMATS:
        raise ConfigError(
            f"report.date_format must be one of {_DATE_FORMATS}, got {date_format!r}"
        )

    layout = report.get("layout", DEFAULT_REPORT_CONFIG.layout).strip().lower()
    if layout not in _LAYOUTS:
        raise ConfigError(f"report.layout must be one of {_LAYOUTS}, got {layout!r}")

    return ReportConfig(
        include_address=_parse_bool(
            report.get("include_address", "yes"), "report.include_address"
        ),
        include_model=_parse_bool(
            report.get("include_model", "yes"), "report.include_model"
        ),
        include_impedance=_parse_bool(
            report.get("include_impedance", "yes"), "report.include_impedance"
        ),
        weight_unit=weight_unit,
        date_format=date_format,
        layout=layout,
    )


def persist_discovered_scale(config_path: Path, address: str, model: str) -> None:
    """Write a newly discovered scale's address and model back to the config file.

    Rewrites only the ``address =`` and ``model =`` lines within the
    ``[scale]`` section in place, so comments and formatting elsewhere in
    the file are preserved.

    Args:
        config_path: Path to the INI configuration file to update.
        address: BLE MAC address of the discovered scale.
        model: Scale model identifier (``ScaleModel.value``) to persist.
    """
    lines = config_path.read_text().splitlines(keepends=True)
    in_scale_section = False
    address_written = False
    model_written = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_scale_section = stripped == "[scale]"
            continue
        if not in_scale_section:
            continue
        if stripped.startswith("address") and "=" in stripped and not address_written:
            lines[i] = f"address = {address}\n"
            address_written = True
        elif stripped.startswith("model") and "=" in stripped and not model_written:
            lines[i] = f"model = {model}\n"
            model_written = True

    config_path.write_text("".join(lines))
