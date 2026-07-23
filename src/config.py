from pydantic import BaseModel


class ServerConfig(BaseModel):
    host: str
    port: int
    api_key: str


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str


class ModelConfig(BaseModel):
    name: str
    provider: str
    rpm: int
    tpm: int
    queue_timeout_seconds: float


class AppConfig(BaseModel):
    server: ServerConfig
    providers: dict[str, ProviderConfig]
    models: list[ModelConfig]


def load_config(path: str = "config.json") -> AppConfig:
    """Load and validate configuration from a JSON file."""
    import json

    with open(path) as f:
        data = json.load(f)
    return AppConfig(**data)
