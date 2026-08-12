"""SvxLink connector: local UDP audio device, PTY squelch and PTT.

Bridges to SvxLink's "Local" receiver/transmitter UDP audio device
(AUDIO_DEV=udp:host:port, see svxlink.conf(5)) plus its PTY-based squelch
(SQL_DET=PTY) and PTT (PTT_TYPE=PTY) detectors.

RX and TX need separate Local sections with separate ports. SvxLink's
AudioDeviceUDP always sends its TX audio to the exact host:port given in
AUDIO_DEV -- it does not track a peer, so if one section/port is shared for
both RX and TX (RX=KV4P, TX=KV4P on the same AUDIO_DEV), SvxLink's TX
audio loops back to itself instead of reaching us.

This is inherently a same-host/LAN transport: SvxLink always sends TX audio
to a fixed, pre-configured address, with no peer discovery or NAT
traversal. It is not suitable if SvxLink and this bridge are not on the
same reachable network (e.g. the radio side is behind a NAT with no
public/forwarded port) -- that would need SvxLink's TYPE=Net protocol
instead, not implemented here.

Audio wire format expected by SvxLink's udp audio device: raw 16-bit signed
PCM, two interleaved channels, at CARD_SAMPLE_RATE. We run at 48000 to match
the Opus codec used on the radio side without resampling, so svxlink.conf
must have CARD_SAMPLE_RATE=48000.

Squelch PTY protocol: write b"O" for squelch open, b"Z" for squelch closed.
PTT PTY protocol: SvxLink writes b"T" to start transmitting, b"R" to stop.

SvxLink itself creates the PTY (posix_openpt) and symlinks PTY_PATH/PTT_PTY
to its slave device (see AsyncPty.cpp, Pty::open()) -- it is not a path we
create. So this side just opens the path SvxLink created, retrying until it
appears since startup order between svxlink and this bridge isn't guaranteed.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

from .base import Connector
from .opus import (
    OPUS_FRAME_SAMPLES,
    decode_packet,
    encode_pcm_packets,
    new_decoder,
    new_encoder,
)

logger = logging.getLogger(__name__)


class SvxlinkConnector:
    """Connector that bridges RX/TX audio and PTT/squelch to SvxLink's local UDP audio device."""

    def __init__(
        self,
        remote_host: str,
        remote_port: int,
        bind_host: str,
        bind_port: int,
        sql_pty_path: str,
        ptt_pty_path: str,
    ) -> None:
        self._remote_addr = (remote_host, remote_port)
        self._bind_addr = (bind_host, bind_port)
        self._sql_pty_path = sql_pty_path
        self._ptt_pty_path = ptt_pty_path

        self._radio = None
        self._stop = threading.Event()

        self._decoder = new_decoder()
        self._rx_sock: socket.socket | None = None
        self._tx_sock: socket.socket | None = None
        self._tx_thread: threading.Thread | None = None

        self._sql_pty_fd: int | None = None

        self._ptt_pty_fd: int | None = None
        self._ptt_thread: threading.Thread | None = None

        self._pcm_buffer = bytearray()
        self._frame_bytes = OPUS_FRAME_SAMPLES * 2
        # Opus is stateful (inter-frame prediction) -- one encoder instance
        # must be reused across the whole TX stream, not recreated per frame.
        self._encoder = new_encoder()

    def open(self, radio: object) -> None:
        self._radio = radio
        self._stop.clear()

        # RX: SvxLink's Local Rx section binds remote_addr and reads from it,
        # so we just connect()+send() to it -- no bind of our own needed.
        self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx_sock.connect(self._remote_addr)

        # TX: SvxLink's Local Tx section sends its TX audio to bind_addr,
        # so we must bind and listen on that address ourselves.
        self._tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tx_sock.bind(self._bind_addr)
        self._tx_thread = threading.Thread(
            target=self._tx_loop,
            name="svxlink-tx",
            daemon=True,
        )
        self._tx_thread.start()

        self._sql_pty_fd = _wait_and_open_pty(self._sql_pty_path, self._stop)
        self._write_sql(False)

        self._ptt_pty_fd = _wait_and_open_pty(self._ptt_pty_path, self._stop)
        self._ptt_thread = threading.Thread(
            target=self._ptt_loop,
            name="svxlink-ptt",
            daemon=True,
        )
        self._ptt_thread.start()

        logger.info(
            "svxlink connector open remote=%s:%d local=%s:%d sql_pty=%s ptt_pty=%s",
            *self._remote_addr,
            *self._bind_addr,
            self._sql_pty_path,
            self._ptt_pty_path,
        )

    def close(self) -> None:
        self._stop.set()
        if self._rx_sock is not None:
            self._rx_sock.close()
            self._rx_sock = None
        if self._tx_sock is not None:
            # Unblock the blocking recvfrom() in _tx_loop.
            self._tx_sock.close()
        if self._tx_thread is not None:
            self._tx_thread.join(timeout=1.0)
            self._tx_thread = None
        self._tx_sock = None

        if self._ptt_pty_fd is not None:
            os.close(self._ptt_pty_fd)
        if self._ptt_thread is not None:
            self._ptt_thread.join(timeout=1.0)
            self._ptt_thread = None
        self._ptt_pty_fd = None

        if self._sql_pty_fd is not None:
            os.close(self._sql_pty_fd)
            self._sql_pty_fd = None

        logger.info("svxlink connector close")
        self._radio = None

    def on_rx_audio(self, payload: bytes) -> None:
        # Called on the radio's single dispatch thread: must never block.
        # UDP send() is non-blocking for datagrams this small, so it is
        # safe to do inline here (unlike a soundcard write).
        try:
            pcm_mono = decode_packet(self._decoder, payload)
        except Exception:
            logger.exception("svxlink rx decode failed")
            return
        sock = self._rx_sock
        if sock is None:
            return
        try:
            sock.send(_mono_to_stereo(pcm_mono))
        except ConnectionError:
            # SvxLink closes its RX read side (and its UDP bind) whenever it
            # doesn't need it. Nothing is listening on the port then, so
            # this is an expected transient, not a bug.
            pass
        except OSError:
            logger.exception("svxlink rx udp send failed")

    def on_sql(self, open: bool) -> None:
        self._write_sql(open)
        logger.info("svxlink sql %s", "open" if open else "closed")

    def on_ax25_frame(self, payload: bytes) -> None:
        logger.info("svxlink ax25 frame rx bytes=%d hex=%s", len(payload), payload.hex(" "))

    def _write_sql(self, open: bool) -> None:
        fd = self._sql_pty_fd
        if fd is None:
            return
        try:
            os.write(fd, b"O" if open else b"Z")
        except OSError:
            logger.exception("svxlink sql pty write failed")

    def _tx_loop(self) -> None:
        # SvxLink sends TX audio (16-bit stereo interleaved PCM) as UDP
        # datagrams to our bind_addr whenever it wants to transmit.
        sock = self._tx_sock
        try:
            while not self._stop.is_set():
                try:
                    data, _addr = sock.recvfrom(65536)
                except OSError:
                    return
                if not data:
                    continue
                self._pcm_buffer.extend(_stereo_to_mono(data))
                while len(self._pcm_buffer) >= self._frame_bytes:
                    frame = bytes(self._pcm_buffer[: self._frame_bytes])
                    del self._pcm_buffer[: self._frame_bytes]
                    radio = self._radio
                    if radio is None:
                        continue
                    try:
                        payloads = encode_pcm_packets(frame, self._encoder)
                    except Exception:
                        logger.exception("svxlink tx encode failed")
                        continue
                    for tx_payload in payloads:
                        try:
                            radio.send_tx_audio(tx_payload)
                        except Exception:
                            logger.exception("svxlink tx send failed")
        except Exception:
            logger.exception("svxlink tx loop failed")

    def _ptt_loop(self) -> None:
        fd = self._ptt_pty_fd
        try:
            while not self._stop.is_set():
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    return
                if not data:
                    return
                radio = self._radio
                if radio is None:
                    continue
                if b"T" in data:
                    logger.info("svxlink ptt on")
                    radio.set_ptt(True)
                if b"R" in data:
                    logger.info("svxlink ptt off")
                    radio.set_ptt(False)
                    self._pcm_buffer.clear()
        except Exception:
            logger.exception("svxlink ptt loop failed")


