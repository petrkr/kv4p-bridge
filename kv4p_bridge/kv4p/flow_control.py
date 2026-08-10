"""HTTP/2-like flow-control window for outgoing vendor frames."""

from __future__ import annotations

import threading
import time


class FlowControlWindow:
    """Tracks how many bytes may still be sent before a WINDOW_UPDATE is required."""

    def __init__(self, initial_size: int = 2048) -> None:
        self._lock = threading.Condition()
        self._size = initial_size

    def reset(self, size: int) -> None:
        """Set the window to a new value (e.g. after HELLO) and wake waiters."""
        with self._lock:
            self._size = size
            self._lock.notify_all()

    def add(self, size: int) -> None:
        """Add bytes freed by a WINDOW_UPDATE and wake waiters."""
        with self._lock:
            self._size += size
            self._lock.notify_all()

    def claim(self, size: int, timeout: float = 1.0) -> bool:
        """Block until `size` bytes are available and deduct them; False on timeout."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._size < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._lock.wait(timeout=remaining)
            self._size -= size
            return True
