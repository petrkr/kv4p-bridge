"""Host desired radio/control state."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..constants.messages import DRA818_12K5, DRA818_25K

_HOST_DESIRED_STATE = struct.Struct("<IiHBffBBB")


def bandwidth_to_dra818(value: str) -> int:
    """Translate a bandwidth string into a DRA818_* constant."""
    normalized = value.lower()
    if normalized in {"25k", "25", "wide"}:
        return DRA818_25K
    if normalized in {"12k5", "12.5k", "narrow"}:
        return DRA818_12K5
    raise ValueError(f"unsupported bandwidth: {value}")


@dataclass(frozen=True, slots=True)
class HostDesiredState:
    """Host desired radio/control state."""

    sequence: int
    memory_id: int
    flags: int
    bw: int
    freq_tx: float
    freq_rx: float
    ctcss_tx: int
    squelch: int
    ctcss_rx: int

    def to_bytes(self) -> bytes:
        """Serialize HostDesiredState."""
        return _HOST_DESIRED_STATE.pack(
            self.sequence,
            self.memory_id,
            self.flags,
            self.bw,
            self.freq_tx,
            self.freq_rx,
            self.ctcss_tx,
            self.squelch,
            self.ctcss_rx,
        )
