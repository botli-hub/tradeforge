"""TradeForge 本地配置加载。

统一从 backend/.env 和系统环境变量读取配置，避免各处自己解析 .env。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

_ENV_LOADED_FLAG = "_TRADEFORGE_ENV_LOADED"
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_PATH = _BACKEND_DIR / ".env"


def load_local_env(env_path: Optional[Path] = None) -> Optional[Path]:
    """按需加载 backend/.env；已有系统环境变量优先。"""
    if os.environ.get(_ENV_LOADED_FLAG) == "1":
        return env_path or _ENV_PATH

    resolved_path = env_path or _ENV_PATH
    if resolved_path.exists():
        for raw_line in resolved_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

    os.environ[_ENV_LOADED_FLAG] = "1"
    return resolved_path


@lru_cache(maxsize=1)
def get_settings() -> dict:
    """返回已加载的基础配置。"""
    load_local_env()
    return {
        "finnhub_api_key": os.getenv("FINNHUB_API_KEY", "").strip(),
        "futu_opend_host": os.getenv("FUTU_OPEND_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "futu_opend_port": int(os.getenv("FUTU_OPEND_PORT", "11111") or 11111),
    }


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    load_local_env()
    return os.getenv(key, default)


def get_backend_dir() -> Path:
    return _BACKEND_DIR


def get_env_path() -> Path:
    return _ENV_PATH
