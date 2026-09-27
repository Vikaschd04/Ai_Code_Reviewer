"""In-process throttle for failed credential attempts (defence against local token guessing)."""

from __future__ import annotations

import time
from collections import deque


class FailedAttemptThrottle:
    def __init__(self, max_failures: int = 10, window_seconds: float = 60.0) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._failures: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._failures and now - self._failures[0] > self._window:
            self._failures.popleft()

    def retry_after(self, now: float | None = None) -> int | None:
        """Seconds to wait before another attempt is accepted, or None if attempts are allowed."""
        current = time.monotonic() if now is None else now
        self._prune(current)
        if len(self._failures) < self._max:
            return None
        return max(1, int(self._window - (current - self._failures[0])) + 1)

    def record_failure(self, now: float | None = None) -> None:
        self._failures.append(time.monotonic() if now is None else now)
