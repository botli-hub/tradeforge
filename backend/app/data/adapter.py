"""行情适配器 - Futu / Finnhub / Mock

优化说明（v1.2）：
- 引入 logging 替换所有 print 调用
"""
import json
import logging
import urllib.parse
import urllib.request
from typing import List, Protocol, Optional
from dataclasses import dataclass
from datetime import datetime

from app.core.config import get_effective_config
from app.data.kline_errors import KlineRateLimited

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """K线数据"""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Quote:
    """实时报价"""
    symbol: str
    name: str
    price: float
    change: float
    change_pct: float
    volume: int
    amount: float
    bid: float
    ask: float
    high: float
    low: float
    open: float
    pre_close: float


class MarketDataAdapter(Protocol):
    """行情数据适配器接口"""

    def get_klines(self, symbol: str, timeframe: str,
                   start_date: str, end_date: str) -> List[Bar]:
        """获取K线数据"""
        ...

    def subscribe(self, symbols: List[str]) -> bool:
        """订阅实时行情"""
        ...

    def on_quote(self, callback) -> None:
        """设置行情回调"""
        ...

    def is_connected(self) -> bool:
        """检查连接状态"""
        ...


class FutuAdapter:
    """富途行情适配器"""

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        futu_cfg = (get_effective_config().get("futu") or {})
        self.host = host or futu_cfg.get("host") or "127.0.0.1"
        self.port = port or futu_cfg.get("port") or 11111
        self._connected = False
        self._subscribed = set()
        self._quote_callback = None
        self._quote_ctx = None
        self.last_error: Optional[str] = None

        self._ktype_map = {
            "1m": "K_1M",
            "5m": "K_5M",
            "15m": "K_15M",
            "30m": "K_30M",
            "60m": "K_60M",
            "1h": "K_60M",
            "4h": "K_240M",
            "1d": "K_DAY",
            "1w": "K_WEEK",
            "1M": "K_MON",
        }
        self._timeframe_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "60m": 60,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
            "1w": 10080,
            "1M": 43200,
        }

    def _normalize_symbol(self, symbol: str) -> str:
        symbol = symbol.strip().upper()
        if symbol.startswith(('US.', 'HK.', 'SH.', 'SZ.')):
            return symbol
        if symbol.endswith('.HK'):
            return f"HK.{symbol[:-3].zfill(5)}"
        if symbol.endswith('.SH'):
            return f"SH.{symbol[:-3]}"
        if symbol.endswith('.SZ'):
            return f"SZ.{symbol[:-3]}"
        if symbol.isdigit():
            if len(symbol) == 6:
                return f"SH.{symbol}" if symbol.startswith(('5', '6', '9')) else f"SZ.{symbol}"
            return f"HK.{symbol.zfill(5)}"
        return f"US.{symbol}"

    def _denormalize_symbol(self, symbol: str) -> str:
        symbol = symbol.strip().upper()
        if symbol.startswith('HK.'):
            return f"{symbol.split('.', 1)[1]}.HK"
        if symbol.startswith('SH.'):
            return f"{symbol.split('.', 1)[1]}.SH"
        if symbol.startswith('SZ.'):
            return f"{symbol.split('.', 1)[1]}.SZ"
        if symbol.startswith('US.'):
            return symbol.split('.', 1)[1]
        return symbol

    def connect(self) -> bool:
        """连接富途OpenD(先 TCP 探测,避免 OpenD 未启动时 OpenQuoteContext 阻塞)"""
        self.last_error = None
        try:
            from app.core.opend import open_quote_context
            self._quote_ctx = open_quote_context(host=self.host, port=self.port)
            self._connected = True
            return True
        except ImportError:
            self.last_error = "futu-api 未安装"
            logger.error(self.last_error)
            return False
        except Exception as e:
            self.last_error = f"连接失败: {e}"
            logger.error(self.last_error)
            return False

    def disconnect(self):
        """断开连接"""
        if self._quote_ctx:
            try:
                self._quote_ctx.close()
            except Exception:
                pass
        self._quote_ctx = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._quote_ctx is not None

    def _estimate_bar_count(self, timeframe: str, start_date: str, end_date: str) -> int:
        try:
            start = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            delta_minutes = max(int((end - start).total_seconds() / 60), 1)
        except Exception:
            return 365
        period = self._timeframe_minutes.get(timeframe, 1440)
        return max(2, min(1000, delta_minutes // period + 5))

    def get_klines(self, symbol: str, timeframe: str,
                   start_date: str, end_date: str) -> List[Bar]:
        """历史K线：OpenD request_history_kline 分页；未连接时先 connect，不直接返回 []。"""
        if not self.is_connected():
            if not self.connect():
                return []

        try:
            from app.data.futu_history_kline import fetch_history_bars

            code = self._normalize_symbol(symbol)
            rows, err = fetch_history_bars(
                self._quote_ctx,
                code=code,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
            )
            if err:
                self.last_error = err
                logger.error(self.last_error)
            else:
                self.last_error = None
            return [
                Bar(
                    timestamp=str(row['timestamp']),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=int(row['volume']),
                )
                for row in rows
            ]
        except Exception as e:
            self.last_error = f"获取K线异常: {e}"
            logger.error(self.last_error)
            return []

    def subscribe(self, symbols: List[str]) -> bool:
        """订阅实时行情"""
        if not self.is_connected():
            return False

        try:
            from futu import SubType, RET_OK
            code_list = [self._normalize_symbol(symbol) for symbol in symbols]
            ret, err = self._quote_ctx.subscribe(code_list, [SubType.QUOTE], subscribe_push=False)
            if ret == RET_OK:
                self._subscribed.update(code_list)
                self.last_error = None
                return True
            self.last_error = f"订阅失败: {err}"
            logger.error(self.last_error)
            return False
        except Exception as e:
            self.last_error = f"订阅失败: {e}"
            logger.error(self.last_error)
            return False

    def unsubscribe(self, symbols: List[str]) -> bool:
        """取消订阅"""
        if not self.is_connected():
            return False

        try:
            from futu import SubType, RET_OK
            code_list = [self._normalize_symbol(symbol) for symbol in symbols]
            ret, err = self._quote_ctx.unsubscribe(code_list, [SubType.QUOTE])
            if ret == RET_OK:
                for code in code_list:
                    self._subscribed.discard(code)
                return True
            logger.error("取消订阅失败: %s", err)
            return False
        except Exception as e:
            logger.error("取消订阅失败", exc_info=True)
            return False

    def on_quote(self, callback):
        """设置行情回调"""
        self._quote_callback = callback

    def get_quote(self, symbol: str) -> Optional[Quote]:
        """获取单个实时报价"""
        quotes = self.get_quotes([symbol])
        return quotes.get(symbol)

    def get_quotes(self, symbols: List[str]) -> dict[str, Quote]:
        """批量获取实时报价，复用连接/订阅，避免逐个 symbol 重连。"""
        if not self.is_connected() or not symbols:
            return {}

        try:
            from futu import SubType, RET_OK

            normalized_codes = []
            original_by_code = {}
            for symbol in symbols:
                code = self._normalize_symbol(symbol)
                normalized_codes.append(code)
                original_by_code[code] = symbol

            missing_quote_subs = [code for code in normalized_codes if code not in self._subscribed]
            if missing_quote_subs:
                ret_sub, err = self._quote_ctx.subscribe(missing_quote_subs, [SubType.QUOTE], subscribe_push=False)
                if ret_sub != RET_OK:
                    self.last_error = f"订阅报价失败: {err}"
                    logger.error(self.last_error)
                    return {}
                self._subscribed.update(missing_quote_subs)

            ret, data = self._quote_ctx.get_stock_quote(normalized_codes)
            if ret != RET_OK or len(data) == 0:
                self.last_error = f"获取报价失败: {data}"
                logger.error(self.last_error)
                return {}

            results: dict[str, Quote] = {}
            for _, row in data.iterrows():
                code = str(row.get('code', '') or '')
                if not code:
                    continue
                price = float(row.get('last_price', 0) or 0)
                pre_close = float(row.get('prev_close_price', 0) or 0)
                change = price - pre_close if pre_close else 0.0
                change_pct = (change / pre_close * 100) if pre_close else 0.0
                denormalized = self._denormalize_symbol(code)
                original_symbol = original_by_code.get(code, denormalized)
                quote = Quote(
                    symbol=denormalized,
                    name=str(row.get('name', denormalized)),
                    price=price,
                    change=change,
                    change_pct=change_pct,
                    volume=int(row.get('volume', 0) or 0),
                    amount=float(row.get('turnover', 0) or 0),
                    bid=price,
                    ask=price,
                    high=float(row.get('high_price', price) or price),
                    low=float(row.get('low_price', price) or price),
                    open=float(row.get('open_price', price) or price),
                    pre_close=pre_close,
                )
                results[original_symbol] = quote
                results[denormalized] = quote

            self.last_error = None
            return results
        except Exception as e:
            self.last_error = f"获取报价异常: {e}"
            logger.error(self.last_error)
            return {}
