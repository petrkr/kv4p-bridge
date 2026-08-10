"""In-memory transport that fakes a firmware HELLO reply, for tests/smoke runs without hardware."""

from __future__ import annotations

import logging
import struct
import threading
from collections.abc import Callable

from ..constants.kiss import KISS_CMD_SETHARDWARE
from ..constants.vendor import COMMAND_HELLO, KV4P_PROTOCOL_VERSION, KV4P_VENDOR_PREFIX
from . import Kv4pTransport

logger = logging.getLogger(__name__)

# Same layouts as messages/version.py and messages/device_state.py from_bytes().
_VERSION = struct.Struct("<HcIBffB")
_DEVICE_STATE = struct.Struct("<IiHBffBBBcBBB")


class DummyTransport(Kv4pTransport):
    """Transport with no real radio: answers reset() with a synthetic HELLO."""

    def __init__(
        self,
        *,
        firmware_version: int = 17,
        window_size: int = 2048,
        min_radio_freq: float = 134.0,
        max_radio_freq: float = 174.0,
        features: int = 0,
    ) -> None:
        self._firmware_version = firmware_version
        self._window_size = window_size
        self._min_radio_freq = min_radio_freq
        self._max_radio_freq = max_radio_freq
        self._features = features
        self._on_frame: Callable[[int, bytes], None] | None = None
        self.written: list[tuple[int, bytes]] = []

    def open(self, on_frame: Callable[[int, bytes], None]) -> None:
        self._on_frame = on_frame
        logger.info("dummy transport open")

    def close(self) -> None:
        self._on_frame = None
        logger.info("dummy transport close")

    def reset(self) -> None:
        logger.info("dummy transport reset -> synthetic HELLO")
        payload = _encode_vendor_payload(COMMAND_HELLO, self._build_hello_bytes())
        threading.Thread(target=self._deliver, args=(payload,), daemon=True).start()

    def _build_hello_bytes(self) -> bytes:
        version = _VERSION.pack(
            self._firmware_version,
            b"D",
            self._window_size,
            0,
            self._min_radio_freq,
            self._max_radio_freq,
            self._features,
        )
        device_state = _DEVICE_STATE.pack(
            0,  # applied_sequence
            -1,  # memory_id
            0,  # flags
            0,  # bw
            self._min_radio_freq,  # freq_tx
            self._min_radio_freq,  # freq_rx
            0,  # ctcss_tx
            1,  # squelch
            0,  # ctcss_rx
            b"D",  # radio_module_status
            1,  # mode (RX)
            0,  # last_error
            0,  # latest_rssi
        )
        return version + device_state

    def _deliver(self, payload: bytes) -> None:
        if self._on_frame is not None:
            self._on_frame(KISS_CMD_SETHARDWARE, payload)

    def write_frame(self, command: int, payload: bytes) -> None:
        self.written.append((command, payload))
        logger.debug("dummy transport write command=0x%02x bytes=%d", command, len(payload))

    def flush(self) -> None:
        pass


def _encode_vendor_payload(command: int, payload: bytes = b"") -> bytes:
    return KV4P_VENDOR_PREFIX + bytes([KV4P_PROTOCOL_VERSION, command]) + payload
