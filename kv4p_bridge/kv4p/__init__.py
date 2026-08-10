"""KV4P radio integration."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .protocol import (
    COMMAND_DEVICE_STATE,
    COMMAND_DEBUG_DEBUG,
    COMMAND_DEBUG_ERROR,
    COMMAND_DEBUG_INFO,
    COMMAND_DEBUG_TRACE,
    COMMAND_DEBUG_WARN,
    COMMAND_HELLO,
    COMMAND_HOST_DESIRED_STATE,
    COMMAND_HOST_TX_AUDIO,
    COMMAND_RX_AUDIO_ADPCM,
    COMMAND_RX_AUDIO,
    COMMAND_WINDOW_UPDATE,
    KISS_CMD_DATA,
    KISS_CMD_SETHARDWARE,
    decode_vendor_payload,
    encode_vendor_payload,
)
from .transport import KissSerialTransport, encode_kiss_frame
from .types import (
    DRA818_12K5,
    DRA818_25K,
    HOST_STATE_ENABLE_STATUS_REPORTS,
    HOST_STATE_FILTER_HIGH,
    HOST_STATE_FILTER_LOW,
    HOST_STATE_FILTER_PRE,
    HOST_STATE_HIGH_POWER,
    HOST_STATE_PTT_REQUESTED,
    HOST_STATE_RADIO_CONFIG_VALID,
    HOST_STATE_RSSI_ENABLED,
    HOST_STATE_RX_AUDIO_OPEN,
    HOST_STATE_TX_ALLOWED,
    DEVICE_STATE_PHYS_PTT_DOWN,
    DeviceState,
    Hello,
    HostDesiredState,
    WindowUpdate,
)

logger = logging.getLogger(__name__)


class Kv4pSettings:
    """Radio settings sent in HostDesiredState."""

    def __init__(
        self,
        rx_freq: float = 145.5,
        tx_freq: float = 145.5,
        bandwidth: str = "12.5k",
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


class Kv4pRadio:
    """KV4P-HT radio side of the bridge."""

    def __init__(
        self,
        device: str,
        *,
        baudrate: int = 115200,
        settings: Kv4pSettings | None = None,
        tx_audio_command: int = COMMAND_HOST_TX_AUDIO,
        rx_audio_open: bool = True,
        status_reports: bool = True,
        on_rx_audio: Callable[[bytes], None] | None = None,
        on_sql: Callable[[bool], None] | None = None,
    ) -> None:
        self._device = device
        self._baudrate = baudrate
        self._settings = settings or Kv4pSettings()
        self._rx_audio_open = rx_audio_open
        self._status_reports = status_reports
        self._on_rx_audio_callback = on_rx_audio
        self._on_sql_callback = on_sql
        self._transport: KissSerialTransport | None = None
        self._state_lock = threading.RLock()
        self._flow_lock = threading.Condition()
        self._flow_window = 2048
        self._hello_event = threading.Event()
        self._hello: Hello | None = None
        self._device_state: DeviceState | None = None
        self._sequence = 0
        self._flags = self._initial_flags()
        self._last_sql_open: bool | None = None
        self._last_physical_ptt: bool | None = None
        self._last_status_key: tuple[object, ...] | None = None
        self._open = False
        self._rx_open_sent_after_state = False
        self._tx_audio_frames = 0
        self._tx_audio_command = tx_audio_command
        self._window_updates = 0
        self._tx_status_reports = 0

    def __enter__(self) -> Kv4pRadio:
        try:
            self.open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def open(self) -> None:
        """Open radio transport."""
        if self._open:
            return

        if not self._device:
            logger.warning("kv4p device not configured; radio transport disabled")
            self._open = True
            return

        self._transport = KissSerialTransport(
            self._device,
            self._baudrate,
            self._on_kiss_frame,
        )
        self._transport.open()
        self._open = True
        logger.info("radio open")

    def close(self) -> None:
        """Close radio transport."""
        if not self._open:
            return

        logger.info("radio close")
        try:
            self.set_ptt(False)
        except Exception:
            logger.exception("failed to clear PTT during close")

        if self._transport is not None:
            self._transport.close()
            self._transport = None

        self._open = False
        logger.info("radio closed")

    def set_ptt(self, enabled: bool) -> None:
        """Set PTT requested state."""
        with self._state_lock:
            if enabled and not (self._flags & HOST_STATE_TX_ALLOWED):
                logger.warning("PTT requested while TX is not allowed")
            old_flags = self._flags
            if enabled:
                self._flags |= HOST_STATE_PTT_REQUESTED
            else:
                self._flags &= ~HOST_STATE_PTT_REQUESTED
            if self._flags == old_flags:
                return
            logger.info("ptt %s", "on" if enabled else "off")
            self._send_desired_state_locked()
        if enabled:
            threading.Timer(0.5, self._retry_ptt_if_needed).start()

    def send_tx_audio(self, payload: bytes) -> None:
        """Send KV4P-native TX audio payload."""
        self._tx_audio_frames += 1
        if self._tx_audio_frames <= 3 or self._tx_audio_frames % 100 == 0:
            logger.info(
                "tx audio frame=%d command=0x%02x bytes=%d first=%s",
                self._tx_audio_frames,
                self._tx_audio_command,
                len(payload),
                payload[:16].hex(" "),
            )
        self._send_vendor(self._tx_audio_command, payload)

    def set_tx_audio_command(self, command: int) -> None:
        """Set KV4P TX audio vendor command for diagnostics."""
        self._tx_audio_command = command
        logger.warning("tx audio command set to 0x%02x", command)

    def send_kiss_data(self, payload: bytes) -> None:
        """Send a raw KISS DATA frame for diagnostics."""
        transport = self._transport
        if transport is None:
            logger.debug("drop KISS DATA bytes=%d; transport closed", len(payload))
            return
        frame_size = len(encode_kiss_frame(KISS_CMD_DATA, payload))
        if not self._claim_flow_window(frame_size):
            logger.warning("drop KISS DATA frame; flow-control window exhausted")
            return
        logger.info("kiss data tx bytes=%d", len(payload))
        transport.write_frame(KISS_CMD_DATA, payload)

    def flush(self) -> None:
        """Flush pending serial writes."""
        transport = self._transport
        if transport is not None:
            transport.flush()

    def _retry_ptt_if_needed(self) -> None:
        with self._state_lock:
            if not (self._flags & HOST_STATE_PTT_REQUESTED):
                return
            if self._device_state is not None and self._device_state.mode == 0:
                return
            logger.warning("PTT requested but radio has not reported TX; retrying desired state")
            self._send_desired_state_locked()

    @property
    def physical_ptt(self) -> bool:
        """Return last reported physical PTT state."""
        with self._state_lock:
            if self._device_state is None:
                return False
            return bool(self._device_state.flags & DEVICE_STATE_PHYS_PTT_DOWN)

    @property
    def mode(self) -> int | None:
        """Return last reported firmware mode."""
        with self._state_lock:
            if self._device_state is None:
                return None
            return self._device_state.mode

    def _on_kiss_frame(self, kiss_command: int, payload: bytes) -> None:
        if (kiss_command & 0x0F) != KISS_CMD_SETHARDWARE:
            logger.debug("ignore KISS command=0x%02x bytes=%d", kiss_command, len(payload))
            return

        decoded = decode_vendor_payload(payload)
        if decoded is None:
            logger.warning("ignore non-KV4P vendor frame bytes=%d", len(payload))
            return

        command, body = decoded
        try:
            self._on_vendor_frame(command, body)
        except Exception:
            logger.exception("failed to handle KV4P command=0x%02x bytes=%d", command, len(body))

    def _on_vendor_frame(self, command: int, payload: bytes) -> None:
        if command in {
            COMMAND_DEBUG_INFO,
            COMMAND_DEBUG_ERROR,
            COMMAND_DEBUG_WARN,
            COMMAND_DEBUG_DEBUG,
            COMMAND_DEBUG_TRACE,
        }:
            self._handle_debug(command, payload)
            return

        if command == COMMAND_HELLO:
            hello = Hello.from_bytes(payload)
            with self._state_lock:
                self._hello = hello
                self._device_state = hello.device_state
                self._sequence = hello.device_state.applied_sequence
                self._flags = self._initial_flags()
                self._rx_open_sent_after_state = False
                self._last_status_key = None
            with self._flow_lock:
                self._flow_window = hello.version.window_size
                self._flow_lock.notify_all()
            logger.info(
                "HELLO firmware=%d window=%d radio=%s range=%.3f-%.3f",
                hello.version.ver,
                hello.version.window_size,
                hello.version.radio_module_status,
                hello.version.min_radio_freq,
                hello.version.max_radio_freq,
            )
            self._hello_event.set()
            self._handle_device_state(hello.device_state)
            return

        if command == COMMAND_DEVICE_STATE:
            self._handle_device_state(DeviceState.from_bytes(payload))
            return

        if command in {COMMAND_RX_AUDIO, COMMAND_RX_AUDIO_ADPCM}:
            self._emit_rx_audio(payload)
            return

        if command == COMMAND_WINDOW_UPDATE:
            size = WindowUpdate.from_bytes(payload).size
            with self._flow_lock:
                self._flow_window += size
                self._flow_lock.notify_all()
            self._window_updates += 1
            if self._device_state is not None and self._device_state.mode == 0 and (
                self._window_updates <= 10 or self._window_updates % 100 == 0
            ):
                logger.info("window update count=%d size=%d window=%d", self._window_updates, size, self._flow_window)
            logger.debug("window update size=%d window=%d", size, self._flow_window)
            return

        logger.debug("ignore KV4P command=0x%02x bytes=%d", command, len(payload))

    def _handle_debug(self, command: int, payload: bytes) -> None:
        text = payload.decode("utf-8", errors="replace").strip()
        if not text:
            return
        if command == COMMAND_DEBUG_ERROR:
            logger.error("firmware: %s", text)
        elif command == COMMAND_DEBUG_WARN:
            logger.warning("firmware: %s", text)
        elif command == COMMAND_DEBUG_INFO:
            logger.info("firmware: %s", text)
        else:
            logger.debug("firmware: %s", text)

    def _handle_device_state(self, state: DeviceState) -> None:
        with self._state_lock:
            self._device_state = state
            sql_open = state.sql_open
            if state.applied_sequence > self._sequence:
                self._sequence = state.applied_sequence

        logger.debug(
            "device state applied_sequence=%d flags=0x%04x mode=%d last_error=%d rssi=%d",
            state.applied_sequence,
            state.flags,
            state.mode,
            state.last_error,
            state.latest_rssi,
        )
        if state.mode == 0:
            self._tx_status_reports += 1
            if self._tx_status_reports <= 5 or self._tx_status_reports % 10 == 0:
                logger.info(
                    "tx status report=%d tx_audio_level=%d flags=0x%04x applied_sequence=%d",
                    self._tx_status_reports,
                    state.latest_rssi,
                    state.flags,
                    state.applied_sequence,
                )
        else:
            self._tx_status_reports = 0
        self._log_device_status(state)

        if not self._rx_open_sent_after_state:
            self._rx_open_sent_after_state = True
            with self._state_lock:
                self._flags |= HOST_STATE_RX_AUDIO_OPEN | HOST_STATE_ENABLE_STATUS_REPORTS
                self._send_desired_state_locked()

        if sql_open != self._last_sql_open:
            self._last_sql_open = sql_open
            logger.info("sql %s", "open" if sql_open else "closed")
            self._emit_sql(sql_open)

        physical_ptt = bool(state.flags & DEVICE_STATE_PHYS_PTT_DOWN)
        if physical_ptt != self._last_physical_ptt:
            self._last_physical_ptt = physical_ptt
            logger.info("physical ptt %s", "down" if physical_ptt else "up")

    def _emit_rx_audio(self, payload: bytes) -> None:
        if self._on_rx_audio_callback is None:
            return
        self._on_rx_audio_callback(payload)

    def _emit_sql(self, open: bool) -> None:
        if self._on_sql_callback is None:
            return
        self._on_sql_callback(open)

    def _log_device_status(self, state: DeviceState) -> None:
        key = (
            state.applied_sequence,
            state.flags,
            state.mode,
            state.last_error,
            round(state.freq_rx, 5),
            round(state.freq_tx, 5),
            state.bw,
            state.squelch,
            state.ctcss_rx,
            state.ctcss_tx,
            state.radio_module_status,
            state.latest_rssi if state.mode == 0 else None,
        )
        if key == self._last_status_key:
            return
        self._last_status_key = key
        logger.info(
            (
                "radio status mode=%s sql=%s rx=%.5f tx=%.5f bw=%s "
                "squelch=%d ctcss_rx=%d ctcss_tx=%d flags=0x%04x "
                "applied_sequence=%d error=%d rssi=%d module=%s"
            ),
            _mode_name(state.mode),
            "open" if state.sql_open else "closed",
            state.freq_rx,
            state.freq_tx,
            "25k" if state.bw == DRA818_25K else "12.5k",
            state.squelch,
            state.ctcss_rx,
            state.ctcss_tx,
            state.flags,
            state.applied_sequence,
            state.last_error,
            state.latest_rssi,
            state.radio_module_status,
        )

    def _send_desired_state(self) -> None:
        with self._state_lock:
            self._send_desired_state_locked()

    def _send_desired_state_locked(self) -> None:
        applied_sequence = self._device_state.applied_sequence if self._device_state is not None else 0
        self._sequence = max(self._sequence, applied_sequence) + 1
        state = self._build_desired_state_locked()
        payload = state.to_bytes()
        self._send_vendor(COMMAND_HOST_DESIRED_STATE, payload)
        logger.info(
            (
                "desired state sequence=%d flags=0x%04x rx=%.5f tx=%.5f "
                "bw=%s squelch=%d ctcss_rx=%d ctcss_tx=%d"
            ),
            state.sequence,
            state.flags,
            state.freq_rx,
            state.freq_tx,
            "25k" if state.bw == DRA818_25K else "12.5k",
            state.squelch,
            state.ctcss_rx,
            state.ctcss_tx,
        )

    def _build_desired_state_locked(self) -> HostDesiredState:
        radio = self._settings
        return HostDesiredState(
            sequence=self._sequence,
            memory_id=-1,
            flags=self._flags,
            bw=_bandwidth_value(radio.bandwidth),
            freq_tx=radio.tx_freq,
            freq_rx=radio.rx_freq,
            ctcss_tx=radio.ctcss_tx,
            squelch=radio.squelch,
            ctcss_rx=radio.ctcss_rx,
        )

    def _send_vendor(self, command: int, payload: bytes = b"") -> None:
        transport = self._transport
        if transport is None:
            logger.debug("drop KV4P command=0x%02x bytes=%d; transport closed", command, len(payload))
            return
        vendor_payload = encode_vendor_payload(command, payload)
        frame_size = len(encode_kiss_frame(KISS_CMD_SETHARDWARE, vendor_payload))
        if command == self._tx_audio_command and not self._claim_flow_window(frame_size):
            logger.warning("drop TX audio frame; flow-control window exhausted")
            return
        transport.write_frame(KISS_CMD_SETHARDWARE, vendor_payload)

    def _claim_flow_window(self, size: int) -> bool:
        deadline = time.monotonic() + 1.0
        with self._flow_lock:
            while self._flow_window < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._flow_lock.wait(timeout=remaining)
            self._flow_window -= size
            return True

    def _initial_flags(self) -> int:
        flags = HOST_STATE_RADIO_CONFIG_VALID
        radio = self._settings
        if self._rx_audio_open:
            flags |= HOST_STATE_RX_AUDIO_OPEN
        if self._status_reports:
            flags |= HOST_STATE_ENABLE_STATUS_REPORTS
        if radio.high_power:
            flags |= HOST_STATE_HIGH_POWER
        if radio.tx_allowed:
            flags |= HOST_STATE_TX_ALLOWED
        if radio.rssi:
            flags |= HOST_STATE_RSSI_ENABLED
        if radio.filter_pre:
            flags |= HOST_STATE_FILTER_PRE
        if radio.filter_high:
            flags |= HOST_STATE_FILTER_HIGH
        if radio.filter_low:
            flags |= HOST_STATE_FILTER_LOW
        return flags


def _bandwidth_value(value: str) -> int:
    normalized = value.lower()
    if normalized in {"25k", "25", "wide"}:
        return DRA818_25K
    if normalized in {"12k5", "12.5k", "narrow"}:
        return DRA818_12K5
    raise ValueError(f"unsupported bandwidth: {value}")


def _mode_name(mode: int) -> str:
    if mode == 0:
        return "TX"
    if mode == 1:
        return "RX"
    if mode == 2:
        return "STOPPED"
    return f"UNKNOWN({mode})"
