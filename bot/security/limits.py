import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

_EVICT_THRESHOLD = 1024  # tracked users before expired windows are swept


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_s: float = 0.0


class RateLimiter:
    """Sliding-window limiter, per user, in memory (resets on restart; fine for the MVP)."""

    def __init__(
        self,
        limit: int,
        window_s: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}

    def acquire(self, user_id: int) -> RateLimitDecision:
        """Record a request if the user is under the limit, otherwise say when to retry."""
        now = self._clock()
        if len(self._hits) > _EVICT_THRESHOLD:
            self._evict_idle(now)

        hits = self._hits.setdefault(user_id, deque())
        while hits and now - hits[0] >= self._window_s:
            hits.popleft()

        if len(hits) >= self._limit:
            return RateLimitDecision(allowed=False, retry_after_s=hits[0] + self._window_s - now)
        hits.append(now)
        return RateLimitDecision(allowed=True)

    def _evict_idle(self, now: float) -> None:
        # Bounded memory: drop users whose whole window has expired.
        idle = [
            uid for uid, hits in self._hits.items() if not hits or now - hits[-1] >= self._window_s
        ]
        for uid in idle:
            del self._hits[uid]
