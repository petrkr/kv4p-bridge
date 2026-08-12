"""Audio connector: live playback/capture via soundcard, keyboard PTT and squelch."""

from __future__ import annotations

import logging
import sys
import threading
import time

from .base import Connector
from .opus import (
    OPUS_FRAME_SAMPLES,
    OPUS_SAMPLE_RATE,
    decode_packet,
    encode_pcm_packets,
    new_decoder,
)

logger = logging.getLogger(__name__)

SQUELCH_MIN = 0
SQUELCH_MAX = 8


class AudioConnector:
    """Connector that bridges RX/TX audio to the system soundcard.

    RX Opus audio is decoded and played live to the output device. The
    input device (microphone) is captured, Opus-encoded and sent as TX
    audio while PTT is held. PTT and squelch are controlled from stdin.
    """

    def __init__(
        self,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
    ) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "audio connector requires the 'sounddevice' package; "
                "install with: pip install kv4p-bridge[audio]"
            ) from exc

        self._sd = sd
        self._input_device = input_device
        self._output_device = output_device

        self._radio = None
        self._stop = threading.Event()
        self._stdin_thread: threading.Thread | None = None

        self._decoder = new_decoder()
        self._out_stream = None
        self._in_stream = None
        self._playback_thread: threading.Thread | None = None
        self._rx_cond = threading.Condition()
        self._rx_frame: bytes | None = None

        self._ptt = False
        self._ptt_lock = threading.Lock()

        self._sql_open = False

        self._squelch = 1
        self._pcm_buffer = bytearray()
        self._frame_bytes = OPUS_FRAME_SAMPLES * 2

    def open(self, radio: object) -> None:
        self._radio = radio
        self._stop.clear()

        self._out_stream = self._sd.RawOutputStream(
            samplerate=OPUS_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self._output_device,
            blocksize=OPUS_FRAME_SAMPLES,
            latency="low",
        )
        self._out_stream.start()

        self._in_stream = self._sd.RawInputStream(
            samplerate=OPUS_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            device=self._input_device,
            blocksize=OPUS_FRAME_SAMPLES,
            callback=self._on_mic_frame,
        )
        self._in_stream.start()

        self._playback_thread = threading.Thread(
            target=self._playback_loop,
            name="audio-playback",
            daemon=True,
        )
        self._playback_thread.start()

        self._stdin_thread = threading.Thread(
            target=self._stdin_loop,
            name="audio-stdin",
            daemon=True,
        )
        self._stdin_thread.start()

        logger.info("audio connector open")
        logger.info(
            "audio commands: p=PTT toggle, 0-%d=set squelch (current=%d)",
            SQUELCH_MAX,
            self._squelch,
        )

    def close(self) -> None:
        self._stop.set()
        with self._ptt_lock:
            self._ptt = False
        with self._rx_cond:
            self._rx_cond.notify_all()
        if self._playback_thread is not None:
            self._playback_thread.join(timeout=1.0)
            self._playback_thread = None
        if self._in_stream is not None:
            self._in_stream.stop()
            self._in_stream.close()
            self._in_stream = None
        if self._out_stream is not None:
            self._out_stream.stop()
            self._out_stream.close()
            self._out_stream = None
        logger.info("audio connector close")
        self._radio = None

    def on_rx_audio(self, payload: bytes) -> None:
        # Called on the radio's single dispatch thread: must never block
        # (a blocking soundcard write here would delay every other event,
        # e.g. on_sql). Decode and hand the frame to the playback thread,
        # replacing any not-yet-played frame instead of queueing a backlog.
        try:
            pcm = decode_packet(self._decoder, payload)
        except Exception:
            logger.exception("audio rx decode failed")
            return
        with self._rx_cond:
            self._rx_frame = pcm
            self._rx_cond.notify()

    def _playback_loop(self) -> None:
        frame_sec = OPUS_FRAME_SAMPLES / OPUS_SAMPLE_RATE
        # ALSA/PortAudio must be fed roughly every frame period regardless
        # of whether new RX audio arrived, or the output stream underruns.
        # But other KISS traffic (e.g. periodic device-state packets) can
        # delay an RX frame past one period without reception actually
        # having stopped, and jumping straight to silence there is audible
        # as a click. So: repeat the last frame for a short grace window
        # (masks jitter, keeps the stream fed), then fall back to silence
        # once the gap is long enough to mean reception really stopped.
        gap_timeout = frame_sec * 4
        silence = bytes(OPUS_FRAME_SAMPLES * 2)
        last_frame = silence
        last_frame_time = 0.0
        try:
            while not self._stop.is_set():
                with self._rx_cond:
                    if self._rx_frame is None:
                        self._rx_cond.wait(timeout=frame_sec)
                    pcm = self._rx_frame
                    self._rx_frame = None
                if self._stop.is_set():
                    continue
                out_stream = self._out_stream
                if out_stream is None:
                    continue

                now = time.monotonic()
                if pcm is not None:
                    last_frame = pcm
                    last_frame_time = now
                    to_write = pcm
                elif now - last_frame_time < gap_timeout:
                    to_write = last_frame
                else:
                    if self._sql_open and last_frame is not silence:
                        logger.warning(
                            "audio playback: RX gap > %.0fms while sql open, filling with silence",
                            gap_timeout * 1000.0,
                        )
                    to_write = silence
                    last_frame = silence

                try:
                    out_stream.write(to_write)
                except Exception:
                    logger.exception("audio playback write failed")
        except Exception:
            logger.exception("audio playback loop failed")

    def on_sql(self, open: bool) -> None:
        self._sql_open = open
        logger.info("audio sql %s", "open" if open else "closed")

    def on_ax25_frame(self, payload: bytes) -> None:
        logger.info("audio ax25 frame rx bytes=%d hex=%s", len(payload), payload.hex(" "))

    def _on_mic_frame(self, indata: object, frames: int, time_info: object, status: object) -> None:
        if status:
            logger.warning("audio mic status: %s", status)
        with self._ptt_lock:
            ptt = self._ptt
        if not ptt:
            return
        radio = self._radio
        if radio is None:
            return
        self._pcm_buffer.extend(bytes(indata))
        while len(self._pcm_buffer) >= self._frame_bytes:
            frame = bytes(self._pcm_buffer[: self._frame_bytes])
            del self._pcm_buffer[: self._frame_bytes]
            try:
                payloads = encode_pcm_packets(frame)
            except Exception:
                logger.exception("audio tx encode failed")
                continue
            for payload in payloads:
                try:
                    radio.send_tx_audio(payload)
                except Exception:
                    logger.exception("audio tx send failed")

    def _stdin_loop(self) -> None:
        try:
            while not self._stop.is_set():
                line = sys.stdin.readline()
                if not line:
                    return
                command = line.strip().lower()
                if command == "p":
                    self._toggle_ptt()
                elif command in {str(n) for n in range(SQUELCH_MIN, SQUELCH_MAX + 1)}:
                    self._set_squelch(int(command))
        except Exception:
            logger.exception("audio stdin loop failed")

    def _toggle_ptt(self) -> None:
        radio = self._radio
        if radio is None:
            return
        with self._ptt_lock:
            self._ptt = not self._ptt
            enabled = self._ptt
        if not enabled:
            self._pcm_buffer.clear()
        logger.info("audio command ptt %s", "on" if enabled else "off")
        radio.set_ptt(enabled)

    def _set_squelch(self, level: int) -> None:
        level = max(SQUELCH_MIN, min(SQUELCH_MAX, level))
        self._squelch = level
        radio = self._radio
        logger.info("audio command squelch=%d", level)
        if radio is not None:
            try:
                radio.set_squelch(level)
            except Exception:
                logger.exception("audio set_squelch failed")


def create_audio_connector(options: dict[str, object]) -> Connector:
    """Create audio connector from config."""
    return AudioConnector(
        input_device=options.get("input_device"),
        output_device=options.get("output_device"),
    )
