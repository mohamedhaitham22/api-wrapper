"""In-memory rate limiter implementation."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    """Sliding-window in-memory rate limiter keyed by client identifier."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        """Initialize limiter with request budget and time window."""
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        """Return whether request is allowed and retry-after seconds when blocked."""
        now = time.monotonic()

        async with self._lock:
            history = self._requests[key]

            while history and (now - history[0]) > self._window_seconds:
                history.popleft()

            if len(history) >= self._max_requests:
                retry_after = int(self._window_seconds - (now - history[0])) + 1
                return False, max(retry_after, 1)

            history.append(now)
            return True, 0
