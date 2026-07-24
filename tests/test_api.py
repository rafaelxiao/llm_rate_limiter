import json
import pytest
import httpx
from httpx import AsyncClient, ASGITransport
from src.main import app


@pytest.fixture
async def client():
    """Create an async test client. Each test gets a fresh app with startup."""
    from src import main as main_module
    from src.rate_limiter import ModelRateLimiter
    from src.provider import ProviderAdapter

    test_cfg = {
        "server": {"host": "0.0.0.0", "port": 6767, "api_key": "sk-test-key"},
        "providers": {
            "mockhub": {
                "base_url": "https://mock.example.com/v1",
                "api_key": "sk-mock",
            }
        },
        "models": [
            {
                "name": "mock-model",
                "provider": "mockhub",
                "rpm": 60,
                "tpm": 10000,
                "queue_timeout_seconds": 5.0,
            }
        ],
    }

    # Manually initialize app state (replaces lifespan startup for testing)
    test_cfg_obj = main_module.config_module.AppConfig(**test_cfg)
    main_module.config = test_cfg_obj
    main_module.rate_limiters = {}
    main_module.providers = {}
    for m in test_cfg_obj.models:
        main_module.rate_limiters[m.name] = ModelRateLimiter(
            rpm=m.rpm, tpm=m.tpm, queue_timeout=m.queue_timeout_seconds
        )
    for name, p in test_cfg_obj.providers.items():
        main_module.providers[name] = ProviderAdapter(p)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_check(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_models_requires_auth(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_with_auth(client):
    resp = await client.get(
        "/v1/models",
        headers={"Authorization": "Bearer sk-test-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 1
    assert data["data"][0]["id"] == "mock-model"


@pytest.mark.asyncio
async def test_chat_completion_requires_auth(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": []},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_completion_unknown_model(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "nonexistent", "messages": []},
        headers={"Authorization": "Bearer sk-test-key"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_completion_wrong_api_key(client):
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": []},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_completion_non_streaming(client):
    """Full non-streaming flow: auth, rate limit, upstream call, response."""
    from src.main import providers as main_providers

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hello back"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )

    main_providers["mockhub"]._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "hello back"
    assert data["usage"]["total_tokens"] == 8

    await main_providers["mockhub"]._client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_streaming(client):
    """Full streaming flow: SSE relay with usage extraction."""
    from src.main import providers as main_providers

    async def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
            'data: {"choices":[{"delta":{"content":" world"}}],"usage":{"total_tokens":5}}\n',
        ]
        return httpx.Response(200, content="\n".join(chunks))

    main_providers["mockhub"]._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    lines = resp.text.strip().split("\n\n")
    assert len(lines) >= 2
    assert "hello" in lines[0]
    assert lines[-1] == "data: [DONE]"

    await main_providers["mockhub"]._client.aclose()


@pytest.mark.asyncio
async def test_queue_timeout_returns_429(client):
    """When rate limit is exhausted and timeout expires, returns 429."""
    from src.main import rate_limiters

    # Exhaust the limiter
    limiter = rate_limiters["mock-model"]
    for _ in range(60):
        await limiter.acquire()

    # Now the limiter has no RPM tokens. Set timeout very short.
    limiter.queue_timeout = 0.01

    resp = await client.post(
        "/v1/chat/completions",
        json={
            "model": "mock-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert resp.status_code == 429
    detail = resp.json()["detail"]
    error = json.loads(detail) if isinstance(detail, str) else detail
    assert error["error"]["type"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_upstream_429_refunds_and_penalizes(client):
    """When upstream returns 429, the RPM token is refunded and penalty applied."""
    from src.main import rate_limiters, providers as main_providers

    async def handler(request):
        return httpx.Response(429, json={"error": {"message": "too many requests"}})

    main_providers["mockhub"]._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    # Get RPM before
    limiter = rate_limiters["mock-model"]
    rpm_before = limiter.rpm_bucket.tokens

    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert resp.status_code == 429
    # Penalize halves capacity (60 -> 30), tokens drained to new ceiling
    assert limiter.rpm_bucket.capacity == 30.0

    await main_providers["mockhub"]._client.aclose()


@pytest.mark.asyncio
async def test_streaming_upstream_error_yields_error_chunk(client):
    """Streaming upstream error doesn't crash — it yields an error SSE event."""
    from src.main import providers as main_providers

    async def handler(request):
        return httpx.Response(500, json={"error": "boom"})

    main_providers["mockhub"]._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        headers={"Authorization": "Bearer sk-test-key"},
    )

    # Streaming should still return 200 (SSE framing), but contain error
    assert resp.status_code == 200
    text = resp.text
    assert "upstream_http_error" in text
    assert "data: [DONE]" in text

    await main_providers["mockhub"]._client.aclose()


@pytest.mark.asyncio
async def test_non_streaming_upstream_5xx_refunds_without_penalty(client):
    """Non-429 upstream errors refund RPM but don't penalize."""
    from src.main import rate_limiters, providers as main_providers

    async def handler(request):
        return httpx.Response(503, json={"error": "service unavailable"})

    main_providers["mockhub"]._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    limiter = rate_limiters["mock-model"]
    rpm_before = limiter.rpm_bucket.tokens

    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "mock-model", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        headers={"Authorization": "Bearer sk-test-key"},
    )

    assert resp.status_code == 503
    # 503 refunds RPM but no penalty — should be back to roughly before
    assert limiter.rpm_bucket.tokens == pytest.approx(rpm_before, rel=0.01)

    await main_providers["mockhub"]._client.aclose()
