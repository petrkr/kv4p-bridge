"""Protocol state machine: HELLO handshake, device-state cache, HostDesiredState building."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .constants.messages import (
    DEVICE_STATE_PHYS_PTT_DOWN,
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
)
from .constants.vendor import COMMAND_HOST_TX_AUDIO
from .logging_utils import ChangeGate
from .messages.desired_state import HostDesiredState, bandwidth_to_dra818
from .messages.device_state import DeviceState
from .messages.hello import Hello
from .settings import Kv4pSettings

logger = logging.getLogger(__name__)


def _mode_name(mode: int) -> str:
    if mode == 0:
        return "TX"
    if mode == 1:
        return "RX"
    if mode == 2:
        return "STOPPED"
    return f"UNKNOWN({mode})"


class DeviceStateTracker:
    """Tracks the firmware handshake/state and builds outgoing HostDesiredState frames."""

    def __init__(
        self,
        rx_audio_open: bool,
        status_reports: bool,
        send_desired_state: Callable[[HostDesiredState], None],
        on_rx_audio: Callable[[bytes], None] | None = None,
        on_sql: Callable[[bool], None] | None = None,
    ) -> None:
        self._rx_audio_open = rx_audio_open
        self._status_reports = status_reports
        self._send_desired_state = send_desired_state
        self._on_rx_audio = on_rx_audio
        self._on_sql = on_sql

        self._lock = threading.RLock()
        self._hello_event = threading.Event()
        self._hello: Hello | None = None
        self._device_state: DeviceState | None = None
        self._sequence = 0
        self._flags = self._initial_flags()
        self._settings = Kv4pSettings()
        self._last_sql_open: bool | None = None
        self._last_physical_ptt: bool | None = None
        self._rx_open_sent_after_state = False

        self._status_gate = ChangeGate()

    # -- handshake / incoming state -----------------------------------------

    def on_hello(self, hello: Hello) -> None:
        """Handle a HELLO frame (only ever sent once, right after the ESP32 boots)."""
        with self._lock:
            self._hello = hello
            self._device_state = hello.device_state
            self._sequence = hello.device_state.applied_sequence
            self._flags = self._initial_flags()
            self._rx_open_sent_after_state = False
        logger.info(
            "HELLO firmware=%d window=%d radio=%s range=%.3f-%.3f features=0x%02x",
            hello.version.ver,
            hello.version.window_size,
            hello.version.radio_module_status,
            hello.version.min_radio_freq,
            hello.version.max_radio_freq,
            hello.version.features,
        )
        self._hello_event.set()
        self.on_device_state(hello.device_state)

    def wait_for_hello(self, timeout: float | None = None) -> bool:
        """Block until HELLO has been received."""
        return self._hello_event.wait(timeout=timeout)

    def on_device_state(self, state: DeviceState) -> None:
        """Handle a DEVICE_STATE frame."""
        with self._lock:
            self._device_state = state
            sql_open = state.sql_open
            if state.applied_sequence > self._sequence:
                self._sequence = state.applied_sequence

        self._log_device_status(state)

        if not self._rx_open_sent_after_state:
            self._rx_open_sent_after_state = True
            with self._lock:
                self._flags |= HOST_STATE_RX_AUDIO_OPEN | HOST_STATE_ENABLE_STATUS_REPORTS
                self._send_desired_state_locked()

        if sql_open != self._last_sql_open:
            self._last_sql_open = sql_open
            logger.info("sql %s", "open" if sql_open else "closed")
            if self._on_sql is not None:
                self._on_sql(sql_open)

        physical_ptt = bool(state.flags & DEVICE_STATE_PHYS_PTT_DOWN)
        if physical_ptt != self._last_physical_ptt:
            self._last_physical_ptt = physical_ptt
            logger.info("physical ptt %s", "down" if physical_ptt else "up")

    def on_rx_audio(self, payload: bytes) -> None:
        """Handle an RX audio payload."""
        if self._on_rx_audio is not None:
            self._on_rx_audio(payload)

    # -- outgoing state -------------------------------------------------------

    def request_ptt(self, enabled: bool) -> bool:
        """Set/clear the PTT-requested bit. Returns True if the flags actually changed."""
        with self._lock:
            if enabled and not (self._flags & HOST_STATE_TX_ALLOWED):
                logger.warning("PTT requested while TX is not allowed")
            old_flags = self._flags
            if enabled:
                self._flags |= HOST_STATE_PTT_REQUESTED
            else:
                self._flags &= ~HOST_STATE_PTT_REQUESTED
            if self._flags == old_flags:
                return False
            logger.info("ptt %s", "on" if enabled else "off")
            self._send_desired_state_locked()
            return True

    def apply_settings(self, settings: Kv4pSettings) -> None:
        """Build and send a full HostDesiredState snapshot from `settings`."""
        with self._lock:
            self._settings = settings
            self._flags = self._flags_for_settings(settings)
            self._send_desired_state_locked()

    def _send_desired_state_locked(self) -> None:
        applied_sequence = self._device_state.applied_sequence if self._device_state is not None else 0
        self._sequence = max(self._sequence, applied_sequence) + 1
        state = self._build_desired_state_locked()
        # Release the lock before handing off to I/O: the caller's send callback
        # may block on flow control / serial writes and must not hold up readers
        # of physical_ptt/mode from other threads.
        self._lock.release()
        try:
            self._send_desired_state(state)
        finally:
            self._lock.acquire()
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
        settings = self._settings
        return HostDesiredState(
            sequence=self._sequence,
            memory_id=-1,
            flags=self._flags,
            bw=bandwidth_to_dra818(settings.bandwidth),
            freq_tx=settings.tx_freq,
            freq_rx=settings.rx_freq,
            ctcss_tx=settings.ctcss_tx,
            squelch=settings.squelch,
            ctcss_rx=settings.ctcss_rx,
        )

    # -- derived properties -----------------------------------------------

    @property
    def hello(self) -> Hello | None:
        with self._lock:
            return self._hello

    @property
    def device_state(self) -> DeviceState | None:
        with self._lock:
            return self._device_state

    @property
    def flags(self) -> int:
        with self._lock:
            return self._flags

    @property
    def settings(self) -> Kv4pSettings:
        """Last settings applied via `apply_settings`, or defaults if never called."""
        with self._lock:
            return self._settings

    @property
    def tx_audio_command(self) -> int:
        """TX audio vendor command, derived from the firmware's advertised features."""
        # No feature bit for ADPCM (0x0C) is published upstream yet; once the
        # firmware advertises one, branch on it here instead of guessing from
        # the version number.
        return COMMAND_HOST_TX_AUDIO

    @property
    def physical_ptt(self) -> bool:
        with self._lock:
            if self._device_state is None:
                return False
            return bool(self._device_state.flags & DEVICE_STATE_PHYS_PTT_DOWN)

    @property
    def mode(self) -> int | None:
        with self._lock:
            if self._device_state is None:
                return None
            return self._device_state.mode

    # -- internals -----------------------------------------------------------

    def _initial_flags(self) -> int:
        flags = HOST_STATE_RADIO_CONFIG_VALID
        if self._rx_audio_open:
            flags |= HOST_STATE_RX_AUDIO_OPEN
        if self._status_reports:
            flags |= HOST_STATE_ENABLE_STATUS_REPORTS
        return flags

    def _flags_for_settings(self, settings: Kv4pSettings) -> int:
        flags = self._initial_flags()
        if settings.high_power:
            flags |= HOST_STATE_HIGH_POWER
        if settings.tx_allowed:
            flags |= HOST_STATE_TX_ALLOWED
        if settings.rssi:
            flags |= HOST_STATE_RSSI_ENABLED
        if settings.filter_pre:
            flags |= HOST_STATE_FILTER_PRE
        if settings.filter_high:
            flags |= HOST_STATE_FILTER_HIGH
        if settings.filter_low:
            flags |= HOST_STATE_FILTER_LOW
        # Preserve PTT-requested / rx-open-after-state bits that live outside
        # of Kv4pSettings.
        preserved = self._flags & (HOST_STATE_PTT_REQUESTED | HOST_STATE_RX_AUDIO_OPEN | HOST_STATE_ENABLE_STATUS_REPORTS)
        return flags | preserved

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
        if not self._status_gate.changed(key):
            return
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
