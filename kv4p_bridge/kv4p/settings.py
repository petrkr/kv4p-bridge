"""Default radio settings applied via `Kv4pRadio.configure()`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Kv4pSettings:
    """Radio settings sent in HostDesiredState.

    These are only defaults for `Kv4pRadio.configure()` — the firmware never
    announces its actual current state on its own, so until `configure()` is
    called the library has no opinion about what the radio is tuned to.
    """

    rx_freq: float = 145.5
    tx_freq: float = 145.5
    bandwidth: str = "12.5k"
    squelch: int = 1
    ctcss_rx: int = 0
    ctcss_tx: int = 0
    high_power: bool = False
    tx_allowed: bool = False
    rssi: bool = False
    filter_pre: bool = False
    filter_high: bool = False
    filter_low: bool = False
