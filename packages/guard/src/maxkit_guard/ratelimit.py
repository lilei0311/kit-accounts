"""Fixed-window counter for one process.

This is a boundary placeholder. It does not coordinate across workers and it is
not, by itself, an authentication or abuse-prevention control.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


@dataclass
class _Window:
    start: float
    count: int


class MemoryRateLimiter:
    def __init__(self, *, limit: int, window_seconds: float) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be an integer >= 1")
        if isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)):
            raise ValueError("window_seconds must be a positive number")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be a positive number")
        self.limit = limit
        self.window_seconds = float(window_seconds)
        self._windows: dict[str, _Window] = {}

    def check(self, key: str, *, now: float | None = None) -> LimitDecision:
        if not key.strip():
            raise ValueError("key must be non-empty")
        ts = time.monotonic() if now is None else now
        window = self._windows.get(key)
        if window is None or ts - window.start >= self.window_seconds:
            window = _Window(start=ts, count=0)
            self._windows[key] = window
        if window.count >= self.limit:
            retry = self.window_seconds - (ts - window.start)
            return LimitDecision(allowed=False, remaining=0, retry_after_seconds=max(0.0, retry))
        window.count += 1
        return LimitDecision(
            allowed=True,
            remaining=self.limit - window.count,
            retry_after_seconds=0.0,
        )
