"""Transport implementations Kv4pRadio can be constructed with."""

from __future__ import annotations

from collections.abc import Callable


class Kv4pTransport:
    """Documents the interface Kv4pRadio needs from a transport.

    Not enforced via abc/@abstractmethod on purpose: the shape a future
    Bluetooth transport needs isn't known yet, and a formal contract would
    just be a breaking change waiting to happen. Subclass and implement.
    """

    def open(self, on_frame: Callable[[int, bytes], None]) -> None:
        """Open the transport and start delivering incoming KISS frames to `on_frame`."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the transport."""
        raise NotImplementedError

    def write_frame(self, command: int, payload: bytes) -> None:
        """Write one KISS frame."""
        raise NotImplementedError

    def flush(self) -> None:
        """Wait until pending output is written."""
        raise NotImplementedError

    def reset(self) -> None:
        """Reset the radio so it re-announces itself via HELLO."""
        raise NotImplementedError
