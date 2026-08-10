"""HELLO handshake payload."""

from __future__ import annotations

from dataclasses import dataclass

from .device_state import DEVICE_STATE_SIZE, DeviceState
from .version import Version


@dataclass(frozen=True, slots=True)
class Hello:
    """HELLO payload."""

    version: Version
    device_state: DeviceState

    @classmethod
    def from_bytes(cls, payload: bytes) -> Hello:
        """Parse HELLO."""
        state_offset = len(payload) - DEVICE_STATE_SIZE
        if state_offset <= 0:
            raise ValueError(f"HELLO payload too short: {len(payload)}")
        version = Version.from_bytes(payload[:state_offset])
        device_state = DeviceState.from_bytes(payload[state_offset:])
        return cls(version, device_state)
