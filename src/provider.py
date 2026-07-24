from typing import AsyncIterator
import httpx
from src.config import ProviderConfig


class ProviderAdapter:
    """Forwards chat completion requests to an upstream OpenAI-compatible provider."""

    def __init__(self, provider: ProviderConfig):
        self.base_url = provider.base_url.rstrip("/")
        self.api_key = provider.api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    async def chat_completion(self, body: dict) -> dict:
        """Send a non-streaming chat completion request upstream."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = await self._client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def chat_completion_stream(self, body: dict) -> AsyncIterator[str]:
        """Send a streaming chat completion request upstream.

        Yields raw SSE lines (e.g. 'data: {"choices":[...]}').
        Caller is responsible for the 'data: [DONE]' termination.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        stream_body = {**body, "stream": True}
        async with self._client.stream("POST", url, json=stream_body, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    yield line
