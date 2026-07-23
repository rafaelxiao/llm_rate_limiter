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
