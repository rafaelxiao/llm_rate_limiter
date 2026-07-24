import json
import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from src import config as config_module
from src.rate_limiter import ModelRateLimiter, RateLimitTimeoutError
from src.provider import ProviderAdapter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Populated at startup
config: config_module.AppConfig | None = None
rate_limiters: dict[str, ModelRateLimiter] = {}
providers: dict[str, ProviderAdapter] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, rate_limiters, providers
    config = config_module.load_config()
    for m in config.models:
        rate_limiters[m.name] = ModelRateLimiter(
            rpm=m.rpm, tpm=m.tpm, queue_timeout=m.queue_timeout_seconds
        )
    for name, p in config.providers.items():
        providers[name] = ProviderAdapter(p)
    logger.info(f"Started with {len(config.models)} models and {len(config.providers)} providers")
    yield


app = FastAPI(title="LLM Rate Limiter Proxy", lifespan=lifespan)


async def verify_api_key(request: Request):
    """FastAPI dependency — verifies Bearer token against configured api_key."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = auth[len("Bearer "):]
    if token != config.server.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models(_: None = Depends(verify_api_key)):
    return {
        "object": "list",
        "data": [
            {
                "id": m.name,
                "object": "model",
                "created": 0,
                "owned_by": m.provider,
            }
            for m in config.models
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, _: None = Depends(verify_api_key)):
    body = await request.json()
    model_name = body.get("model", "")
    stream = body.get("stream", False)

    # Look up model config
    model_config = next((m for m in config.models if m.name == model_name), None)
    if model_config is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_name}' not found",
        )

    if model_config.provider not in providers:
        raise HTTPException(
            status_code=500,
            detail=f"Provider '{model_config.provider}' not configured",
        )

    provider = providers[model_config.provider]
    limiter = rate_limiters[model_name]

    # Acquire rate limit slot (may queue)
    try:
        await limiter.acquire()
    except RateLimitTimeoutError:
        raise HTTPException(
            status_code=429,
            detail=json.dumps({
                "error": {
                    "message": "rate limit queue timeout",
                    "type": "rate_limit_exceeded",
                }
            }),
        )

    if stream:
        return StreamingResponse(
            _stream_response(provider, body, limiter),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    else:
        try:
            resp = await provider.chat_completion(body)
        except httpx.HTTPStatusError as e:
            limiter.refund_rpm()
            if e.response.status_code == 429:
                limiter.penalize_rpm()
                logger.warning(f"Upstream 429 for model '{model_name}', penalty applied")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.text,
            )
        usage = resp.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        limiter.consume_tpm(total_tokens)
        limiter.reward()
        return JSONResponse(resp)


async def _stream_response(
    provider: ProviderAdapter,
    body: dict,
    limiter: ModelRateLimiter,
):
    """SSE generator for streaming responses. Extracts TPM usage from final chunk."""
    total_tokens = 0
    upstream_error = None
    try:
        async for line in provider.chat_completion_stream(body):
            yield f"{line}\n\n"
            # Try to extract usage from chunks that have usage info
            if '"usage":' in line:
                try:
                    data_str = line[len("data: "):]
                    if data_str == "[DONE]":
                        break
                    data = json.loads(data_str)
                    usage = data.get("usage", {})
                    if usage:
                        total_tokens = usage.get("total_tokens", total_tokens)
                except (json.JSONDecodeError, KeyError):
                    pass
    except httpx.HTTPStatusError as e:
        upstream_error = e
    except httpx.RequestError as e:
        upstream_error = e
    except Exception as e:
        upstream_error = e

    if upstream_error is not None:
        limiter.refund_rpm()
        if isinstance(upstream_error, httpx.HTTPStatusError):
            if upstream_error.response.status_code == 429:
                limiter.penalize_rpm()
                limiter.penalize_tpm()
            status_code = upstream_error.response.status_code
            message = upstream_error.response.text[:500]
            error_type = "upstream_http_error"
        elif isinstance(upstream_error, httpx.RequestError):
            status_code = 502
            message = str(upstream_error) or type(upstream_error).__name__
            error_type = "upstream_connection_error"
        else:
            status_code = 500
            message = str(upstream_error) or type(upstream_error).__name__
            error_type = "upstream_error"
        error_data = {
            "error": {
                "message": message,
                "type": error_type,
                "code": status_code,
            }
        }
        yield f"data: {json.dumps(error_data)}\n\n"
    else:
        limiter.consume_tpm(total_tokens)
        limiter.reward()
    yield "data: [DONE]\n\n"
