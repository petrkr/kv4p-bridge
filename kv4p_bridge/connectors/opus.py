"""Opus helpers for connector-side tests."""

from __future__ import annotations

import math
import struct
import subprocess


OPUS_SAMPLE_RATE = 48000
OPUS_FRAME_SAMPLES = 1920
OPUS_ENCODER_WARMUP_PACKETS = 12


def tone_packets(duration_sec: float, frequency_hz: float, square: bool = False) -> list[bytes]:
    """Encode a mono 48 kHz test tone into raw Opus packets."""
    warmup_samples = OPUS_ENCODER_WARMUP_PACKETS * OPUS_FRAME_SAMPLES
    total_samples = warmup_samples + int(OPUS_SAMPLE_RATE * duration_sec)
    amplitude = 0.75
    pcm = bytearray()
    for sample_index in range(total_samples):
        tone_sample_index = sample_index - warmup_samples
        phase = math.sin(2.0 * math.pi * frequency_hz * tone_sample_index / OPUS_SAMPLE_RATE)
        value = 1.0 if square and phase >= 0.0 else -1.0 if square else phase
        pcm.extend(struct.pack("<h", int(value * amplitude * 32767.0)))
    return encode_pcm_packets(bytes(pcm))[OPUS_ENCODER_WARMUP_PACKETS:]


def silence_packet() -> bytes:
    """Encode one 40 ms mono silence frame into one raw Opus packet."""
    packets = encode_pcm_packets(bytes(OPUS_FRAME_SAMPLES * 2))
    if not packets:
        raise RuntimeError("ffmpeg produced no Opus packets")
    return packets[0]


def encode_pcm_packets(pcm_s16le: bytes) -> list[bytes]:
    """Encode 48 kHz mono s16le PCM into raw Opus packets via ffmpeg."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(OPUS_SAMPLE_RATE),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-application",
        "voip",
        "-frame_duration",
        "40",
        "-vbr",
        "on",
        "-b:a",
        "24000",
        "-f",
        "opus",
        "pipe:1",
    ]
    proc = subprocess.run(
        command,
        input=pcm_s16le,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return _ogg_opus_packets(proc.stdout)


def _ogg_opus_packets(ogg: bytes) -> list[bytes]:
    packets: list[bytes] = []
    packet = bytearray()
    offset = 0
    while offset + 27 <= len(ogg):
        if ogg[offset:offset + 4] != b"OggS":
            raise ValueError("invalid Ogg capture pattern")
        page_segments = ogg[offset + 26]
        segment_table_start = offset + 27
        segment_table_end = segment_table_start + page_segments
        body_start = segment_table_end
        if segment_table_end > len(ogg):
            raise ValueError("truncated Ogg segment table")
        segment_table = ogg[segment_table_start:segment_table_end]
        body_end = body_start + sum(segment_table)
        if body_end > len(ogg):
            raise ValueError("truncated Ogg page body")
        body_offset = body_start
        for segment_len in segment_table:
            packet.extend(ogg[body_offset:body_offset + segment_len])
            body_offset += segment_len
            if segment_len < 255:
                payload = bytes(packet)
                packet.clear()
                if payload not in {b"OpusHead"} and not payload.startswith(b"OpusHead") and not payload.startswith(b"OpusTags"):
                    packets.append(payload)
        offset = body_end
    return packets
