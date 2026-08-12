"""Opus helpers for connector-side tests."""

from __future__ import annotations

import math
import struct

import opuslib

OPUS_SAMPLE_RATE = 48000
OPUS_FRAME_SAMPLES = 1920


def tone_packets(duration_sec: float, frequency_hz: float, square: bool = False) -> list[bytes]:
    """Encode a mono 48 kHz test tone into raw Opus packets."""
    total_samples = int(OPUS_SAMPLE_RATE * duration_sec)
    amplitude = 0.75
    pcm = bytearray()
    for sample_index in range(total_samples):
        phase = math.sin(2.0 * math.pi * frequency_hz * sample_index / OPUS_SAMPLE_RATE)
        value = 1.0 if square and phase >= 0.0 else -1.0 if square else phase
        pcm.extend(struct.pack("<h", int(value * amplitude * 32767.0)))
    return encode_pcm_packets(bytes(pcm))


def silence_packet() -> bytes:
    """Encode one 40 ms mono silence frame into one raw Opus packet."""
    packets = encode_pcm_packets(bytes(OPUS_FRAME_SAMPLES * 2))
    if not packets:
        raise RuntimeError("opus encoder produced no packets")
    return packets[0]


def new_encoder() -> opuslib.Encoder:
    """Create a mono 48 kHz Opus encoder."""
    return opuslib.Encoder(OPUS_SAMPLE_RATE, 1, opuslib.APPLICATION_VOIP)


def encode_pcm_packets(pcm_s16le: bytes, encoder: opuslib.Encoder | None = None) -> list[bytes]:
    """Encode 48 kHz mono s16le PCM into raw Opus packets, one per OPUS_FRAME_SAMPLES.

    Opus is a stateful codec (inter-frame prediction), so pass a single
    `encoder` reused across consecutive calls for a continuous stream --
    otherwise each call restarts encoder state and introduces artifacts at
    frame boundaries. Only omit `encoder` for one-shot, self-contained
    encodes of a full buffer in one call (e.g. test tones).
    """
    if encoder is None:
        encoder = new_encoder()
    frame_bytes = OPUS_FRAME_SAMPLES * 2  # 16-bit mono
    packets = []
    for offset in range(0, len(pcm_s16le), frame_bytes):
        frame = pcm_s16le[offset : offset + frame_bytes]
        if len(frame) < frame_bytes:
            frame = frame + bytes(frame_bytes - len(frame))
        packets.append(encoder.encode(frame, OPUS_FRAME_SAMPLES))
    return packets


def new_decoder() -> opuslib.Decoder:
    """Create a mono 48 kHz Opus decoder."""
    return opuslib.Decoder(OPUS_SAMPLE_RATE, 1)


def decode_packet(decoder: opuslib.Decoder, payload: bytes) -> bytes:
    """Decode one raw Opus packet into 16-bit mono PCM."""
    return decoder.decode(payload, OPUS_FRAME_SAMPLES)
