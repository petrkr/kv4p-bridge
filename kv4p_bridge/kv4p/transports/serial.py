"""Serial transport for the KV4P-HT (ESP32) radio."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..protocol.kiss import KissParser, encode_kiss_frame
from . import Kv4pTransport

logger = logging.getLogger(__name__)

# Standard ESP32 auto-program circuit: RTS drives EN/CHIP_PU (reset), DTR drives
# GPIO0 (boot mode select). pyserial dtr/rts=True is inverted to a LOW level on
# the board through the auto-program transistors. Holding DTR deasserted while
# pulsing RTS resets the chip into its normal firmware (not the ROM bootloader).
_RESET_PULSE_SECONDS = 0.1
_RESET_SETTLE_SECONDS = 0.05


class Kv4pSerialTransport(Kv4pTransport):
    """Blocking serial transport with an RX thread, for use with `Kv4pRadio`."""

    def __init__(self, device: str, baudrate: int) -> None:
        self._device = device
        self._baudrate = baudrate
        self._parser: KissParser | None = None
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()

    def open(self, on_frame: Callable[[int, bytes], None]) -> None:
        """Open serial port and start the RX thread."""
        import serial

        self._parser = KissParser(on_frame)
        self._serial = serial.Serial(self._device, self._baudrate, timeout=0.2)
        self._serial.rts = False
        self._serial.dtr = False
        self._stop.clear()

        self._thread = threading.Thread(
            target=self._read_loop,
            name="kv4p-rx",
            daemon=True,
        )

        self._thread.start()
        logger.info("serial open device=%s baudrate=%d", self._device, self._baudrate)

    def close(self) -> None:
        """Stop RX thread and close serial port."""
        self._stop.set()
        serial_port = self._serial
        self._serial = None

        if serial_port is not None:
            serial_port.close()

        if self._thread is not None:
            self._thread.join(timeout=2)

            if self._thread.is_alive():
                logger.warning("serial RX thread did not stop within timeout")

            self._thread = None

        logger.info("serial closed")

    def reset(self) -> None:
        """Hardware-reset the ESP32 into its normal firmware via an RTS pulse.

        DTR is held deasserted throughout so GPIO0 stays in run mode (not
        bootloader mode); RTS is pulsed to toggle EN/CHIP_PU.
        """
        with self._write_lock:
            if self._serial is None:
                raise RuntimeError("serial transport is not open")
            self._serial.dtr = False
            self._serial.rts = True
            time.sleep(_RESET_PULSE_SECONDS)
            self._serial.rts = False
            time.sleep(_RESET_SETTLE_SECONDS)
        logger.info("serial reset device=%s", self._device)

    def write_frame(self, command: int, payload: bytes) -> None:
        """Write one KISS frame."""
        frame = encode_kiss_frame(command, payload)

        with self._write_lock:
            if self._serial is None:
                raise RuntimeError("serial transport is not open")

            self._serial.write(frame)

        logger.debug("serial tx KISS command=0x%02x payload=%d frame=%d", command, len(payload), len(frame))

    def flush(self) -> None:
        """Wait until serial output is written."""
        with self._write_lock:
            self._serial.flush()

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._serial.read(512)
            except Exception:
                if not self._stop.is_set():
                    logger.exception("serial read failed")
                return

            if data:
                assert self._parser is not None
                self._parser.feed(data)
