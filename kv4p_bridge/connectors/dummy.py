"""Dummy connector."""

from __future__ import annotations

import logging
import math
import struct
import sys
import threading
import time
from pathlib import Path

from kv4p.protocol.ax25 import ax25_ui_frame

from .base import Connector
from .adpcm import decode_block, encode_block
from .opus import OPUS_FRAME_SAMPLES, OPUS_SAMPLE_RATE, silence_packet, tone_packets

logger = logging.getLogger(__name__)

AUDIO_SAMPLE_RATE = 16000
AUDIO_FRAME_SAMPLES = 249
TX_AUDIO_PREROLL_FRAMES = 1
TX_AUDIO_TAIL_FRAMES = 1
TX_AUDIO_BURST_FRAMES = 0


class DummyConnector:
    """Connector that only logs bridge activity."""

    def __init__(self, log_rx_audio: bool = True, log_rx_audio_every: int = 100) -> None:
        self._radio = None
        self._log_rx_audio = log_rx_audio
        self._log_rx_audio_every = log_rx_audio_every
        self._rx_audio_frames = 0
        self._stop = threading.Event()
        self._stdin_thread: threading.Thread | None = None
        self._ptt_keepalive_stop = threading.Event()
        self._ptt_keepalive_thread: threading.Thread | None = None

    def open(self, radio: object) -> None:
        self._radio = radio
        self._stop.clear()
        self._stdin_thread = threading.Thread(
            target=self._stdin_loop,
            name="dummy-stdin",
            daemon=True,
        )
        self._stdin_thread.start()
        logger.info("dummy connector open")
        logger.info(
            "dummy commands: t=TX 1kHz Opus sine, s=TX 1kHz Opus square, "
            "a=TX AFSK diag, o=TX OK1PKR TEST, p=PTT toggle"
        )

    def close(self) -> None:
        self._stop.set()
        self._stop_ptt_keepalive()
        logger.info("dummy connector close")
        self._radio = None

    def on_rx_audio(self, payload: bytes) -> None:
        self._rx_audio_frames += 1
        if self._log_rx_audio and self._rx_audio_frames % self._log_rx_audio_every == 1:
            logger.info(
                "dummy rx audio frame=%d bytes=%d",
                self._rx_audio_frames,
                len(payload),
            )

    def on_sql(self, open: bool) -> None:
        logger.info("dummy sql %s", "open" if open else "closed")

    def on_ax25_frame(self, payload: bytes) -> None:
        logger.info("dummy ax25 frame rx bytes=%d hex=%s", len(payload), payload.hex(" "))

    def _stdin_loop(self) -> None:
        try:
            ptt = False
            while not self._stop.is_set():
                line = sys.stdin.readline()
                if not line:
                    return
                command = line.strip().lower()
                if command == "t":
                    logger.info("dummy command tone")
                    self._send_tone(square=False)
                elif command == "s":
                    logger.info("dummy command square")
                    self._send_tone(square=True)
                elif command == "o":
                    logger.info("dummy command ok1pkr")
                    self._send_ok1pkr_test()
                elif command == "p":
                    ptt = not ptt
                    logger.info("dummy command ptt %s", "on" if ptt else "off")
                    self._set_ptt(ptt, keepalive=ptt)
        except Exception:
            logger.exception("dummy stdin loop failed")

    def _send_tone(self, square: bool) -> None:
        radio = self._radio
        if radio is None:
            return
        kind = "square" if square else "sine"
        logger.info("dummy tx 1kHz %s start", kind)
        self._set_ptt(True, keepalive=False)
        try:
            if not self._wait_for_tx(radio):
                logger.warning("dummy TX tone started before radio reported TX mode")
            _send_opus_silence_preroll(radio)
            payloads = tone_packets(duration_sec=5.0, frequency_hz=1000.0, square=square)
            logger.info(
                "dummy tx 1kHz Opus %s frames=%d first_bytes=%d first=%s",
                kind,
                len(payloads),
                len(payloads[0]) if payloads else 0,
                payloads[0][:16].hex(" ") if payloads else "",
            )
            _send_payloads_paced(radio, payloads, OPUS_FRAME_SAMPLES / OPUS_SAMPLE_RATE)
            silence = silence_packet()
            _send_payloads_paced(radio, [silence] * TX_AUDIO_TAIL_FRAMES, OPUS_FRAME_SAMPLES / OPUS_SAMPLE_RATE)
            if hasattr(radio, "flush"):
                radio.flush()
            time.sleep(0.08)
        finally:
            self._set_ptt(False, keepalive=False)
            logger.info("dummy tx 1kHz %s end", kind)

    def _send_ok1pkr_test(self) -> None:
        radio = self._radio
        if radio is None:
            return
        payload = ax25_ui_frame(
            source="OK1PKR",
            destination="APRS",
            digipeaters=["WIDE1-1"],
            info=b">OK1PKR TEST",
        )
        logger.info("dummy tx ok1pkr test start bytes=%d hex=%s", len(payload), payload.hex(" "))
        radio.send_ax25_frame(payload)
        if hasattr(radio, "flush"):
            radio.flush()
        logger.info("dummy tx ok1pkr test end")

    def _set_ptt(self, enabled: bool, keepalive: bool) -> None:
        radio = self._radio
        if radio is None:
            return
        if not enabled:
            self._stop_ptt_keepalive()
        radio.set_ptt(enabled)
        if enabled and keepalive:
            if not self._wait_for_tx(radio):
                logger.warning("dummy PTT keepalive started before radio reported TX mode")
            self._start_ptt_keepalive(radio)

    def _start_ptt_keepalive(self, radio: object) -> None:
        if self._ptt_keepalive_thread is not None and self._ptt_keepalive_thread.is_alive():
            return
        self._ptt_keepalive_stop.clear()
        logger.info("dummy PTT keepalive start")
        self._ptt_keepalive_thread = threading.Thread(
            target=self._ptt_keepalive_loop,
            args=(radio,),
            name="dummy-ptt-keepalive",
            daemon=True,
        )
        self._ptt_keepalive_thread.start()

    def _stop_ptt_keepalive(self) -> None:
        self._ptt_keepalive_stop.set()
        if self._ptt_keepalive_thread is not None:
            logger.info("dummy PTT keepalive stop")
        self._ptt_keepalive_thread = None

    def _ptt_keepalive_loop(self, radio: object) -> None:
        try:
            silence = silence_packet()
            frames = 0
            while not self._ptt_keepalive_stop.is_set():
                if getattr(radio, "mode", None) != 0:
                    logger.warning("dummy PTT keepalive stop: radio is no longer TX")
                    return
                radio.send_tx_audio(silence)
                frames += 1
                if frames == 1 or frames % 10 == 0:
                    logger.info("dummy PTT keepalive frame=%d bytes=%d", frames, len(silence))
                time.sleep(1.0)
        except Exception:
            logger.exception("dummy PTT keepalive failed")

    @staticmethod
    def _wait_for_tx(radio: object) -> bool:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if getattr(radio, "mode", None) == 0:
                return True
            time.sleep(0.02)
        return False


