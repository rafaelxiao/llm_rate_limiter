import time


class TokenBucket:
    """A token bucket for rate limiting.

    Tokens refill continuously at refill_rate tokens per second,
    up to the configured capacity.
    """

    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def has_tokens(self, count: float = 1.0) -> bool:
        """Check if bucket has at least `count` tokens."""
        return self.tokens >= count

    def consume(self, count: float = 1.0) -> None:
        """Remove `count` tokens from the bucket. Can go negative."""
        self._refill()
        self._tokens -= count
