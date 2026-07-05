"""后端配置管理 REST API:读写本地 SQLite(app_kv)。

真正的默认值/合并逻辑统一在 app.core.config 中(唯一配置来源),
本模块只负责暴露 GET/PUT /api/config/backend 接口。"""
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import (
    DEFAULT_CONFIG as EDITABLE_DEFAULTS,
    deep_merge,
    get_db_overrides,
    get_effective_config,
)
from app.data.wheel_repository import set_kv

logger = logging.getLogger(__name__)
router = APIRouter()

KV_KEY = "backend_config"


class BackendConfigIn(BaseModel):
    telegram: Optional[Dict[str, Any]] = None
    finnhub_api_key: Optional[str] = None
    finnhub_base_url: Optional[str] = None
    yahoo_base_url: Optional[str] = None
    futu: Optional[Dict[str, Any]] = None
    scan: Optional[Dict[str, Any]] = None
    signal: Optional[Dict[str, Any]] = None
    suggestions: Optional[Dict[str, Any]] = None
    wheel_timing: Optional[Dict[str, Any]] = None
    wheel_position: Optional[Dict[str, Any]] = None


@router.get("/backend")
def get_backend_config():
    """当前生效配置(代码默认值 ← 数据库,后者优先)"""
    return get_effective_config()


@router.put("/backend")
def save_backend_config(body: BackendConfigIn):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    existing = get_db_overrides()
    merged = deep_merge(existing, data)
    set_kv(KV_KEY, json.dumps(merged, ensure_ascii=False))
    # 让配置缓存失效,立即生效(后台线程每轮都会重新读取)
    import app.api.leaps as leaps_mod
    leaps_mod._config_cache = None
    logger.info("后端配置已更新并生效")
    return get_backend_config()
