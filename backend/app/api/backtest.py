"""回测 API - local-first 历史数据 + 稳定结果结构。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.core.engine import BacktestEngine
from app.core.multi_engine import MultiSymbolEngine
from app.data.database import get_db
from app.data.history_backfill import ensure_local_kline_range, resolve_history_source
from app.data.mock import generate_klines, get_stock_info
from app.data.source_router import normalize_symbol

router = APIRouter()


class BacktestParams(BaseModel):
    strategy_id: str
    symbols: List[str] = Field(default_factory=list)
    timeframe: str = "1d"
    start_date: str
    end_date: str
    initial_capital: float = 100000
    fee_rate: float = 0.0003
    slippage: float = 0.001
    position_mode: str = "shared"
    host: str = "127.0.0.1"
    port: int = 11111
    preferred_adapter: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_symbols(cls, data: Any):
        if not isinstance(data, dict):
            return data
        raw_symbols = data.get("symbols")
        raw_symbol = data.get("symbol")
        if not raw_symbols and raw_symbol:
            raw_symbols = [raw_symbol]
        if isinstance(raw_symbols, str):
            raw_symbols = [part.strip() for part in raw_symbols.split(",") if part.strip()]
        data["symbols"] = raw_symbols or []
        return data


class BacktestDataLoadError(RuntimeError):
    pass


def _load_strategy(strategy_id: str) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return {
        "id": row["id"],
        "name": row["name"],
        "config": json.loads(row["config"]),
    }


def _to_dataframe(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        raise BacktestDataLoadError("未获取到任何 K 线数据")
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    df = df.set_index("timestamp")
    return df[["open", "high", "low", "close", "volume"]]


def _load_symbol_bars(symbol: str, timeframe: str, start_date: str, end_date: str, host: str, port: int, preferred_adapter: Optional[str]) -> Dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    resolved_source = resolve_history_source(normalized_symbol, preferred_adapter)
    load_mode = "local"
    warning = None

    try:
        ensured = ensure_local_kline_range(
            symbol=normalized_symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            host=host,
            port=port,
            preferred_adapter=preferred_adapter,
        )
        rows = ensured.get("bars") or []
        if not rows:
            raise BacktestDataLoadError(f"{normalized_symbol} 本地历史数据为空")
        df = _to_dataframe([
            {
                "timestamp": row["ts"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row.get("volume", 0),
            }
            for row in rows
        ])
        return {
            "symbol": normalized_symbol,
            "dataframe": df,
            "requested_source": resolved_source,
            "data_source": ensured.get("source") or resolved_source,
            "load_mode": load_mode,
            "bar_count": len(df),
            "warning": warning,
        }
    except Exception as local_error:
        info = get_stock_info(normalized_symbol)
        mock_rows = generate_klines(
            symbol=normalized_symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_price=info["base_price"],
        )
        if not mock_rows:
            raise BacktestDataLoadError(f"{normalized_symbol} 历史数据获取失败，且 mock 兜底也为空: {local_error}")
        df = _to_dataframe(mock_rows)
        return {
            "symbol": normalized_symbol,
            "dataframe": df,
            "requested_source": resolved_source,
            "data_source": "mock",
            "load_mode": "fallback_mock",
            "bar_count": len(df),
            "warning": str(local_error),
        }


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _ensure_result_shape(raw_result: Dict[str, Any], strategy: Dict[str, Any], params: BacktestParams, symbol_payloads: List[Dict[str, Any]]) -> Dict[str, Any]:
    metrics = {
        "total_return": raw_result.get("total_return", 0),
        "annual_return": raw_result.get("annual_return", 0),
        "sharpe_ratio": raw_result.get("sharpe_ratio", 0),
        "max_drawdown": raw_result.get("max_drawdown", 0),
        "win_rate": raw_result.get("win_rate", 0),
        "profit_factor": raw_result.get("profit_factor", 0),
        "total_trades": raw_result.get("total_trades", 0),
        "avg_holding_days": raw_result.get("avg_holding_days", 0),
    }
    trades = _json_safe(raw_result.get("trades") or [])
    equity_curve = _json_safe(raw_result.get("equity_curve") or [])

    completed_at = datetime.now().isoformat()
    return {
        "strategy_id": strategy["id"],
        "strategy_name": strategy["name"],
        "symbols": [item["symbol"] for item in symbol_payloads],
        "timeframe": params.timeframe,
        "start_date": params.start_date,
        "end_date": params.end_date,
        "status": "success",
        "completed_at": completed_at,
        "metrics": metrics,
        "trades": trades,
        "equity_curve": equity_curve,
        "symbol_results": _json_safe(raw_result.get("symbol_results", {})),
        "data_sources": [
            {
                "symbol": item["symbol"],
                "requested_source": item["requested_source"],
                "data_source": item["data_source"],
                "load_mode": item["load_mode"],
                "bar_count": item["bar_count"],
                "warning": item.get("warning"),
            }
            for item in symbol_payloads
        ],
    }


def _save_backtest(backtest_id: str, params: BacktestParams, result_payload: Dict[str, Any]):
    result_payload = _json_safe(result_payload)
    conn = get_db()
    cursor = conn.cursor()
    now = result_payload.get("completed_at") or datetime.now().isoformat()

    cursor.execute(
        """
        INSERT INTO backtest_runs
        (id, strategy_id, symbol, timeframe, start_date, end_date,
         initial_capital, fee_rate, slippage, status, metrics, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            backtest_id,
            params.strategy_id,
            ",".join(result_payload["symbols"]),
            params.timeframe,
            params.start_date,
            params.end_date,
            params.initial_capital,
            params.fee_rate,
            params.slippage,
            "completed",
            json.dumps(result_payload, ensure_ascii=False),
            now,
        ),
    )

    for trade in result_payload.get("trades", []):
        cursor.execute(
            """
            INSERT INTO trades
            (id, backtest_id, symbol, direction, entry_time, entry_price,
             exit_time, exit_price, quantity, pnl, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                backtest_id,
                trade.get("symbol") or result_payload["symbols"][0],
                trade.get("direction", "long"),
                str(trade.get("entry_time") or now),
                float(trade.get("entry_price") or 0),
                str(trade.get("exit_time") or now),
                float(trade.get("exit_price") or 0),
                float(trade.get("quantity") or 0),
                float(trade.get("pnl") or 0),
                now,
            ),
        )

    conn.commit()
    conn.close()


@router.post("/run")
async def run_backtest(params: BacktestParams):
    strategy = _load_strategy(params.strategy_id)
    strategy_config = dict(strategy["config"])

    if not params.symbols:
        default_symbols = strategy_config.get("symbols") or []
        if default_symbols:
            params.symbols = [str(default_symbols[0])]

    if not params.symbols:
        raise HTTPException(status_code=400, detail="请至少提供一个回测标的")

    if params.start_date >= params.end_date:
        raise HTTPException(status_code=400, detail="开始日期必须早于结束日期")

    risk_rules = dict(strategy_config.get("risk_rules") or {})
    risk_rules.update({
        "initial_capital": params.initial_capital,
        "fee_rate": params.fee_rate,
        "slippage": params.slippage,
    })
    strategy_config["risk_rules"] = risk_rules
    strategy_config["position_mode"] = params.position_mode

    symbol_payloads: List[Dict[str, Any]] = []
    dataframes: Dict[str, pd.DataFrame] = {}
    data_errors: List[str] = []

    for symbol in params.symbols:
        try:
            payload = _load_symbol_bars(
                symbol=symbol,
                timeframe=params.timeframe,
                start_date=params.start_date,
                end_date=params.end_date,
                host=params.host,
                port=params.port,
                preferred_adapter=params.preferred_adapter,
            )
            symbol_payloads.append(payload)
            dataframes[payload["symbol"]] = payload["dataframe"]
        except Exception as e:
            data_errors.append(f"{symbol}: {e}")

    if not dataframes:
        raise HTTPException(status_code=502, detail="; ".join(data_errors) or "回测数据加载失败")

    symbols = [item["symbol"] for item in symbol_payloads]
    if len(symbols) == 1:
        engine = BacktestEngine(strategy_config, dataframes[symbols[0]])
        raw_result = engine.run()
        raw_result["symbol_results"] = {
            symbols[0]: {
                **_json_safe(raw_result),
                "trades": _json_safe(raw_result.get("trades") or []),
                "equity_curve": _json_safe(raw_result.get("equity_curve") or []),
            }
        }
    else:
        engine = MultiSymbolEngine(strategy_config, symbols, dataframes)
        raw_result = engine.run()

    result_payload = _ensure_result_shape(raw_result, strategy, params, symbol_payloads)
    backtest_id = str(uuid.uuid4())
    result_payload["id"] = backtest_id
    _save_backtest(backtest_id, params, result_payload)

    if data_errors:
        result_payload["warnings"] = data_errors

    return result_payload


@router.get("/{backtest_id}")
async def get_backtest_result(backtest_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM backtest_runs WHERE id = ?", (backtest_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Backtest not found")

    payload = json.loads(row["metrics"]) if row["metrics"] else {}
    payload.setdefault("id", row["id"])
    payload.setdefault("strategy_id", row["strategy_id"])
    payload.setdefault("timeframe", row["timeframe"])
    payload.setdefault("start_date", row["start_date"])
    payload.setdefault("end_date", row["end_date"])
    payload.setdefault("status", row["status"])
    payload.setdefault("created_at", row["created_at"])
    return payload


@router.get("/{backtest_id}/trades")
async def get_backtest_trades(backtest_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades WHERE backtest_id = ? ORDER BY entry_time", (backtest_id,))
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "id": row["id"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "entry_time": row["entry_time"],
            "entry_price": row["entry_price"],
            "exit_time": row["exit_time"],
            "exit_price": row["exit_price"],
            "quantity": row["quantity"],
            "pnl": row["pnl"],
        }
        for row in rows
    ]
