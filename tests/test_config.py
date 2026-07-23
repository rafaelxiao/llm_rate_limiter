import json
import tempfile
from pathlib import Path
import pytest
from src.config import load_config, AppConfig, ServerConfig, ProviderConfig, ModelConfig


def test_load_config_parses_server():
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6767, "api_key": "sk-test"},
        "providers": {},
        "models": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        config = load_config(path)
        assert config.server.host == "0.0.0.0"
        assert config.server.port == 6767
        assert config.server.api_key == "sk-test"
    finally:
        Path(path).unlink()


def test_load_config_parses_providers():
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6767, "api_key": "sk-test"},
        "providers": {
            "tokenhub": {
                "base_url": "https://tokenhub.tencentmaas.com/v1",
                "api_key": "sk-upstream",
            }
        },
        "models": [],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        config = load_config(path)
        assert "tokenhub" in config.providers
        assert config.providers["tokenhub"].base_url == "https://tokenhub.tencentmaas.com/v1"
        assert config.providers["tokenhub"].api_key == "sk-upstream"
    finally:
        Path(path).unlink()


def test_load_config_parses_models():
    cfg = {
        "server": {"host": "0.0.0.0", "port": 6767, "api_key": "sk-test"},
        "providers": {},
        "models": [
            {
                "name": "deepseek-v4-pro-202606",
                "provider": "tokenhub",
                "rpm": 60,
                "tpm": 1000000,
                "queue_timeout_seconds": 300,
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        config = load_config(path)
        assert len(config.models) == 1
        m = config.models[0]
        assert m.name == "deepseek-v4-pro-202606"
        assert m.provider == "tokenhub"
        assert m.rpm == 60
        assert m.tpm == 1000000
        assert m.queue_timeout_seconds == 300
    finally:
        Path(path).unlink()


def test_load_config_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.json")


def test_load_config_missing_server():
    cfg = {"providers": {}, "models": []}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        with pytest.raises(Exception):  # pydantic ValidationError
            load_config(path)
    finally:
        Path(path).unlink()
