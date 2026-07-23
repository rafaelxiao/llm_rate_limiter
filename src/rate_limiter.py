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


import asyncio


class RateLimitTimeoutError(Exception):
    """Raised when a request waits too long in the rate limit queue."""
    pass


class ModelRateLimiter:
    """Per-model rate limiter using dual token buckets (RPM + TPM).

    acquire() blocks until both an RPM slot is available and the TPM
    bucket is non-negative. consume_tpm() is called after the response
    to deduct actual token usage, which may cause subsequent requests
    to block.
    """

    def __init__(self, rpm: int, tpm: int, queue_timeout: float):
        self.rpm_bucket = TokenBucket(capacity=float(rpm), refill_rate=rpm / 60.0)
        self.tpm_bucket = TokenBucket(capacity=float(tpm), refill_rate=tpm / 60.0)
        self.queue_timeout = queue_timeout

    async def acquire(self) -> None:
        """Wait until a request slot is available.

        Blocks until the RPM bucket has at least 1 token AND the TPM
        bucket is non-negative. Polls every 50ms until timeout.
        """
        deadline = time.monotonic() + self.queue_timeout
        while True:
            if time.monotonic() > deadline:
                raise RateLimitTimeoutError(
                    f"Rate limit queue timeout after {self.queue_timeout}s"
                )
            # Check both: RPM has a token, TPM has capacity remaining
            if self.rpm_bucket.has_tokens(1) and self.tpm_bucket.tokens > 0:
                self.rpm_bucket.consume(1)
                return
            await asyncio.sleep(0.05)

    def consume_tpm(self, tokens: int) -> None:
        """Deduct TPM tokens after a response completes."""
        self.tpm_bucket.consume(float(tokens))
