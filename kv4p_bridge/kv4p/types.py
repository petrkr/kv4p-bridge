"""KV4P protocol data types."""

from __future__ import annotations

import struct


DRA818_12K5 = 0x00
DRA818_25K = 0x01

HOST_STATE_RADIO_CONFIG_VALID = 1 << 0
HOST_STATE_PTT_REQUESTED = 1 << 1
HOST_STATE_RX_AUDIO_OPEN = 1 << 2
HOST_STATE_HIGH_POWER = 1 << 3
HOST_STATE_RSSI_ENABLED = 1 << 4
HOST_STATE_FILTER_PRE = 1 << 5
HOST_STATE_FILTER_HIGH = 1 << 6
HOST_STATE_FILTER_LOW = 1 << 7
HOST_STATE_TX_ALLOWED = 1 << 11
HOST_STATE_ENABLE_STATUS_REPORTS = 1 << 12

DEVICE_STATE_PHYS_PTT_DOWN = 1 << 8
DEVICE_STATE_TX_ACTIVE = 1 << 9
DEVICE_STATE_SQUELCHED = 1 << 10

_VERSION = struct.Struct("<HcIBffB")
_HOST_DESIRED_STATE = struct.Struct("<IiHBffBBB")
_DEVICE_STATE = struct.Struct("<IiHBffBBBcBBB")
_WINDOW_UPDATE = struct.Struct("<I")


class Version:
    """Firmware version payload."""

    def __init__(
        self,
        ver: int,
        radio_module_status: str,
        window_size: int,
        rf_module_type: int,
        min_radio_freq: float,
        max_radio_freq: float,
        features: int,
    ) -> None:
        self.ver = ver
        self.radio_module_status = radio_module_status
        self.window_size = window_size
        self.rf_module_type = rf_module_type
        self.min_radio_freq = min_radio_freq
        self.max_radio_freq = max_radio_freq
        self.features = features

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


class HostDesiredState:
    """Host desired radio/control state."""

    def __init__(
        self,
        sequence: int,
        memory_id: int,
        flags: int,
        bw: int,
        freq_tx: float,
        freq_rx: float,
        ctcss_tx: int,
        squelch: int,
        ctcss_rx: int,
    ) -> None:
        self.sequence = sequence
        self.memory_id = memory_id
        self.flags = flags
        self.bw = bw
        self.freq_tx = freq_tx
        self.freq_rx = freq_rx
        self.ctcss_tx = ctcss_tx
        self.squelch = squelch
        self.ctcss_rx = ctcss_rx

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


class DeviceState:
    """Firmware-applied state."""

    def __init__(
        self,
        applied_sequence: int,
        memory_id: int,
        flags: int,
        bw: int,
        freq_tx: float,
        freq_rx: float,
        ctcss_tx: int,
        squelch: int,
        ctcss_rx: int,
        radio_module_status: str,
        mode: int,
        last_error: int,
        latest_rssi: int,
    ) -> None:
        self.applied_sequence = applied_sequence
        self.memory_id = memory_id
        self.flags = flags
        self.bw = bw
        self.freq_tx = freq_tx
        self.freq_rx = freq_rx
        self.ctcss_tx = ctcss_tx
        self.squelch = squelch
        self.ctcss_rx = ctcss_rx
        self.radio_module_status = radio_module_status
        self.mode = mode
        self.last_error = last_error
        self.latest_rssi = latest_rssi

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


class Hello:
    """HELLO payload."""

    def __init__(self, version: Version, device_state: DeviceState) -> None:
        self.version = version
        self.device_state = device_state

    @classmethod
    def from_bytes(cls, payload: bytes) -> Hello:
        """Parse HELLO."""
        state_offset = len(payload) - _DEVICE_STATE.size
        if state_offset <= 0:
            raise ValueError(f"HELLO payload too short: {len(payload)}")
        version = Version.from_bytes(payload[:state_offset])
        device_state = DeviceState.from_bytes(payload[state_offset:])
        return cls(version, device_state)


class WindowUpdate:
    """Flow-control window update."""

    def __init__(self, size: int) -> None:
        self.size = size

    @classmethod
    def from_bytes(cls, payload: bytes) -> WindowUpdate:
        """Parse WindowUpdate."""
        if len(payload) < _WINDOW_UPDATE.size:
            raise ValueError(f"WindowUpdate payload too short: {len(payload)}")
        return cls(_WINDOW_UPDATE.unpack(payload[: _WINDOW_UPDATE.size])[0])
