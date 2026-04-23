"""Retry helpers for resilient upstream API calls."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class RetryableRequestError(Exception):
    """Exception that marks a failed request as retryable."""

    message: str
    retry_after_seconds: float | None = None
    status_code: int | None = None

    def __str__(self) -> str:
        """Return a readable string representation."""
        return self.message


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay_seconds: float,
    should_retry: Callable[[Exception], bool],
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """Execute an async operation with exponential-backoff retry behavior."""
    attempt = 1

    while True:
        try:
            return await operation()
        except Exception as exc:  # noqa: BLE001
            is_retryable = should_retry(exc)
            is_last_attempt = attempt >= max_attempts

            if not is_retryable or is_last_attempt:
                raise

            delay_seconds = _calculate_delay(exc, attempt, base_delay_seconds)

            if on_retry is not None:
                on_retry(attempt, exc, delay_seconds)

            await asyncio.sleep(delay_seconds)
            attempt += 1


def _calculate_delay(exc: Exception, attempt: int, base_delay_seconds: float) -> float:
    """Calculate retry delay using server hints and exponential backoff with jitter."""
    if isinstance(exc, RetryableRequestError) and exc.retry_after_seconds is not None:
        return max(exc.retry_after_seconds, 0.0)

    exponential_backoff = base_delay_seconds * (2 ** (attempt - 1))
    jitter = random.uniform(0.0, base_delay_seconds)
    return exponential_backoff + jitter
