"""统一策略执行层 - Strategy Runtime Engine v1.1

核心设计：
- 两种触发模式：
  - ON_QUOTE（实时触发）：每次quote进来立即计算信号
  - ON_BAR_CLOSE（收盘触发）：等bar收盘才计算
- 默认使用 ON_QUOTE 实时模式
- 信号评估使用 "历史bars + forming bar" 的混合数据

评估流程：
1. 获取 MarketState（包含历史bars和实时forming bar）
2. 转换为 DataFrame（混合数据用于指标计算）
3. 调用 SignalEngine 评估
4. 判断是否基于实时bar触发
5. 返回标准化的 SignalResult
"""
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Literal
from dataclasses import dataclass, field

import pandas as pd

from app.core.market_state import (
    MarketState,
    market_state_to_dataframe,
    get_market_state,
    TriggerMode,
)
from app.core.signal_engine import StrategySignalEngine


@dataclass
class SignalResult:
    """
    策略信号结果
    
    字段说明：
    - signal: BUY / SELL / NONE
    - reason: 信号原因
    - is_live_triggered: 是否基于实时forming bar触发
    - trigger_mode: ON_QUOTE / ON_BAR_CLOSE
    - entry/exit_details: 入场/出场触发详情
    - latest_bar: 最新K线（可能是forming bar）
    - indicators: 指标值
    """
    strategy_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    signal: str
    reason: str
    trigger_mode: str  # ON_QUOTE / ON_BAR_CLOSE
    is_live_triggered: bool  # 是否基于实时bar触发
    entry_triggered: bool
    exit_triggered: bool
    entry_details: Dict[str, Any]
    exit_details: Dict[str, Any]
    latest_bar: Optional[Dict[str, Any]]
    indicators: Dict[str, Any]
    signal_key: str
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal": self.signal,
            "reason": self.reason,
            "trigger_mode": self.trigger_mode,
            "is_live_triggered": self.is_live_triggered,
            "entry_triggered": self.entry_triggered,
            "exit_triggered": self.exit_triggered,
            "entry_details": self.entry_details,
            "exit_details": self.exit_details,
            "latest_bar": self.latest_bar,
            "indicators": self.indicators,
            "signal_key": self.signal_key,
            "timestamp": self.timestamp,
        }


# ========== 策略执行入口 ==========

def evaluate_strategy(
    strategy_id: str,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    trigger_mode: Literal["on_quote", "on_bar_close"] = "on_quote",
    adapter_type: str = "mock",
    adapter_host: str = "127.0.0.1",
    adapter_port: int = 11111,
    strategy_config: Optional[Dict[str, Any]] = None,
) -> SignalResult:
    """
    策略信号评估入口
    
    Args:
        strategy_id: 策略ID（与strategy_config二选一）
        symbol: 标的代码
        timeframe: 时间周期
        trigger_mode: 触发模式 on_quote / on_bar_close
        adapter_type: 数据源
        adapter_host: 行情地址
        adapter_port: 行情端口
        strategy_config: 策略配置（可选，直接传入不查库）
    
    Returns:
        SignalResult
    """
    # 1. 加载策略配置
    if strategy_config is None:
        config = _load_strategy_config(strategy_id)
        if not config:
            return _error_result(strategy_id, "策略不存在", symbol or "UNKNOWN")
    else:
        config = strategy_config
        config["_strategy_id"] = strategy_id
    
    # 2. 确定标的和时间周期
    symbols = config.get("symbols") or []
    target_symbol = symbol or (symbols[0] if symbols else None)
    target_timeframe = timeframe or config.get("timeframe", "1d")
    
    if not target_symbol:
        return _error_result(strategy_id, "策略未配置symbol", symbol or "UNKNOWN")
    
    # 3. 获取市场状态
    market_state = get_market_state(
        symbol=target_symbol,
        timeframe=target_timeframe,
        trigger_mode=trigger_mode,
        history_days=420,
        adapter_type=adapter_type,
        adapter_host=adapter_host,
        adapter_port=adapter_port,
    )
    
    return _evaluate_with_market_state(
        strategy_id=strategy_id,
        config=config,
        symbol=target_symbol,
        timeframe=target_timeframe,
        market_state=market_state,
    )


def evaluate_strategy_on_quote(
    strategy_id: str,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    adapter_type: str = "mock",
    adapter_host: str = "127.0.0.1",
    adapter_port: int = 11111,
) -> SignalResult:
    """
    实时触发模式：每次quote进来立即计算信号
    
    等同于 evaluate_strategy(..., trigger_mode="on_quote")
    """
    return evaluate_strategy(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        trigger_mode="on_quote",
        adapter_type=adapter_type,
        adapter_host=adapter_host,
        adapter_port=adapter_port,
    )


def evaluate_strategy_on_bar_close(
    strategy_id: str,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    adapter_type: str = "mock",
    adapter_host: str = "127.0.0.1",
    adapter_port: int = 11111,
) -> SignalResult:
    """
    收盘触发模式：等bar收盘才计算信号
    
    等同于 evaluate_strategy(..., trigger_mode="on_bar_close")
    """
    return evaluate_strategy(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        trigger_mode="on_bar_close",
        adapter_type=adapter_type,
        adapter_host=adapter_host,
        adapter_port=adapter_port,
    )


