"""Firmware-applied device state."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..constants.messages import DEVICE_STATE_SQUELCHED

_DEVICE_STATE = struct.Struct("<IiHBffBBBcBBB")
DEVICE_STATE_SIZE = _DEVICE_STATE.size


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Firmware-applied state."""

    applied_sequence: int
    memory_id: int
    flags: int
    bw: int
    freq_tx: float
    freq_rx: float
    ctcss_tx: int
    squelch: int
    ctcss_rx: int
    radio_module_status: str
    mode: int
    last_error: int
    latest_rssi: int

    @classmethod
    def from_bytes(cls, payload: bytes) -> DeviceState:
        """Parse DeviceState."""
        if len(payload) < _DEVICE_STATE.size:
            raise ValueError(f"DeviceState payload too short: {len(payload)}")
        values = _DEVICE_STATE.unpack(payload[: _DEVICE_STATE.size])
        return cls(
            applied_sequence=values[0],
            memory_id=values[1],
            flags=values[2],
            bw=values[3],
            freq_tx=values[4],
            freq_rx=values[5],
            ctcss_tx=values[6],
            squelch=values[7],
            ctcss_rx=values[8],
            radio_module_status=values[9].decode("ascii", errors="replace"),
            mode=values[10],
            last_error=values[11],
            latest_rssi=values[12],
        )

    @property
    def sql_open(self) -> bool:
        """Return true when the device squelch is open."""
        return not bool(self.flags & DEVICE_STATE_SQUELCHED)
