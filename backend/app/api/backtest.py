"""回测API - 支持多标的"""
import uuid
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import pandas as pd
from app.data.database import get_db
from app.data.mock import generate_klines, get_stock_info
from app.core.engine import BacktestEngine
from app.core.multi_engine import MultiSymbolEngine

router = APIRouter()

class BacktestParams(BaseModel):
    strategy_id: str
    symbols: List[str]  # 支持多标的
    timeframe: str = "1d"
    start_date: str
    end_date: str
    initial_capital: float = 100000
    fee_rate: float = 0.0003
    slippage: float = 0.001
    position_mode: str = "shared"  # shared / independent

@router.post("/run")
async def run_backtest(params: BacktestParams):
    """执行回测（支持多标的）"""
    # 获取策略配置
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT config FROM strategies WHERE id = ?", (params.strategy_id,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    strategy_config = json.loads(row["config"])
    
    # 合并参数
    strategy_config["risk_rules"]["initial_capital"] = params.initial_capital
    strategy_config["risk_rules"]["fee_rate"] = params.fee_rate
    strategy_config["risk_rules"]["slippage"] = params.slippage
    strategy_config["position_mode"] = params.position_mode
    
    # 获取各标的K线数据
    data = {}
    for symbol in params.symbols:
        info = get_stock_info(symbol)
        klines = generate_klines(
            symbol=symbol,
            timeframe=params.timeframe,
            start_date=params.start_date,
            end_date=params.end_date,
            initial_price=info["base_price"]
        )
        
        if klines:
            df = pd.DataFrame(klines)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.set_index("timestamp", inplace=True)
            data[symbol] = df
    
    # 执行回测
    if len(params.symbols) == 1:
        # 单标的
        symbol = params.symbols[0]
        engine = BacktestEngine(strategy_config, data[symbol])
        result = engine.run()
        result["symbol_results"] = {symbol: result}
    else:
        # 多标的
        engine = MultiSymbolEngine(strategy_config, params.symbols, data)
        result = engine.run()
    
    # 保存回测记录
    backtest_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    cursor.execute("""
        INSERT INTO backtest_runs 
        (id, strategy_id, symbol, timeframe, start_date, end_date, 
         initial_capital, fee_rate, slippage, status, metrics, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        backtest_id,
        params.strategy_id,
        ",".join(params.symbols),
        params.timeframe,
        params.start_date,
        params.end_date,
        params.initial_capital,
        params.fee_rate,
        params.slippage,
        "completed",
        json.dumps(result),
        now
    ))
    
    # 保存成交记录
    for trade in result.get("trades", []):
        trade_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO trades 
            (id, backtest_id, symbol, direction, entry_time, entry_price,
             exit_time, exit_price, quantity, pnl, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id,
            backtest_id,
            trade.get("symbol", params.symbols[0]),
            "long",
            trade.get("entry_time"),
            trade.get("entry_price"),
            trade.get("exit_time"),
            trade.get("exit_price"),
            trade.get("quantity"),
            trade.get("pnl"),
            now
        ))
    
    conn.commit()
    conn.close()
    
    return {
        "id": backtest_id,
        "status": "completed",
        "symbols": params.symbols,
        "metrics": result
    }

@router.get("/{backtest_id}")
async def get_backtest_result(backtest_id: str):
    """获取回测结果"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM backtest_runs WHERE id = ?", (backtest_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="Backtest not found")
    
    return {
        "id": row["id"],
        "strategy_id": row["strategy_id"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "status": row["status"],
        "metrics": json.loads(row["metrics"]) if row["metrics"] else None,
        "created_at": row["created_at"]
    }

@router.get("/{backtest_id}/trades")
async def get_backtest_trades(backtest_id: str):
    """获取回测成交明细"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades WHERE backtest_id = ? ORDER BY entry_time", (backtest_id,))
    rows = cursor.fetchall()
    conn.close()
    
    trades = []
    for row in rows:
        trades.append({
            "id": row["id"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "entry_time": row["entry_time"],
            "entry_price": row["entry_price"],
            "exit_time": row["exit_time"],
            "exit_price": row["exit_price"],
            "quantity": row["quantity"],
            "pnl": row["pnl"]
        })
    
    return trades
