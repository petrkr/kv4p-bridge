"""KV4P protocol constants and framing helpers."""

from __future__ import annotations

KISS_FEND = 0xC0
KISS_FESC = 0xDB
KISS_TFEND = 0xDC
KISS_TFESC = 0xDD
KISS_CMD_DATA = 0x00
KISS_CMD_SETHARDWARE = 0x06

KV4P_PROTOCOL_VERSION = 0x01
KV4P_VENDOR_PREFIX = b"KV4P"
KV4P_VENDOR_HEADER_LEN = 6

# Firmware 17 in the v2.0.0.1 Android/FW line uses Opus voice audio on 0x07.
COMMAND_HOST_TX_AUDIO = 0x07
COMMAND_HOST_DESIRED_STATE = 0x0D

COMMAND_DEBUG_INFO = 0x01
COMMAND_DEBUG_ERROR = 0x02
COMMAND_DEBUG_WARN = 0x03
COMMAND_DEBUG_DEBUG = 0x04
COMMAND_DEBUG_TRACE = 0x05
COMMAND_HELLO = 0x06
COMMAND_RX_AUDIO = 0x07
COMMAND_RX_AUDIO_ADPCM = 0x0C
COMMAND_WINDOW_UPDATE = 0x09
COMMAND_DEVICE_STATE = 0x0B


def encode_vendor_payload(command: int, payload: bytes = b"") -> bytes:
    """Build KV4P vendor payload for a KISS SETHARDWARE frame."""
    return KV4P_VENDOR_PREFIX + bytes([KV4P_PROTOCOL_VERSION, command]) + payload


def decode_vendor_payload(payload: bytes) -> tuple[int, bytes] | None:
    """Parse KV4P vendor payload."""
    if len(payload) < KV4P_VENDOR_HEADER_LEN:
        return None
    if payload[:4] != KV4P_VENDOR_PREFIX:
        return None
    if payload[4] != KV4P_PROTOCOL_VERSION:
        return None
    return payload[5], payload[6:]
