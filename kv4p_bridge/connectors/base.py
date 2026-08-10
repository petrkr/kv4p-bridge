"""Connector protocol."""

from typing import Protocol


class Connector(Protocol):
    """External side of the bridge."""

    def open(self, radio) -> None:
        """Open connector resources."""

    def close(self) -> None:
        """Close connector resources."""

    def on_rx_audio(self, payload: bytes) -> None:
        """Receive KV4P-native RX audio payload."""

    def on_sql(self, open: bool) -> None:
        """Receive squelch state."""
