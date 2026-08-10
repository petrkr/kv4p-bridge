"""Throttling helpers to keep observability out of business logic."""

from __future__ import annotations


class Throttle:
    """Allow logging the first `burst` occurrences, then only every `every`-th."""

    def __init__(self, burst: int = 3, every: int = 100) -> None:
        self._burst = burst
        self._every = every
        self._count = 0

    def should_log(self) -> bool:
        """Advance the counter and return whether this occurrence should be logged."""
        self._count += 1
        return self._count <= self._burst or self._count % self._every == 0


class ChangeGate:
    """Return True only when the key differs from the previous call."""

    def __init__(self) -> None:
        self._last: tuple[object, ...] | None = None

    def changed(self, key: tuple[object, ...]) -> bool:
        """Return whether `key` differs from the last-seen key, and remember it."""
        if key == self._last:
            return False
        self._last = key
        return True