def create_dummy_connector(options: dict[str, object]) -> Connector:
    """Create dummy connector from config."""
    return DummyConnector(
        log_rx_audio=bool(options.get("log_rx_audio", True)),
        log_rx_audio_every=int(options.get("log_rx_audio_every", 100)),
    )


def _tone_payloads(duration_sec: float, frequency_hz: float, square: bool = False) -> list[bytes]:
    total_samples = int(AUDIO_SAMPLE_RATE * duration_sec)
    amplitude = 24000
    payloads = []
    offset = 0
    while offset < total_samples:
        frame = []
        for i in range(AUDIO_FRAME_SAMPLES):
            sample_index = offset + i
            if sample_index >= total_samples:
                frame.append(0)
            else:
                phase = math.sin(2.0 * math.pi * frequency_hz * sample_index / AUDIO_SAMPLE_RATE)
                if square:
                    sample = 1.0 if phase >= 0.0 else -1.0
                else:
                    sample = phase
                frame.append(int(sample * amplitude))
        payloads.append(encode_block(frame))
        offset += AUDIO_FRAME_SAMPLES
    return payloads


def _send_silence_preroll(radio: object) -> None:
    silence = encode_block([0] * AUDIO_FRAME_SAMPLES)
    logger.info("dummy tx audio preroll frames=%d bytes=%d", TX_AUDIO_PREROLL_FRAMES, len(silence))
    for _ in range(TX_AUDIO_PREROLL_FRAMES):
        radio.send_tx_audio(silence)
        time.sleep(AUDIO_FRAME_SAMPLES / AUDIO_SAMPLE_RATE)


def _send_opus_silence_preroll(radio: object) -> None:
    silence = silence_packet()
    logger.info("dummy tx Opus preroll frames=%d bytes=%d", TX_AUDIO_PREROLL_FRAMES, len(silence))
    _send_payloads_paced(radio, [silence] * TX_AUDIO_PREROLL_FRAMES, OPUS_FRAME_SAMPLES / OPUS_SAMPLE_RATE)


def _send_payloads_paced(radio: object, payloads: list[bytes], frame_sec: float) -> None:
    next_send = time.monotonic()
    for index, payload in enumerate(payloads):
        radio.send_tx_audio(payload)
        if index < TX_AUDIO_BURST_FRAMES:
            continue
        next_send += frame_sec
        delay = next_send - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -frame_sec:
            next_send = time.monotonic()
