"""KISS framing."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..constants.kiss import KISS_FEND, KISS_FESC, KISS_TFEND, KISS_TFESC

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
