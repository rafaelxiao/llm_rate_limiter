import json
import pytest
import httpx
from src.config import ProviderConfig
from src.provider import ProviderAdapter


def _make_adapter(base_url: str = "https://example.com/v1") -> ProviderAdapter:
    return ProviderAdapter(
        ProviderConfig(base_url=base_url, api_key="sk-test-key")
    )


@pytest.mark.asyncio
async def test_chat_completion_sends_correct_request():
    """Use httpx.MockTransport to verify the outgoing request shape."""
    captured_request = None
    captured_body = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request, captured_body
        captured_request = request
        captured_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"total_tokens": 10},
            },
        )

    adapter = _make_adapter()
    # Replace internal client with one using mock transport
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    body = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
    resp = await adapter.chat_completion(body)

    assert captured_request.method == "POST"
    assert captured_request.url == "https://example.com/v1/chat/completions"
    assert captured_request.headers["Authorization"] == "Bearer sk-test-key"
    assert captured_body == body
    assert resp["choices"][0]["message"]["content"] == "hello"
    assert resp["usage"]["total_tokens"] == 10

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_stream_relays_chunks():
    chunks = [
        'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
        'data: {"choices":[{"delta":{"content":" world"}}],"usage":{"total_tokens":5}}\n',
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="\n".join(chunks))

    adapter = _make_adapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    body = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}
    received = []
    async for line in adapter.chat_completion_stream(body):
        received.append(line)

    assert len(received) == 2
    assert received[0] == chunks[0].rstrip("\n")
    assert received[1] == chunks[1].rstrip("\n")

    await adapter._client.aclose()


@pytest.mark.asyncio
async def test_chat_completion_http_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal error"})

    adapter = _make_adapter()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    body = {"model": "test", "messages": []}
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.chat_completion(body)

    await adapter._client.aclose()
