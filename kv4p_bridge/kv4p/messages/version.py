"""Firmware version payload (part of HELLO)."""

from __future__ import annotations

import struct
from dataclasses import dataclass

_VERSION = struct.Struct("<HcIBffB")


@dataclass(frozen=True, slots=True)
class Version:
    """Firmware version payload."""

    ver: int
    radio_module_status: str
    window_size: int
    rf_module_type: int
    min_radio_freq: float
    max_radio_freq: float
    features: int

    @classmethod
    def from_bytes(cls, payload: bytes) -> Version:
        """Parse Version."""
        if len(payload) != _VERSION.size:
            raise ValueError(f"Version payload must be {_VERSION.size} bytes, got {len(payload)}")
        values = _VERSION.unpack(payload)
        return cls(
            ver=values[0],
            radio_module_status=values[1].decode("ascii", errors="replace"),
            window_size=values[2],
            rf_module_type=values[3],
            min_radio_freq=values[4],
            max_radio_freq=values[5],
            features=values[6],
        )
