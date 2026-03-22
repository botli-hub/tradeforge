"""实时数据融合层 - Market State Engine v1.1

核心能力：
- local-first 历史K线
- 实时 quote → 实时更新 forming bar
- 支持两种评估模式：on_quote（实时触发）/ on_bar_close（等收盘）

两种模式区别：
- on_bar_close: 用已收盘的完整 bars，适合回测和传统定时信号
- on_quote: 用 historical_bars + forming_bar（实时更新OHLC），适合盘中实时信号
"""
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any, Literal
from dataclasses import dataclass, field
from enum import Enum

from app.data.history_repository import get_kline_bars
from app.data.adapter import get_adapter, Quote, Bar
from app.data.source_router import normalize_symbol, resolve_kline_source, resolve_quote_source


# 支持的timeframe
SUPPORTED_TIMEFRAMES = ["1d", "1h", "30m", "5m", "1m"]

# timeframe转分钟数
TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "30m": 30,
    "1h": 60,
    "1d": 1440,
}


class TriggerMode(Enum):
    """信号触发模式"""
    ON_QUOTE = "on_quote"      # 实时触发：每次quote都计算
    ON_BAR_CLOSE = "on_bar_close"  # 收盘触发：等bar收盘才计算


@dataclass
class FormingBar:
    """
    当前正在形成的K线（实时更新）
    
    实时更新规则：
    - open: 周期开始的第一个价格（不变）
    - high: 周期内最高价（实时刷新）
    - low: 周期内最低价（实时刷新）
    - close: 最新成交价（实时刷新）
    - volume: 累计成交量（需要tick级别或估算）
    """
    symbol: str
    timeframe: str
    period_start: str       # 周期开始时间（ISO格式）
    open: float
    high: float
    low: float
    close: float
    volume: float = 0
    tick_count: int = 0    # 累计tick数（用于估算volume）
    
    def update_with_quote(self, price: float, volume_delta: float = 0):
        """用最新报价更新forming bar"""
        self.close = price
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        if volume_delta > 0:
            self.volume += volume_delta
        self.tick_count += 1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "period_start": self.period_start,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "tick_count": self.tick_count,
            "is_forming": True,
        }


@dataclass
class MarketState:
    """
    统一市场状态
    
    组成：
    - history_bars: 已收盘的历史K线
    - forming_bar: 当前正在形成的K线（可选，实时模式下存在）
    - latest_quote: 最新实时报价
    - metadata: 元数据
    """
    symbol: str
    timeframe: str
    history_bars: List[Dict[str, Any]]       # 已收盘的K线
    forming_bar: Optional[FormingBar]         # 当前形成的K线
    latest_quote: Optional[Quote]             # 最新报价
    trigger_mode: TriggerMode = TriggerMode.ON_BAR_CLOSE
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def get_combined_bars(self) -> List[Dict[str, Any]]:
        """
        获取组合bars：历史 + forming bar
        用于策略计算
        """
        bars = list(self.history_bars)
        if self.forming_bar:
            bars.append(self.forming_bar.to_dict())
        return bars
    
    def get_latest_bar_for_signal(self) -> Optional[Dict[str, Any]]:
        """
        获取信号评估用的最新bar
        - ON_QUOTE模式: 返回forming bar（实时）
        - ON_BAR_CLOSE模式: 返回history最后一根
        """
        if self.trigger_mode == TriggerMode.ON_QUOTE and self.forming_bar:
            return self.forming_bar.to_dict()
        if self.history_bars:
            return self.history_bars[-1]
        return None
    
    def is_live_bar_triggered(self) -> bool:
        """是否基于实时bar触发"""
        return self.trigger_mode == TriggerMode.ON_QUOTE and self.forming_bar is not None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trigger_mode": self.trigger_mode.value,
            "history_bar_count": len(self.history_bars),
            "has_forming_bar": self.forming_bar is not None,
            "history_bars": self.history_bars,
            "forming_bar": self.forming_bar.to_dict() if self.forming_bar else None,
            "latest_quote": {
                "symbol": q.symbol,
                "price": q.price,
                "change": q.change,
                "bid": q.bid,
                "ask": q.ask,
                "volume": q.volume,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            } if self.latest_quote else None,
            "latest_bar": self.get_latest_bar_for_signal(),
            "is_live_triggered": self.is_live_bar_triggered(),
            "metadata": self.metadata,
        }


