"""KV4P radio integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from .constants.kiss import KISS_CMD_DATA, KISS_CMD_SETHARDWARE
from .constants.vendor import (
    COMMAND_DEBUG_DEBUG,
    COMMAND_DEBUG_ERROR,
    COMMAND_DEBUG_INFO,
    COMMAND_DEBUG_TRACE,
    COMMAND_DEBUG_WARN,
    COMMAND_DEVICE_STATE,
    COMMAND_HELLO,
    COMMAND_HOST_DESIRED_STATE,
    COMMAND_RX_AUDIO,
    COMMAND_RX_AUDIO_ADPCM,
    COMMAND_WINDOW_UPDATE,
    KV4P_PROTOCOL_VERSION,
    KV4P_VENDOR_HEADER_LEN,
    KV4P_VENDOR_PREFIX,
)
from .flow_control import FlowControlWindow
from .logging_utils import Throttle
from .messages.desired_state import HostDesiredState
from .messages.device_state import DeviceState
from .messages.hello import Hello
from .messages.window_update import WindowUpdate
from .protocol.kiss import encode_kiss_frame
from .ptt import PttController
from .settings import Kv4pSettings
from .state_tracker import DeviceStateTracker
from .transports import Kv4pTransport
from .transports.serial import Kv4pSerialTransport

logger = logging.getLogger(__name__)


def encode_vendor_payload(command: int, payload: bytes = b"") -> bytes:
    """Build KV4P vendor payload for a KISS SETHARDWARE frame."""
    return KV4P_VENDOR_PREFIX + bytes([KV4P_PROTOCOL_VERSION, command]) + payload


def decode_vendor_payload(payload: bytes) -> tuple[int, bytes] | None:
    """Parse KV4P vendor payload."""
    if len(payload) < KV4P_VENDOR_HEADER_LEN:
        return None
    if payload[:4] != KV4P_VENDOR_PREFIX:
        return None
    if payload[4] != KV4P_PROTOCOL_VERSION:
        return None
    return payload[5], payload[6:]


class RadioNotReadyError(RuntimeError):
    """Raised when an operation requires HELLO to have been received but it hasn't."""


