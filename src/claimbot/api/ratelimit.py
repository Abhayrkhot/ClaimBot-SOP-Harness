"""Per-client token bucket. In-process; put a shared limiter at the edge for multi-replica deploys."""

from __future__ import annotations

import time
from collections import OrderedDict


class RateLimiter:
    def __init__(self, per_minute: int, burst: int | None = None, max_clients: int = 10_000) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(burst or max(1, per_minute // 4))
        self.max_clients = max_clients
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def allow(self, key: str) -> bool:
        if self.rate <= 0:
            return True
        now = time.monotonic()
        tokens, last = self._buckets.pop(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        allowed = tokens >= 1.0
        self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
        while len(self._buckets) > self.max_clients:
            self._buckets.popitem(last=False)
        return allowed
