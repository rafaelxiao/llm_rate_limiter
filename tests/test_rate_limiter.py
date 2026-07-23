import time
import pytest
from src.rate_limiter import TokenBucket


def test_token_bucket_initial_tokens():
    bucket = TokenBucket(capacity=100.0, refill_rate=10.0)
    assert bucket.tokens == pytest.approx(100.0, rel=0.01)


def test_token_bucket_has_tokens():
    bucket = TokenBucket(capacity=100.0, refill_rate=10.0)
    assert bucket.has_tokens(50) is True
    assert bucket.has_tokens(200) is False


def test_token_bucket_consume():
    bucket = TokenBucket(capacity=100.0, refill_rate=10.0)
    assert bucket.has_tokens(30) is True
    bucket.consume(30)
    assert bucket.tokens == pytest.approx(70.0, rel=0.01)


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(capacity=100.0, refill_rate=50.0)  # 50 tokens/sec
    bucket.consume(100)  # drain completely
    assert bucket.tokens <= 1.0  # near 0
    time.sleep(1.0)  # wait for refill
    # After 1 second at 50/sec, should have ~50 tokens
    assert bucket.tokens == pytest.approx(50.0, rel=0.1)


def test_token_bucket_does_not_exceed_capacity():
    bucket = TokenBucket(capacity=100.0, refill_rate=1000.0)
    time.sleep(0.5)  # refill would add 500 but capped at 100
    assert bucket.tokens == pytest.approx(100.0, rel=0.01)


def test_token_bucket_consume_can_go_negative():
    bucket = TokenBucket(capacity=100.0, refill_rate=10.0)
    bucket.consume(200)  # consume more than available
    assert bucket.tokens < 0


import asyncio
from src.rate_limiter import ModelRateLimiter, RateLimitTimeoutError


@pytest.mark.asyncio
async def test_rate_limiter_acquire_immediate_when_tokens_available():
    limiter = ModelRateLimiter(rpm=60, tpm=1000, queue_timeout=1.0)
    # Should return immediately since buckets are full
    await asyncio.wait_for(limiter.acquire(), timeout=0.5)


@pytest.mark.asyncio
async def test_rate_limiter_acquire_consumes_rpm():
    limiter = ModelRateLimiter(rpm=2, tpm=1000, queue_timeout=1.0)
    await limiter.acquire()
    await limiter.acquire()
    # 3rd acquire should block since RPM is depleted (rate=2/min)
    # We immediately get a timeout because queue_timeout is short
    with pytest.raises(RateLimitTimeoutError):
        await limiter.acquire()


@pytest.mark.asyncio
async def test_rate_limiter_tpm_consumption_blocks_future_requests():
    limiter = ModelRateLimiter(rpm=60, tpm=500, queue_timeout=0.5)
    # Consume all TPM tokens
    limiter.consume_tpm(600)
    # Now acquire should block because TPM is negative
    with pytest.raises(RateLimitTimeoutError):
        await limiter.acquire()


@pytest.mark.asyncio
async def test_rate_limiter_refills_rpm_over_time():
    limiter = ModelRateLimiter(rpm=120, tpm=1000, queue_timeout=5.0)
    # Drain RPM: 120/min = 2/sec, so drain initial 120 then wait
    for _ in range(120):
        await limiter.acquire()
    # Now bucket is empty, but rate is 2/sec, so after 0.6s we should have ~1.2 tokens
    await asyncio.sleep(0.6)
    # Should be able to acquire again
    await asyncio.wait_for(limiter.acquire(), timeout=1.0)


@pytest.mark.asyncio
async def test_rate_limiter_timeout():
    limiter = ModelRateLimiter(rpm=1, tpm=1000, queue_timeout=0.1)
    await limiter.acquire()  # consume the 1 RPM token
    # Next acquire should timeout quickly since refill rate is 1/60 per sec
    with pytest.raises(RateLimitTimeoutError):
        await limiter.acquire()
