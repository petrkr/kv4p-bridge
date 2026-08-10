"""PTT request/retry policy on top of DeviceStateTracker."""

from __future__ import annotations

import logging
import threading

from .constants.messages import HOST_STATE_PTT_REQUESTED
from .state_tracker import DeviceStateTracker

logger = logging.getLogger(__name__)


class PttController:
    """Requests PTT and retries the desired-state send if the firmware doesn't confirm TX."""

    def __init__(self, tracker: DeviceStateTracker, retry_delay: float = 0.5) -> None:
        self._tracker = tracker
        self._retry_delay = retry_delay
        self._retry_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def set(self, enabled: bool) -> None:
        """Request PTT on/off; schedules a retry check when turning on."""
        self._tracker.request_ptt(enabled)
        if enabled:
            self._schedule_retry()
        else:
            self.cancel()

    def cancel(self) -> None:
        """Cancel any pending retry timer. Call this from close()."""
        with self._lock:
            if self._retry_timer is not None:
                self._retry_timer.cancel()
                self._retry_timer = None

    def _schedule_retry(self) -> None:
        with self._lock:
            if self._retry_timer is not None:
                self._retry_timer.cancel()
            self._retry_timer = threading.Timer(self._retry_delay, self._retry_if_needed)
            self._retry_timer.daemon = True
            self._retry_timer.start()

    def _retry_if_needed(self) -> None:
        if not (self._tracker.flags & HOST_STATE_PTT_REQUESTED):
            return
        if self._tracker.mode == 0:
            return
        logger.warning("PTT requested but radio has not reported TX; retrying desired state")
        self._tracker.request_ptt(True)
