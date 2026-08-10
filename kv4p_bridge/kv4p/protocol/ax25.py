"""AX.25 UI-frame encoding."""

from __future__ import annotations


def ax25_ui_frame(source: str, destination: str, digipeaters: list[str], info: bytes) -> bytes:
    """Build an AX.25 UI frame."""
    addresses = [_ax25_address(destination, last=False), _ax25_address(source, last=not digipeaters)]
    for index, digipeater in enumerate(digipeaters):
        addresses.append(_ax25_address(digipeater, last=index == len(digipeaters) - 1))
    return b"".join(addresses) + bytes((0x03, 0xF0)) + info


def _ax25_address(callsign: str, last: bool) -> bytes:
    call, ssid = _split_ax25_callsign(callsign)
    encoded = bytearray((ord(char) << 1 for char in call.ljust(6)))
    encoded.append(0x60 | ((ssid & 0x0F) << 1) | (0x01 if last else 0x00))
    return bytes(encoded)


def _split_ax25_callsign(value: str) -> tuple[str, int]:
    parts = value.upper().split("-", 1)
    call = parts[0]
    if not call or len(call) > 6:
        raise ValueError(f"invalid AX.25 callsign: {value}")
    ssid = int(parts[1]) if len(parts) == 2 else 0
    if ssid < 0 or ssid > 15:
        raise ValueError(f"invalid AX.25 SSID: {value}")
    return call, ssid