# ========== 全局行情状态管理器 ==========

class MarketStateManager:
    """
    市场状态管理器
    
    职责：
    - 管理各标的的市场状态
    - 处理实时quote，更新forming bar
    - 维护订阅关系
    """
    
    def __init__(self):
        # symbol -> MarketState
        self._states: Dict[str, MarketState] = {}
        # symbol -> (symbol, timeframe, trigger_mode) -> FormingBar
        self._forming_bars: Dict[str, FormingBar] = {}
    
    def _get_state_key(self, symbol: str, timeframe: str, trigger_mode: TriggerMode) -> str:
        return f"{symbol}:{timeframe}:{trigger_mode.value}"
    
    def get_or_create_state(
        self,
        symbol: str,
        timeframe: str,
        trigger_mode: TriggerMode = TriggerMode.ON_QUOTE,
        history_days: int = 420,
        adapter_type: str = "auto",
        adapter_host: str = "127.0.0.1",
        adapter_port: int = 11111,
    ) -> MarketState:
        """获取或创建市场状态"""
        key = self._get_state_key(symbol, timeframe, trigger_mode)
        
        if key not in self._states:
            # 初始化
            state = _load_market_state(
                symbol=symbol,
                timeframe=timeframe,
                trigger_mode=trigger_mode,
                history_days=history_days,
                adapter_type=adapter_type,
                adapter_host=adapter_host,
                adapter_port=adapter_port,
            )
            self._states[key] = state
        else:
            state = self._states[key]
        
        return state
    
    def update_with_quote(self, quote: Quote):
        """用实时报价更新市场状态"""
        # 更新所有使用该symbol的state
        for key, state in self._states.items():
            symbol_from_key = key.split(":")[0]
            if symbol_from_key != quote.symbol:
                continue
            
            if state.trigger_mode == TriggerMode.ON_QUOTE:
                # 更新forming bar
                if state.forming_bar:
                    # volume_delta估算：假设每秒1笔，每笔100股
                    volume_delta = 100
                    state.forming_bar.update_with_quote(quote.price, volume_delta)
                else:
                    # 创建新的forming bar
                    state.forming_bar = _create_forming_bar(
                        symbol=quote.symbol,
                        timeframe=state.timeframe,
                        initial_price=quote.price,
                    )
                
                state.latest_quote = quote
    
    def get_state(self, symbol: str, timeframe: str, trigger_mode: TriggerMode) -> Optional[MarketState]:
        """获取已存在的市场状态"""
        key = self._get_state_key(symbol, timeframe, trigger_mode)
        return self._states.get(key)
    
    def update_forming_bar_with_quote(self, symbol: str, price: float, volume: float = 0):
        """
        用实时报价直接更新 forming bar（便捷方法）
        
        用于 WebSocket 推送场景，直接传入 price/volume 更新 forming bar
        
        Args:
            symbol: 股票代码
            price: 最新价格
            volume: 成交量增量
        """
        from app.data.adapter import Quote
        
        # 创建一个简化的 Quote 对象
        quote = Quote(
            symbol=symbol,
            name=symbol,
            price=price,
            change=0,
            change_pct=0,
            volume=int(volume) if volume else 0,
            amount=0,
            bid=price,
            ask=price,
            high=price,
            low=price,
            open=price,
            pre_close=price,
        )
        self.update_with_quote(quote)
    
    def clear(self, symbol: Optional[str] = None):
        """清理状态"""
        if symbol is None:
            self._states.clear()
        else:
            keys_to_remove = [k for k in self._states if k.startswith(f"{symbol}:")]
            for k in keys_to_remove:
                del self._states[k]


