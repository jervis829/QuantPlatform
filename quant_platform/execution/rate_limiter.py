"""Exchange API weight-aware rate limiter and exponential backoff scheduler (P6)."""

from __future__ import annotations

from collections import deque
import time
from typing import Callable


class WeightRateLimiter:
    """
    Rolling-window API request weight scheduler with 429/418 exponential backoff.
    Tracks throttle trigger counts for Prometheus observability.
    """

    def __init__(
        self,
        *,
        max_weight_per_window: int = 1200,
        window_seconds: float = 60.0,
        time_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.max_weight_per_window = max_weight_per_window
        self.window_seconds = window_seconds
        self._time_fn = time_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep
        self._events: deque[tuple[float, int]] = deque()
        self._used_weight = 0
        self.throttle_count = 0

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._events and self._events[0][0] <= cutoff:
            _, w = self._events.popleft()
            self._used_weight -= w

    def acquire(self, weight: int = 1) -> float:
        """Reserve `weight` units; sleeps until budget frees up if window is saturated."""
        if weight > self.max_weight_per_window:
            raise ValueError(f"Single request weight {weight} > max_weight_per_window {self.max_weight_per_window}")

        now = self._time_fn()
        self._evict_expired(now)
        waited = 0.0

        while self._used_weight + weight > self.max_weight_per_window and self._events:
            oldest_ts, _ = self._events[0]
            sleep_for = max(0.001, (oldest_ts + self.window_seconds) - now + 1e-6)
            self.throttle_count += 1
            self._sleep_fn(sleep_for)
            waited += sleep_for
            now = self._time_fn()
            self._evict_expired(now)

        self._events.append((now, weight))
        self._used_weight += weight
        return waited

    def record_rate_limit_hit(self, attempt: int = 1, base_backoff_sec: float = 0.5, max_backoff_sec: float = 30.0) -> float:
        """Apply exponential backoff upon receiving HTTP 429 / exchange rate-limit rejection."""
        self.throttle_count += 1
        delay = min(max_backoff_sec, base_backoff_sec * (2 ** max(0, attempt - 1)))
        self._sleep_fn(delay)
        return delay
