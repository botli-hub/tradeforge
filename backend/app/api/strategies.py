"""策略API"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.strategy_runtime import evaluate_strategy
from app.data.database import get_db
from app.data.source_router import resolve_kline_source, resolve_runtime_source

router = APIRouter()


class StrategyCreate(BaseModel):
    name: str
    config: Dict[str, Any]


def _load_strategy_row(strategy_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def _parse_config(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _serialize_strategy_row(row) -> Dict[str, Any]:
    config = _parse_config(row["config"])
    return {
        "id": row["id"],
        "name": row["name"],
        "mode": row["mode"],
        "status": row["status"],
        "version": row["version"],
        "symbols": config.get("symbols", []),
        "timeframe": config.get("timeframe"),
        "config": config,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _save_strategy_version(cursor, strategy_id: str, version: int, config: Dict[str, Any], created_at: str):
    cursor.execute(
        """
        INSERT INTO strategy_versions (id, strategy_id, version, config, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            strategy_id,
            version,
            json.dumps(config, ensure_ascii=False),
            created_at,
        ),
    )


@router.get("")
async def get_strategies():
    """获取策略列表"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM strategies ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [_serialize_strategy_row(row) for row in rows]


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    """获取策略详情"""
    row = _load_strategy_row(strategy_id)
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return _serialize_strategy_row(row)


@router.post("")
async def create_strategy(data: StrategyCreate):
    """创建策略"""
    strategy_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    config = dict(data.config)
    config["strategy_id"] = strategy_id
    if not config.get("name"):
        config["name"] = data.name

    mode = config.get("mode", "visual")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO strategies (id, name, mode, config, status, version, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            data.name,
            mode,
            json.dumps(config, ensure_ascii=False),
            "ready",
            1,
            now,
            now,
        )
    )
    _save_strategy_version(cursor, strategy_id, 1, config, now)
    conn.commit()
    conn.close()

    return {"id": strategy_id, "status": "created"}


@router.put("/{strategy_id}")
async def update_strategy(strategy_id: str, data: StrategyCreate):
    """更新策略"""
    now = datetime.now().isoformat()
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT version FROM strategies WHERE id = ?", (strategy_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Strategy not found")

    config = dict(data.config)
    config["strategy_id"] = strategy_id
    if not config.get("name"):
        config["name"] = data.name

    new_version = row["version"] + 1
    mode = config.get("mode", "visual")
    cursor.execute(
        """
        UPDATE strategies
        SET name = ?, mode = ?, config = ?, updated_at = ?, version = ?, status = ?
        WHERE id = ?
        """,
        (data.name, mode, json.dumps(config, ensure_ascii=False), now, new_version, "ready", strategy_id)
    )
    _save_strategy_version(cursor, strategy_id, new_version, config, now)

    conn.commit()
    conn.close()

    return {"id": strategy_id, "status": "updated"}


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: str):
    """删除策略"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM strategy_versions WHERE strategy_id = ?", (strategy_id,))
    cursor.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
    conn.commit()
    conn.close()

    return {"status": "deleted"}


@router.get("/{strategy_id}/signal")
async def evaluate_strategy_signal(
    strategy_id: str,
    symbol: Optional[str] = Query(None, description="标的代码，不传则取策略首个symbol"),
    adapter: str = Query("auto", description="前端传入的首选入口（实际会走自动路由）"),
    host: str = Query("127.0.0.1", description="行情地址"),
    port: int = Query(11111, description="行情端口"),
):
    """实时评估策略信号（统一走 Strategy Runtime 自动路由）"""
    row = _load_strategy_row(strategy_id)
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")

    config = _parse_config(row["config"])
    symbols = config.get("symbols") or []
    target_symbol = symbol or (symbols[0] if symbols else None)

    if not target_symbol:
        raise HTTPException(status_code=400, detail="策略未配置symbol")

    try:
        result = evaluate_strategy(
            strategy_id=strategy_id,
            symbol=target_symbol,
            timeframe=config.get("timeframe", "1d"),
            trigger_mode="on_quote",
            adapter_type=adapter,
            adapter_host=host,
            adapter_port=port,
            strategy_config=config,
        )
        payload = result.to_dict()
        payload["requested_adapter"] = adapter
        payload["resolved_quote_source"] = resolve_runtime_source(target_symbol, adapter)
        payload["resolved_kline_source"] = resolve_kline_source(target_symbol, adapter)
        return payload
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"策略信号评估失败: {e}")