class Kv4pRadio:
    """KV4P-HT radio side of the bridge."""

    def __init__(
        self,
        transport: Kv4pTransport,
        *,
        rx_audio_open: bool = True,
        status_reports: bool = True,
        on_rx_audio: Callable[[bytes], None] | None = None,
        on_sql: Callable[[bool], None] | None = None,
    ) -> None:
        self._transport = transport
        self._flow = FlowControlWindow()
        self._tracker = DeviceStateTracker(
            rx_audio_open=rx_audio_open,
            status_reports=status_reports,
            send_desired_state=self._send_desired_state,
            on_rx_audio=on_rx_audio,
            on_sql=on_sql,
        )
        self._ptt = PttController(self._tracker)
        self._open = False

        self._tx_audio_throttle = Throttle(burst=3, every=100)
        self._window_update_throttle = Throttle(burst=10, every=100)

        self._dispatch: dict[int, Callable[[bytes], None]] = {
            COMMAND_DEBUG_INFO: self._handle_debug,
            COMMAND_DEBUG_ERROR: self._handle_debug,
            COMMAND_DEBUG_WARN: self._handle_debug,
            COMMAND_DEBUG_DEBUG: self._handle_debug,
            COMMAND_DEBUG_TRACE: self._handle_debug,
            COMMAND_HELLO: self._handle_hello,
            COMMAND_DEVICE_STATE: self._handle_device_state,
            COMMAND_RX_AUDIO: self._handle_rx_audio,
            COMMAND_RX_AUDIO_ADPCM: self._handle_rx_audio,
            COMMAND_WINDOW_UPDATE: self._handle_window_update,
        }

    def __enter__(self) -> Kv4pRadio:
        try:
            self.open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    # -- lifecycle -------------------------------------------------------------

    def open(self, hello_timeout: float = 5.0) -> None:
        """Open the transport, reset the radio, and wait for HELLO."""
        if self._open:
            return
        self._transport.open(self._on_kiss_frame)
        self._open = True
        logger.info("radio open")
        self.reset(hello_timeout=hello_timeout)

    def close(self) -> None:
        """Close radio transport."""
        if not self._open:
            return
        logger.info("radio close")
        self._ptt.cancel()
        try:
            if self.is_ready:
                self.set_ptt(False)
        except Exception:
            logger.exception("failed to clear PTT during close")

        self._transport.close()
        self._open = False
        logger.info("radio closed")

    def reset(self, hello_timeout: float = 5.0) -> None:
        """Hardware-reset the radio and wait for it to re-announce itself via HELLO.

        Public so callers can recover a radio that has hung, not just at open().
        """
        if not self._open:
            raise RuntimeError("radio transport is not open")
        self._transport.reset()
        if not self._tracker.wait_for_hello(timeout=hello_timeout):
            raise TimeoutError("timed out waiting for HELLO after reset")

    # -- configuration -----------------------------------------------------------

    def configure(self, settings: Kv4pSettings) -> None:
        """Send a full HostDesiredState snapshot derived from `settings`."""
        self._require_ready()
        self._tracker.apply_settings(settings)

    def set_frequency(self, *, rx: float | None = None, tx: float | None = None) -> None:
        """Update RX/TX frequency, validated against the range reported in HELLO."""
        self._require_ready()
        hello = self._tracker.hello
        assert hello is not None
        for value in (v for v in (rx, tx) if v is not None):
            if not (hello.version.min_radio_freq <= value <= hello.version.max_radio_freq):
                raise ValueError(
                    f"frequency {value} outside radio range "
                    f"{hello.version.min_radio_freq}-{hello.version.max_radio_freq}"
                )
        changes = {}
        if rx is not None:
            changes["rx_freq"] = rx
        if tx is not None:
            changes["tx_freq"] = tx
        self._tracker.apply_settings(replace(self._tracker.settings, **changes))

    def set_ptt(self, enabled: bool) -> None:
        """Set PTT requested state."""
        self._require_ready()
        self._ptt.set(enabled)

    # -- data path ---------------------------------------------------------------

    def send_tx_audio(self, payload: bytes) -> None:
        """Send KV4P-native TX audio payload."""
        self._require_ready()
        command = self._tracker.tx_audio_command
        if self._tx_audio_throttle.should_log():
            logger.info("tx audio command=0x%02x bytes=%d", command, len(payload))
        self._send_vendor(command, payload, flow_controlled=True)

    def send_kiss_data(self, payload: bytes) -> None:
        """Send a raw KISS DATA frame for diagnostics."""
        frame_size = len(encode_kiss_frame(KISS_CMD_DATA, payload))
        if not self._flow.claim(frame_size):
            logger.warning("drop KISS DATA frame; flow-control window exhausted")
            return
        logger.info("kiss data tx bytes=%d", len(payload))
        self._transport.write_frame(KISS_CMD_DATA, payload)

    def flush(self) -> None:
        """Flush pending serial writes."""
        self._transport.flush()

    # -- read-only state -----------------------------------------------------

    @property
    def hello(self) -> Hello | None:
        return self._tracker.hello

    @property
    def is_ready(self) -> bool:
        return self._tracker.hello is not None

    @property
    def physical_ptt(self) -> bool:
        return self._tracker.physical_ptt

    @property
    def mode(self) -> int | None:
        return self._tracker.mode

    # -- incoming frame routing -----------------------------------------------

    def _on_kiss_frame(self, kiss_command: int, payload: bytes) -> None:
        if (kiss_command & 0x0F) != KISS_CMD_SETHARDWARE:
            logger.debug("ignore KISS command=0x%02x bytes=%d", kiss_command, len(payload))
            return

        decoded = decode_vendor_payload(payload)
        if decoded is None:
            logger.warning("ignore non-KV4P vendor frame bytes=%d", len(payload))
            return

        command, body = decoded
        handler = self._dispatch.get(command)
        if handler is None:
            logger.debug("ignore KV4P command=0x%02x bytes=%d", command, len(body))
            return
        try:
            handler(body)
        except Exception:
            logger.exception("failed to handle KV4P command=0x%02x bytes=%d", command, len(body))

    def _handle_debug(self, payload: bytes) -> None:
        text = payload.decode("utf-8", errors="replace").strip()
        if text:
            logger.info("firmware: %s", text)

    def _handle_hello(self, payload: bytes) -> None:
        hello = Hello.from_bytes(payload)
        self._flow.reset(hello.version.window_size)
        self._tracker.on_hello(hello)

    def _handle_device_state(self, payload: bytes) -> None:
        self._tracker.on_device_state(DeviceState.from_bytes(payload))

    def _handle_rx_audio(self, payload: bytes) -> None:
        self._tracker.on_rx_audio(payload)

    def _handle_window_update(self, payload: bytes) -> None:
        size = WindowUpdate.from_bytes(payload).size
        self._flow.add(size)
        if self._window_update_throttle.should_log():
            logger.info("window update size=%d", size)

    # -- outgoing frames -------------------------------------------------------

    def _send_desired_state(self, state: HostDesiredState) -> None:
        self._send_vendor(COMMAND_HOST_DESIRED_STATE, state.to_bytes(), flow_controlled=False)

    def _send_vendor(self, command: int, payload: bytes = b"", *, flow_controlled: bool) -> None:
        vendor_payload = encode_vendor_payload(command, payload)
        if flow_controlled:
            frame_size = len(encode_kiss_frame(KISS_CMD_SETHARDWARE, vendor_payload))
            if not self._flow.claim(frame_size):
                logger.warning("drop command=0x%02x frame; flow-control window exhausted", command)
                return
        self._transport.write_frame(KISS_CMD_SETHARDWARE, vendor_payload)

    def _require_ready(self) -> None:
        if not self.is_ready:
            raise RadioNotReadyError("radio has not completed the HELLO handshake yet")