def _evaluate_with_market_state(
    strategy_id: str,
    config: Dict[str, Any],
    symbol: str,
    timeframe: str,
    market_state: MarketState,
) -> SignalResult:
    """使用已有市场状态评估策略"""
    # 1. 转换为DataFrame
    df = market_state_to_dataframe(market_state)
    
    if df.empty or len(df) < 2:
        return SignalResult(
            strategy_id=strategy_id,
            strategy_name=config.get("name") or config.get("_db_name", "Unknown"),
            symbol=symbol,
            timeframe=timeframe,
            signal="NONE",
            reason="数据不足，无法评估",
            trigger_mode=market_state.trigger_mode.value,
            is_live_triggered=False,
            entry_triggered=False,
            exit_triggered=False,
            entry_details={"error": "数据不足"},
            exit_details={"error": "数据不足"},
            latest_bar=None,
            indicators={},
            signal_key="",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    
    # 2. 调用信号引擎
    engine = StrategySignalEngine(config, df)
    result = engine.evaluate()
    
    # 3. 判断是否基于实时bar触发
    is_live_triggered = (
        market_state.trigger_mode == TriggerMode.ON_QUOTE and
        market_state.forming_bar is not None and
        (result.get("entry_triggered") or result.get("exit_triggered"))
    )
    
    # 4. 构建signal_key
    latest_bar = market_state.get_latest_bar_for_signal()
    signal_key = f"{strategy_id}:{symbol}:{result.get('signal')}:{latest_bar.get('timestamp', latest_bar.get('period_start', '')) if latest_bar else ''}:{market_state.trigger_mode.value}"
    
    return SignalResult(
        strategy_id=strategy_id,
        strategy_name=config.get("name") or config.get("_db_name", "Unknown"),
        symbol=symbol,
        timeframe=timeframe,
        signal=result.get("signal", "NONE"),
        reason=result.get("reason", ""),
        trigger_mode=market_state.trigger_mode.value,
        is_live_triggered=is_live_triggered,
        entry_triggered=result.get("entry_triggered", False),
        exit_triggered=result.get("exit_triggered", False),
        entry_details=result.get("entry", {}),
        exit_details=result.get("exit", {}),
        latest_bar=latest_bar,
        indicators=result.get("indicators", {}),
        signal_key=signal_key,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _load_strategy_config(strategy_id: str) -> Optional[Dict[str, Any]]:
    """从数据库加载策略配置"""
    from app.data.database import get_db
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return None
    
    config = row["config"]
    if isinstance(config, str):
        config = json.loads(config)
    
    config["_db_name"] = row["name"]
    config["_db_mode"] = row["mode"]
    config["_db_status"] = row["status"]
    config["_strategy_id"] = strategy_id
    
    return config


def _error_result(strategy_id: str, error_msg: str, symbol: str) -> SignalResult:
    """构建错误结果"""
    return SignalResult(
        strategy_id=strategy_id,
        strategy_name="Unknown",
        symbol=symbol,
        timeframe="1d",
        signal="NONE",
        reason=error_msg,
        trigger_mode="unknown",
        is_live_triggered=False,
        entry_triggered=False,
        exit_triggered=False,
        entry_details={"error": error_msg},
        exit_details={"error": error_msg},
        latest_bar=None,
        indicators={},
        signal_key="",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ========== 批量评估 ==========

def evaluate_all_strategies_on_symbol(
    symbol: str,
    timeframe: str = "1d",
    trigger_mode: Literal["on_quote", "on_bar_close"] = "on_quote",
    adapter_type: str = "mock",
) -> List[SignalResult]:
    """
    评估所有订阅了该标的的策略
    
    Returns:
        List[SignalResult]: 所有策略的信号结果
    """
    from app.data.database import get_db
    
    conn = get_db()
    cursor = conn.cursor()
    
    # 查询配置了此symbol的策略
    cursor.execute(
        """
        SELECT id, name, config FROM strategies 
        WHERE status = 'ready' 
        AND config LIKE ?
        ORDER BY updated_at DESC
        """,
        (f'%{symbol}%',)
    )
    rows = cursor.fetchall()
    conn.close()
    
    results = []
    for row in rows:
        config_str = row["config"]
        if isinstance(config_str, str):
            config = json.loads(config_str)
        else:
            config = config_str
        
        symbols = config.get("symbols") or []
        if symbol not in symbols:
            continue
        
        result = evaluate_strategy(
            strategy_id=row["id"],
            symbol=symbol,
            timeframe=timeframe,
            trigger_mode=trigger_mode,
            adapter_type=adapter_type,
            strategy_config=config,
        )
        results.append(result)
    
    return results


# ========== 辅助函数 ==========

def get_available_strategies() -> List[Dict[str, Any]]:
    """获取可用策略列表"""
    from app.data.database import get_db
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, mode, status FROM strategies ORDER BY updated_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "mode": row["mode"],
            "status": row["status"],
        }
        for row in rows
    ]


def summarize_signals(results: List[SignalResult]) -> Dict[str, Any]:
    """
    汇总多个策略的信号结果
    """
    buy_count = sum(1 for r in results if r.signal == "BUY")
    sell_count = sum(1 for r in results if r.signal == "SELL")
    live_count = sum(1 for r in results if r.is_live_triggered)
    
    return {
        "total": len(results),
        "buy": buy_count,
        "sell": sell_count,
        "none": len(results) - buy_count - sell_count,
        "live_triggered": live_count,
        "results": [r.to_dict() for r in results],
    }
