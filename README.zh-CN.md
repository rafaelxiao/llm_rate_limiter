# LLM Rate Limiter Proxy

[English](./README.md)

一个兼容 OpenAI API 的代理服务，支持按模型限流，超限时排队而非拒绝。

## 功能

- **兼容 OpenAI API** — 可直接替换 `/v1/chat/completions` 和 `/v1/models`
- **排队限流** — 超过 TPM/RPM 限制时请求自动排队，而非返回 429
- **多供应商** — 通过 JSON 配置将不同模型路由到不同上游
- **支持流式** — SSE 流式响应从上游透传
- **零额外依赖** — FastAPI + httpx，`uv run` 即可运行

## 快速开始

### 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### 安装

```bash
git clone https://github.com/rafaelxiao/llm-rate-limiter.git
cd llm-rate-limiter

cp config.json.example config.json
# 编辑 config.json — 填入你的上游 API Key

uv sync
```

### 启动

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 6767
```

也可使用 systemd 管理，参见 [deploy/](./deploy/)。

### 使用

```bash
curl -X POST 'http://localhost:6767/v1/chat/completions' \
  -H 'Authorization: Bearer sk-your-proxy-key-here' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-v4-flash-202605",
    "messages": [
      {"role": "user", "content": "你好！"}
    ],
    "stream": false
  }'
```

## 配置说明

`config.json`（从 `config.json.example` 复制）：

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

| 字段 | 说明 |
|-------|------|
| `server.api_key` | 调用此代理所需的 API Key |
| `providers.<name>` | 上游 LLM 供应商 — `base_url` 和 `api_key` |
| `models[].provider` | 该模型路由到哪个供应商 |
| `models[].rpm` | 每分钟请求数限制 |
| `models[].tpm` | 每分钟 Token 数限制 |
| `models[].queue_timeout_seconds` | 队列最长等待时间（秒），超时返回 429 |

添加新模型 = 在 `models[]` 中加一项。添加新供应商 = 在 `providers` 中加一项。

## 限流原理

每个模型有两个令牌桶（RPM + TPM），持续补充：

- **RPM 桶** — 每个请求消耗 1 个令牌，按 `rpm/60` 每秒补充
- **TPM 桶** — 根据响应中的 `usage.total_tokens` 消耗令牌，按 `tpm/60` 每秒补充

令牌不足时请求排队等待（每 50ms 轮询一次）。等待超过 `queue_timeout_seconds` 时返回 429。

> **为什么在响应后计算 TPM？** 从上游响应中获取实际 token 数，避免引入 tokenizer 库。可能会有短暂的超限，但会自动修正。

## Nginx 反向代理

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

流式响应和排队等待需要关闭缓冲（`proxy_buffering off`）并设置较长的超时时间。

## API

| 端点 | 方法 | 认证 | 说明 |
|----------|--------|------|------|
| `/health` | GET | 无 | 健康检查 |
| `/v1/models` | GET | Bearer token | 列出已配置模型 |
| `/v1/chat/completions` | POST | Bearer token | 聊天补全（支持流式/非流式） |

## 开发

```bash
uv sync --dev      # 安装开发依赖
uv run pytest -v   # 运行测试
```

## 许可证

MIT
