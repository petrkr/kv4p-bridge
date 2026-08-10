"""TOML configuration."""

from __future__ import annotations

from pathlib import Path
import tomllib


class LogConfig:
    """Logging configuration."""

    __slots__ = ("level",)

    def __init__(self, level: str = "INFO") -> None:
        self.level = level


class RadioConfig:
    """Radio configuration."""

    __slots__ = (
        "rx_freq",
        "tx_freq",
        "bandwidth",
        "squelch",
        "ctcss_rx",
        "ctcss_tx",
        "high_power",
        "tx_allowed",
        "rssi",
        "filter_pre",
        "filter_high",
        "filter_low",
    )

    def __init__(
        self,
        rx_freq: float = 145.5,
        tx_freq: float = 145.5,
        bandwidth: str = "25k",
        squelch: int = 1,
        ctcss_rx: int = 0,
        ctcss_tx: int = 0,
        high_power: bool = False,
        tx_allowed: bool = False,
        rssi: bool = False,
        filter_pre: bool = False,
        filter_high: bool = False,
        filter_low: bool = False,
    ) -> None:
        self.rx_freq = rx_freq
        self.tx_freq = tx_freq
        self.bandwidth = bandwidth
        self.squelch = squelch
        self.ctcss_rx = ctcss_rx
        self.ctcss_tx = ctcss_tx
        self.high_power = high_power
        self.tx_allowed = tx_allowed
        self.rssi = rssi
        self.filter_pre = filter_pre
        self.filter_high = filter_high
        self.filter_low = filter_low


class Kv4pConfig:
    """KV4P transport configuration."""

    __slots__ = ("device", "baudrate", "tx_audio_command", "rx_audio_open", "status_reports", "radio")

    def __init__(
        self,
        device: str = "",
        baudrate: int = 115200,
        tx_audio_command: int = 0x07,
        rx_audio_open: bool = True,
        status_reports: bool = True,
        radio: RadioConfig | None = None,
    ) -> None:
        self.device = device
        self.baudrate = baudrate
        self.tx_audio_command = tx_audio_command
        self.rx_audio_open = rx_audio_open
        self.status_reports = status_reports
        self.radio = radio or RadioConfig()


class ConnectorConfig:
    """Connector configuration."""

    __slots__ = ("type", "options")

    def __init__(self, type: str = "dummy", options: dict[str, object] | None = None) -> None:
        self.type = type
        self.options = options or {}


class AppConfig:
    """Application configuration."""

    __slots__ = ("log", "kv4p", "connector")

    def __init__(
        self,
        log: LogConfig | None = None,
        kv4p: Kv4pConfig | None = None,
        connector: ConnectorConfig | None = None,
    ) -> None:
        self.log = log or LogConfig()
        self.kv4p = kv4p or Kv4pConfig()
        self.connector = connector or ConnectorConfig()


def load_config(path: str | Path | None) -> AppConfig:
    """Load configuration from TOML."""
    if path is None:
        return AppConfig()

    with Path(path).open("rb") as fh:
        raw = tomllib.load(fh)

    log = LogConfig(**raw.get("log", {}))

    kv4p_raw = raw.get("kv4p", {})
    radio = RadioConfig(**kv4p_raw.get("radio", {}))
    kv4p = Kv4pConfig(
        device=kv4p_raw.get("device", ""),
        baudrate=kv4p_raw.get("baudrate", 115200),
        tx_audio_command=_int_value(kv4p_raw.get("tx_audio_command", 0x07)),
        rx_audio_open=kv4p_raw.get("rx_audio_open", True),
        status_reports=kv4p_raw.get("status_reports", True),
        radio=radio,
    )

    connector_raw = raw.get("connector", {})
    connector_type = connector_raw.get("type", "dummy")
    options = raw.get("connector", {}).get(connector_type, {})
    connector = ConnectorConfig(type=connector_type, options=options)

    return AppConfig(log=log, kv4p=kv4p, connector=connector)


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"expected int value, got {type(value).__name__}")
