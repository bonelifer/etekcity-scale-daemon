"""Configuration loading and persistence for the daemon."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from datetime import date, datetime
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
    include_heart_rate: bool
    include_summary: bool
    include_body_metrics: bool
    weight_unit: str  # "kg", "lb", or "st"
    date_format: str  # "us" or "world"
    layout: str  # "full" or "simple"
    page_size: str  # "letter" or "a4"


DEFAULT_REPORT_CONFIG = ReportConfig(
    include_address=True,
    include_model=True,
    include_impedance=True,
    include_heart_rate=False,
    include_summary=False,
    include_body_metrics=False,
    weight_unit="kg",
    date_format="world",
    layout="full",
    page_size="letter",
)

_WEIGHT_UNITS = ("kg", "lb", "st")
_DATE_FORMATS = ("us", "world")
_PAGE_SIZES = ("letter", "a4")
_LAYOUTS = ("full", "simple", "chart")


@dataclass
class PatientConfig:
    """Parsed [patient] section: optional identifying info for PDF reports."""

    name: str
    email: str
    height_m: float  # 0.0 means unset
    birthdate: date | None
    sex: str  # "" (unset), "male", or "female"
    athlete: bool


DEFAULT_PATIENT_CONFIG = PatientConfig(
    name="", email="", height_m=0.0, birthdate=None, sex="", athlete=False
)

_SEXES = ("male", "female")


@dataclass
class MqttConfig:
    """Parsed [mqtt] section: optional MQTT publishing of live measurements."""

    enabled: bool
    host: str
    port: int
    username: str
    password: str
    use_tls: bool
    topic_prefix: str
    qos: int
    retain: bool


DEFAULT_MQTT_CONFIG = MqttConfig(
    enabled=False,
    host="",
    port=1883,
    username="",
    password="",
    use_tls=False,
    topic_prefix="etekcity_scale_daemon",
    qos=0,
    retain=True,
)

_QOS_LEVELS = (0, 1, 2)


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

    page_size = report.get("page_size", DEFAULT_REPORT_CONFIG.page_size).strip().lower()
    if page_size not in _PAGE_SIZES:
        raise ConfigError(f"report.page_size must be one of {_PAGE_SIZES}, got {page_size!r}")

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
        include_heart_rate=_parse_bool(
            report.get("include_heart_rate", "no"), "report.include_heart_rate"
        ),
        include_summary=_parse_bool(
            report.get("include_summary", "no"), "report.include_summary"
        ),
        include_body_metrics=_parse_bool(
            report.get("include_body_metrics", "no"), "report.include_body_metrics"
        ),
        weight_unit=weight_unit,
        date_format=date_format,
        layout=layout,
        page_size=page_size,
    )


def load_patient_config(config_path: str) -> PatientConfig:
    """Load the ``[patient]`` section of the daemon config file, if present.

    Both fields are free text and optional; whichever are left blank are
    simply omitted from PDF reports.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed patient info, or ``DEFAULT_PATIENT_CONFIG`` (both blank)
        if the file has no ``[patient]`` section.

    Raises:
        ConfigError: If the file is missing.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("patient"):
        return DEFAULT_PATIENT_CONFIG

    patient = parser["patient"]

    height_m_str = patient.get("height_m", "").strip()
    height_m = 0.0
    if height_m_str:
        try:
            height_m = float(height_m_str)
        except ValueError as exc:
            raise ConfigError("patient.height_m must be a number") from exc
        if height_m <= 0:
            raise ConfigError("patient.height_m must be a positive number")

    birthdate_str = patient.get("birthdate", "").strip()
    birthdate = None
    if birthdate_str:
        try:
            birthdate = datetime.strptime(birthdate_str, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ConfigError("patient.birthdate must be in YYYY-MM-DD format") from exc

    sex = patient.get("sex", "").strip().lower()
    if sex and sex not in _SEXES:
        raise ConfigError(f"patient.sex must be one of {_SEXES}, got {sex!r}")

    return PatientConfig(
        name=patient.get("name", "").strip(),
        email=patient.get("email", "").strip(),
        height_m=height_m,
        birthdate=birthdate,
        sex=sex,
        athlete=_parse_bool(patient.get("athlete", "no"), "patient.athlete"),
    )


def load_mqtt_config(config_path: str) -> MqttConfig:
    """Load the ``[mqtt]`` section of the daemon config file, if present.

    Args:
        config_path: Path to the INI configuration file.

    Returns:
        The parsed MQTT configuration, or ``DEFAULT_MQTT_CONFIG`` (disabled)
        if the file has no ``[mqtt]`` section.

    Raises:
        ConfigError: If the file is missing or a ``[mqtt]`` value is invalid.
    """
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    parser = configparser.ConfigParser()
    parser.read(path)

    if not parser.has_section("mqtt"):
        return DEFAULT_MQTT_CONFIG

    mqtt = parser["mqtt"]
    enabled = _parse_bool(mqtt.get("enabled", "no"), "mqtt.enabled")

    host = mqtt.get("host", "").strip()
    if enabled and not host:
        raise ConfigError("mqtt.host must be set when mqtt.enabled = yes")

    try:
        port = int(mqtt.get("port", str(DEFAULT_MQTT_CONFIG.port)))
    except ValueError as exc:
        raise ConfigError("mqtt.port must be an integer") from exc

    try:
        qos = int(mqtt.get("qos", str(DEFAULT_MQTT_CONFIG.qos)))
    except ValueError as exc:
        raise ConfigError("mqtt.qos must be an integer") from exc
    if qos not in _QOS_LEVELS:
        raise ConfigError(f"mqtt.qos must be one of {_QOS_LEVELS}, got {qos!r}")

    return MqttConfig(
        enabled=enabled,
        host=host,
        port=port,
        username=mqtt.get("username", "").strip(),
        password=mqtt.get("password", "").strip(),
        use_tls=_parse_bool(mqtt.get("use_tls", "no"), "mqtt.use_tls"),
        topic_prefix=mqtt.get("topic_prefix", DEFAULT_MQTT_CONFIG.topic_prefix).strip(),
        qos=qos,
        retain=_parse_bool(mqtt.get("retain", "yes"), "mqtt.retain"),
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