def _wait_and_open_pty(path: str, stop: threading.Event, poll_interval: float = 0.2) -> int:
    # SvxLink creates PTY_PATH/PTT_PTY itself at startup (see module
    # docstring), so wait for the symlink to exist before opening it -- the
    # bridge and svxlink may not start in a fixed order.
    logged_wait = False
    while not os.path.exists(path):
        if stop.is_set():
            raise RuntimeError(f"stopped while waiting for svxlink PTY: {path}")
        if not logged_wait:
            logger.info("svxlink waiting for PTY to appear: %s", path)
            logged_wait = True
        time.sleep(poll_interval)
    return os.open(path, os.O_RDWR | os.O_NOCTTY)


def _mono_to_stereo(pcm_mono: bytes) -> bytes:
    stereo = bytearray(len(pcm_mono) * 2)
    stereo[0::4] = pcm_mono[0::2]
    stereo[1::4] = pcm_mono[1::2]
    stereo[2::4] = pcm_mono[0::2]
    stereo[3::4] = pcm_mono[1::2]
    return bytes(stereo)


def _stereo_to_mono(pcm_stereo: bytes) -> bytes:
    # Drop the odd trailing byte, if any, to keep the buffer 16-bit aligned.
    usable = len(pcm_stereo) - (len(pcm_stereo) % 4)
    mono = bytearray(usable // 2)
    mono[0::2] = pcm_stereo[0:usable:4]
    mono[1::2] = pcm_stereo[1:usable:4]
    return bytes(mono)


def create_svxlink_connector(options: dict[str, object]) -> Connector:
    """Create SvxLink local UDP audio connector from config."""
    return SvxlinkConnector(
        remote_host=str(options.get("remote_host", "127.0.0.1")),
        remote_port=int(options.get("remote_port", 10100)),
        bind_host=str(options.get("bind_host", "127.0.0.1")),
        bind_port=int(options.get("bind_port", 10101)),
        sql_pty_path=str(options.get("sql_pty_path", "/tmp/kv4p_sql")),
        ptt_pty_path=str(options.get("ptt_pty_path", "/tmp/kv4p_ptt")),
    )
