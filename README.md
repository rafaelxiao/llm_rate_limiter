# LLM Rate Limiter Proxy

[中文文档](./README.zh-CN.md)

An OpenAI-compatible API proxy with per-model rate limiting via queuing — slow down instead of reject.

## Features

- **OpenAI-compatible API** — drop-in replacement for `/v1/chat/completions` and `/v1/models`
- **Rate limiting with queuing** — exceed TPM/RPM limits? Requests queue up instead of getting 429
- **Multi-provider** — route different models to different upstream providers via JSON config
- **Streaming support** — SSE streaming proxied from upstream
- **Zero-dependency runtime** — FastAPI + httpx, `uv run` and you're done

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### Install

```bash
git clone https://github.com/rafaelxiao/llm-rate-limiter.git
cd llm-rate-limiter

cp config.json.example config.json
# Edit config.json — fill in your provider API keys

uv sync
```

### Run

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 6767
```

Or with systemd — see [deploy/](./deploy/).

### Use

```bash
curl -X POST 'http://localhost:6767/v1/chat/completions' \
  -H 'Authorization: Bearer sk-your-proxy-key-here' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v4-flash-202605",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ],
    "stream": false
  }'
```

## Configuration

`config.json` (copy from `config.json.example`):

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 6767,
    "api_key": "sk-your-proxy-key"
  },
  "providers": {
    "tencent": {
      "base_url": "https://tokenhub.tencentmaas.com/v1",
      "api_key": "sk-your-upstream-key"
    }
  },
  "models": [
    {
      "name": "deepseek-v4-flash-202605",
      "provider": "tencent",
      "rpm": 60,
      "tpm": 1000000,
      "queue_timeout_seconds": 300
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `server.api_key` | API key clients use to call this proxy |
| `providers.<name>` | Upstream LLM provider — `base_url` and `api_key` |
| `models[].provider` | Which provider this model routes to |
| `models[].rpm` | Requests per minute limit |
| `models[].tpm` | Tokens per minute limit |
| `models[].queue_timeout_seconds` | Max wait time in queue before returning 429 |

Adding a new model = one entry in `models[]`. Adding a new provider = one entry in `providers`.

## How Rate Limiting Works

Each model has two token buckets (RPM + TPM), refilling continuously:

- **RPM bucket** — one token consumed per request, refills at `rpm/60` per second
- **TPM bucket** — tokens consumed from response `usage.total_tokens`, refills at `tpm/60` per second

When tokens are insufficient, requests wait in queue (polling every 50ms). If the wait exceeds `queue_timeout_seconds`, a 429 is returned.

> **Why post-response TPM counting?** We count tokens from the upstream response, avoiding the need for tokenizer libraries. A brief overshoot is possible but self-correcting.

## Nginx Reverse Proxy

```nginx
location /llm/ {
    proxy_pass http://127.0.0.1:6767/;
    proxy_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

`proxy_buffering off` and long timeouts are required for streaming and queue wait times.

## API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | No | Health check |
| `/v1/models` | GET | Bearer token | List configured models |
| `/v1/chat/completions` | POST | Bearer token | Chat completion (stream + non-stream) |

## Development

```bash
uv sync --dev      # install dev deps
uv run pytest -v   # run tests
```

## License

MIT
