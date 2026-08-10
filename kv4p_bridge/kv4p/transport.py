"""Serial KISS transport."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .protocol import KISS_FEND, KISS_FESC, KISS_TFEND, KISS_TFESC

logger = logging.getLogger(__name__)


class KissParser:
    """Incremental KISS frame parser."""

    def __init__(self, on_frame: Callable[[int, bytes], None]) -> None:
        self._on_frame = on_frame
        self._in_frame = False
        self._escaped = False
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        """Feed serial bytes."""
        for byte in data:
            self._feed_byte(byte)

    def _feed_byte(self, byte: int) -> None:
        if byte == KISS_FEND:
            if self._in_frame and self._buf:
                command = self._buf[0]
                payload = bytes(self._buf[1:])
                logger.debug("serial rx KISS command=0x%02x payload=%d", command, len(payload))
                self._on_frame(command, payload)
            self._buf.clear()
            self._in_frame = True
            self._escaped = False
            return

        if not self._in_frame:
            return

        if self._escaped:
            if byte == KISS_TFEND:
                self._buf.append(KISS_FEND)
            elif byte == KISS_TFESC:
                self._buf.append(KISS_FESC)
            else:
                logger.warning("invalid KISS escape byte 0x%02x", byte)
            self._escaped = False
            return

        if byte == KISS_FESC:
            self._escaped = True
            return

        self._buf.append(byte)


def encode_kiss_frame(command: int, payload: bytes) -> bytes:
    """Encode a KISS frame."""
    out = bytearray([KISS_FEND, command])
    for byte in payload:
        if byte == KISS_FEND:
            out.extend((KISS_FESC, KISS_TFEND))
        elif byte == KISS_FESC:
            out.extend((KISS_FESC, KISS_TFESC))
        else:
            out.append(byte)
    out.append(KISS_FEND)
    return bytes(out)


class KissSerialTransport:
    """Blocking serial transport with RX thread."""

    def __init__(
        self,
        device: str,
        baudrate: int,
        on_frame: Callable[[int, bytes], None],
    ) -> None:
        self._device = device
        self._baudrate = baudrate
        self._parser = KissParser(on_frame)
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()

    def open(self) -> None:
        """Open serial port and start RX thread."""
        import serial

        self._serial = serial.Serial(self._device, self._baudrate, timeout=0.2)
        self._serial.rts = True
        self._serial.dtr = True
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
                self._parser.feed(data)
