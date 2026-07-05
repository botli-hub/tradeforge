"""TradeForge 配置加载 — 唯一配置来源。

设计原则:
- 所有第三方数据源(Yahoo/Finnhub/Futu)地址、密钥(Telegram/Finnhub)、可调参数
  一律保存在本地数据库(app_kv 表),通过设置页读写、立即生效。
- 本模块的 DEFAULT_CONFIG 只是数据库为空时的代码兜底默认值,不含任何真实密钥,
  也不代表"配置来源" —— 真正生效的值永远是 数据库覆盖 优先。
- backend/.env 仅用于本地开发时覆盖极少数与业务无关的运行参数(如有),
  不再作为 Finnhub/Yahoo/Futu 等数据源配置的来源。
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_ENV_LOADED_FLAG = "_TRADEFORGE_ENV_LOADED"
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_PATH = _BACKEND_DIR / ".env"

_KV_KEY = "backend_config"

# 代码内默认值:仅作为数据库为空时的兜底,不含任何真实密钥/账号信息。
DEFAULT_CONFIG: Dict[str, Any] = {
    "telegram": {"bot_token": "", "chat_id": ""},
    "finnhub_api_key": "",
    "finnhub_base_url": "https://finnhub.io/api/v1",
    "yahoo_base_url": "https://query1.finance.yahoo.com/v8/finance/chart",
    "futu": {"host": "127.0.0.1", "port": 11111},
    "scan": {
        "intraday": False,
        "intraday_interval_minutes": 30,
    },
    "signal": {
        "iv_percentile_threshold": 70,
        "ema50_min_bars": 60,
        "ema200_min_bars": 210,
        "contract_cooldown_trading_days": 5,
        "per_symbol_max_30d": 10,
        "intraday_use_last_price": True,
        "contract_dte_min": 30,
        "contract_max_per_symbol": 0,
        "strike_range_pct": 0.20,
    },
    "suggestions": {
        "delta_range": [0.20, 0.30],
        "yield_method": "premium_over_strike",
    },
    "wheel_timing": {
        "dte_min": 30, "dte_max": 500, "contract_max_per_symbol": 0,
        "iv_percentile_threshold": 0, "cooldown_trading_days": 3,
        "auto_scan_minutes": 30,
    },
    "wheel_position": {
        "profit_target_pct": 50, "margin_ratio": 0.25,
        "earnings_warn_days": 14, "weekly_report": True,
    },
}


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


def deep_merge(base: Dict, overlay: Dict) -> Dict:
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def get_db_overrides() -> Dict[str, Any]:
    """读取设置页保存到本地数据库的配置覆盖。"""
    try:
        from app.data.wheel_repository import get_kv
        raw = get_kv(_KV_KEY)
        return json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning("读取本地数据库配置失败: %s", e)
        return {}


def get_effective_config() -> Dict[str, Any]:
    """全项目唯一的“当前生效配置”:代码默认值 ← 数据库覆盖(设置页保存)。"""
    return deep_merge(DEFAULT_CONFIG, get_db_overrides())


def get_settings() -> dict:
    """兼容旧调用点的精简视图;实际值优先取数据库,其次环境变量兜底(仅用于本地脚本调试)。"""
    load_local_env()
    effective = get_effective_config()
    futu = effective.get("futu") or {}
    return {
        "finnhub_api_key": (effective.get("finnhub_api_key") or os.getenv("FINNHUB_API_KEY", "")).strip(),
        "finnhub_base_url": effective.get("finnhub_base_url") or "https://finnhub.io/api/v1",
        "yahoo_base_url": effective.get("yahoo_base_url") or "https://query1.finance.yahoo.com/v8/finance/chart",
        "futu_opend_host": (futu.get("host") or os.getenv("FUTU_OPEND_HOST", "127.0.0.1") or "127.0.0.1").strip(),
        "futu_opend_port": int(futu.get("port") or os.getenv("FUTU_OPEND_PORT", "11111") or 11111),
    }


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    load_local_env()
    return os.getenv(key, default)


def get_backend_dir() -> Path:
    return _BACKEND_DIR


def get_env_path() -> Path:
    return _ENV_PATH
