"""Flow-control window update."""

from __future__ import annotations

import struct
from dataclasses import dataclass

_WINDOW_UPDATE = struct.Struct("<I")


@dataclass(frozen=True, slots=True)
class WindowUpdate:
    """Flow-control window update."""

    size: int

    @classmethod
    def from_bytes(cls, payload: bytes) -> WindowUpdate:
        """Parse WindowUpdate."""
        if len(payload) < _WINDOW_UPDATE.size:
            raise ValueError(f"WindowUpdate payload too short: {len(payload)}")
        return cls(_WINDOW_UPDATE.unpack(payload[: _WINDOW_UPDATE.size])[0])
