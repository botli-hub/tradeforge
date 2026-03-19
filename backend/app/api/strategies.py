"""策略API"""
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.signal_engine import StrategySignalEngine
from app.data.adapter import get_adapter
from app.data.database import get_db

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


@router.get("")
async def get_strategies():
    """获取策略列表"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM strategies ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()

    strategies = []
    for row in rows:
        config = _parse_config(row["config"])
        strategies.append({
            "id": row["id"],
            "name": row["name"],
            "mode": row["mode"],
            "status": row["status"],
            "version": row["version"],
            "symbols": config.get("symbols", []),
            "timeframe": config.get("timeframe"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        })

    return strategies


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    """获取策略详情"""
    row = _load_strategy_row(strategy_id)
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")

    return {
        "id": row["id"],
        "name": row["name"],
        "mode": row["mode"],
        "config": _parse_config(row["config"]),
        "status": row["status"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }


@router.post("")
async def create_strategy(data: StrategyCreate):
    """创建策略"""
    strategy_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    config = dict(data.config)
    config["strategy_id"] = strategy_id
    if not config.get("name"):
        config["name"] = data.name

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
            config.get("mode", "visual"),
            json.dumps(config, ensure_ascii=False),
            "ready",
            1,
            now,
            now,
        )
    )
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
    cursor.execute(
        """
        UPDATE strategies
        SET name = ?, config = ?, updated_at = ?, version = ?, status = ?
        WHERE id = ?
        """,
        (data.name, json.dumps(config, ensure_ascii=False), now, new_version, "ready", strategy_id)
    )

    conn.commit()
    conn.close()

    return {"id": strategy_id, "status": "updated"}


@router.delete("/{strategy_id}")
async def delete_strategy(strategy_id: str):
    """删除策略"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
    conn.commit()
    conn.close()

    return {"status": "deleted"}


@router.get("/{strategy_id}/signal")
async def evaluate_strategy_signal(
    strategy_id: str,
    symbol: Optional[str] = Query(None, description="标的代码，不传则取策略首个symbol"),
    adapter: str = Query("mock", description="数据源：mock/futu"),
    host: str = Query("127.0.0.1", description="行情地址"),
    port: int = Query(11111, description="行情端口"),
):
    """实时评估策略信号"""
    row = _load_strategy_row(strategy_id)
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")

    config = _parse_config(row["config"])
    symbols = config.get("symbols") or []
    target_symbol = symbol or (symbols[0] if symbols else None)
    timeframe = config.get("timeframe", "1d")

    if not target_symbol:
        raise HTTPException(status_code=400, detail="策略未配置symbol")

    adapter_instance = None
    try:
        adapter_instance = get_adapter(adapter_type=adapter, host=host, port=port)
        if hasattr(adapter_instance, 'connect') and not adapter_instance.connect():
            raise HTTPException(status_code=502, detail=f"连接{adapter}行情源失败")

        end_date = datetime.now().isoformat()
        start_date = (datetime.now() - timedelta(days=420)).isoformat()
        bars = adapter_instance.get_klines(target_symbol, timeframe, start_date, end_date)
        if not bars:
            detail = getattr(adapter_instance, 'last_error', None) or '策略信号评估未拿到行情数据'
            raise HTTPException(status_code=502, detail=detail)

        df = pd.DataFrame([
            {
                'timestamp': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            }
            for bar in bars
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp')

        engine = StrategySignalEngine(config, df)
        result = engine.evaluate()
        latest_bar = result.get('latest_bar') or {}
        signal_key = f"{strategy_id}:{target_symbol}:{result.get('signal')}:{latest_bar.get('timestamp', '')}"

        return {
            'strategy_id': strategy_id,
            'strategy_name': config.get('name', row['name']),
            'symbol': target_symbol,
            'timeframe': timeframe,
            'adapter': adapter,
            'signal_key': signal_key,
            **result,
        }
    finally:
        if adapter_instance and hasattr(adapter_instance, 'disconnect'):
            try:
                adapter_instance.disconnect()
            except Exception:
                pass