# 全局实例
_market_state_manager: Optional[MarketStateManager] = None


def get_market_state_manager() -> MarketStateManager:
    global _market_state_manager
    if _market_state_manager is None:
        _market_state_manager = MarketStateManager()
    return _market_state_manager


# ========== 内部函数 ==========

def _get_period_start(timestamp: datetime, timeframe: str) -> datetime:
    """计算K线周期开始时间"""
    minutes = TIMEFRAME_MINUTES.get(timeframe, 1440)
    
    if timeframe == "1d":
        return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    
    period_min = (timestamp.minute // minutes) * minutes
    return timestamp.replace(
        minute=period_min,
        second=0,
        microsecond=0
    )


def _create_forming_bar(
    symbol: str,
    timeframe: str,
    initial_price: float,
    period_start: Optional[datetime] = None,
) -> FormingBar:
    """创建新的forming bar"""
    now = datetime.now(timezone.utc)
    period_start = period_start or _get_period_start(now, timeframe)
    
    return FormingBar(
        symbol=symbol,
        timeframe=timeframe,
        period_start=period_start.isoformat(),
        open=initial_price,
        high=initial_price,
        low=initial_price,
        close=initial_price,
        volume=0,
        tick_count=1,
    )


def _load_market_state(
    symbol: str,
    timeframe: str,
    trigger_mode: TriggerMode,
    history_days: int,
    adapter_type: str,
    adapter_host: str,
    adapter_port: int,
) -> MarketState:
    """加载市场状态（不获取实时quote）"""
    symbol = normalize_symbol(symbol)
    kline_source = resolve_kline_source(symbol, adapter_type)
    quote_source = resolve_quote_source(symbol, adapter_type)

    # 计算时间范围
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=history_days)
    
    # 1. 尝试从本地数据库获取历史K线
    history_bars = []
    try:
        local_bars = get_kline_bars(
            symbol=symbol,
            timeframe=timeframe,
            start_ts=start_dt.isoformat(),
            end_ts=end_dt.isoformat(),
        )
        if len(local_bars) >= 5:
            history_bars = [
                {
                    "ts": bar["ts"],
                    "timestamp": bar["ts"],
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar.get("volume", 0),
                    "source": bar.get("source", "local"),
                    "is_forming": False,
                }
                for bar in local_bars
            ]
    except Exception as e:
        print(f"获取本地历史K线失败: {e}")
    
    # 2. 如果本地数据不足，从adapter获取
    if len(history_bars) < 5:
        adapter = None
        try:
            adapter = get_adapter(adapter_type=kline_source, host=adapter_host, port=adapter_port)
            if adapter.connect():
                bars = adapter.get_klines(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_date=start_dt.isoformat(),
                    end_date=end_dt.isoformat(),
                )
                history_bars = [
                    {
                        "ts": bar.timestamp,
                        "timestamp": bar.timestamp,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "source": kline_source,
                        "is_forming": False,
                    }
                    for bar in bars
                ]
        finally:
            if adapter and hasattr(adapter, 'disconnect'):
                try:
                    adapter.disconnect()
                except:
                    pass
    
    # 3. 如果是实时模式，创建forming bar
    forming_bar = None
    if trigger_mode == TriggerMode.ON_QUOTE:
        # 从实时 quote 路由获取最新报价来初始化 forming bar
        adapter = None
        try:
            adapter = get_adapter(adapter_type=quote_source, host=adapter_host, port=adapter_port)
            if adapter.connect():
                quote = adapter.get_quote(symbol)
                if quote:
                    latest_price = quote.price
                    # 检查当前是否在交易时段
                    now = datetime.now(timezone.utc)
                    period_start = _get_period_start(now, timeframe)
                    
                    # 如果历史bar的最后一个时间 >= 当前period开始，说明当前bar已收盘
                    if history_bars:
                        last_ts = history_bars[-1].get("ts") or history_bars[-1].get("timestamp")
                        if last_ts:
                            # 统一为naive datetime比较
                            last_dt_str = last_ts.replace('Z', '+00:00').replace('+00:00', '')
                            last_dt = datetime.fromisoformat(last_dt_str)
                            if last_dt >= period_start.replace(tzinfo=None):
                                # 当前period已有完整bar，不需要forming bar
                                pass
                            else:
                                # 创建forming bar
                                forming_bar = _create_forming_bar(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    initial_price=latest_price,
                                    period_start=period_start,
                                )
                    else:
                        # 没有历史数据，创建forming bar
                        forming_bar = _create_forming_bar(
                            symbol=symbol,
                            timeframe=timeframe,
                            initial_price=latest_price,
                            period_start=period_start,
                        )
        finally:
            if adapter and hasattr(adapter, 'disconnect'):
                try:
                    adapter.disconnect()
                except:
                    pass
    
    # 4. 元数据
    metadata = {
        "timeframe": timeframe,
        "trigger_mode": trigger_mode.value,
        "history_bar_count": len(history_bars),
        "has_forming_bar": forming_bar is not None,
        "route_mode": "auto",
        "quote_source": quote_source,
        "kline_source": history_bars[0].get("source", kline_source) if history_bars else kline_source,
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return MarketState(
        symbol=symbol,
        timeframe=timeframe,
        history_bars=history_bars,
        forming_bar=forming_bar,
        latest_quote=None,
        trigger_mode=trigger_mode,
        metadata=metadata,
    )


# ========== 公开 API ==========

def get_market_state(
    symbol: str,
    timeframe: str = "1d",
    trigger_mode: Literal["on_quote", "on_bar_close"] = "on_quote",
    history_days: int = 420,
    adapter_type: str = "auto",
    adapter_host: str = "127.0.0.1",
    adapter_port: int = 11111,
) -> MarketState:
    """
    获取市场状态
    
    Args:
        symbol: 股票代码
        timeframe: 时间周期
        trigger_mode: 触发模式 on_quote / on_bar_close
        history_days: 历史天数
        adapter_type: 行情适配器
        adapter_host: 行情地址
        adapter_port: 行情端口
    
    Returns:
        MarketState
    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"不支持的timeframe: {timeframe}，支持: {SUPPORTED_TIMEFRAMES}")
    
    mode = TriggerMode.ON_QUOTE if trigger_mode == "on_quote" else TriggerMode.ON_BAR_CLOSE
    
    manager = get_market_state_manager()
    return manager.get_or_create_state(
        symbol=symbol,
        timeframe=timeframe,
        trigger_mode=mode,
        history_days=history_days,
        adapter_type=adapter_type,
        adapter_host=adapter_host,
        adapter_port=adapter_port,
    )


def update_forming_bar_with_quote(symbol: str, price: float, volume: float = 0):
    """直接更新forming bar（用于外部推送quote）"""
    manager = get_market_state_manager()
    
    for key, state in manager._states.items():
        if state.symbol == symbol and state.trigger_mode == TriggerMode.ON_QUOTE:
            if state.forming_bar:
                state.forming_bar.update_with_quote(price, volume)


def market_state_to_dataframe(market_state: MarketState) -> pd.DataFrame:
    """
    将MarketState转换为pandas DataFrame
    
    用于策略信号引擎
    """
    # 收盘模式：用历史bars
    if market_state.trigger_mode == TriggerMode.ON_BAR_CLOSE:
        bars = market_state.history_bars
    else:
        # 实时模式：用历史 + forming bar
        bars = market_state.get_combined_bars()
    
    if not bars:
        return pd.DataFrame()
    
    df = pd.DataFrame(bars)
    
    # 标准化列名
    if "timestamp" not in df.columns and "ts" in df.columns:
        df["timestamp"] = df["ts"]
    
    df["timestamp"] = pd.to_datetime(df["timestamp"].str.replace('Z', '+00:00'), errors='coerce')
    df = df.set_index("timestamp")
    df = df.sort_index()
    
    # 确保数值类型
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    
    return df
