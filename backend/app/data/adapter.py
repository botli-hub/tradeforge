"""行情适配器 - Futu / Finnhub / Mock"""
import json
import urllib.parse
import urllib.request
from typing import List, Protocol, Optional
from dataclasses import dataclass
from datetime import datetime

from app.core.config import get_settings


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

    def __init__(self, host: str = "127.0.0.1", port: int = 11111):
        self.host = host
        self.port = port
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
            "1h": "K_60M",
            "4h": "K_240M",
            "1d": "K_DAY",
            "1w": "K_WEEK",
        }
        self._timeframe_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
            "1w": 10080,
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
        """连接富途OpenD"""
        self.last_error = None
        try:
            from futu import OpenQuoteContext
            self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            self._connected = True
            return True
        except ImportError:
            self.last_error = "futu-api 未安装"
            print(self.last_error)
            return False
        except Exception as e:
            self.last_error = f"连接失败: {e}"
            print(self.last_error)
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
        """获取K线数据"""
        if not self.is_connected():
            return []

        try:
            from futu import SubType, AuType, RET_OK, Session

            code = self._normalize_symbol(symbol)
            subtype_name = self._ktype_map.get(timeframe, 'K_DAY')
            subtype = getattr(SubType, subtype_name)

            ret_sub, err = self._quote_ctx.subscribe([code], [subtype], subscribe_push=False, session=Session.ALL)
            if ret_sub != RET_OK:
                self.last_error = f"订阅K线失败: {err}"
                print(self.last_error)
                return []

            num = self._estimate_bar_count(timeframe, start_date, end_date)
            ret, data = self._quote_ctx.get_cur_kline(code, num, subtype, AuType.QFQ)
            if ret != RET_OK:
                self.last_error = f"获取K线失败: {data}"
                print(self.last_error)
                return []

            bars = []
            for _, row in data.iterrows():
                bars.append(Bar(
                    timestamp=str(row['time_key']),
                    open=float(row['open']),
                    high=float(row['high']),
                    low=float(row['low']),
                    close=float(row['close']),
                    volume=int(row['volume'])
                ))
            self.last_error = None
            return bars
        except Exception as e:
            self.last_error = f"获取K线异常: {e}"
            print(self.last_error)
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
            print(self.last_error)
            return False
        except Exception as e:
            self.last_error = f"订阅失败: {e}"
            print(self.last_error)
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
            print(f"取消订阅失败: {err}")
            return False
        except Exception as e:
            print(f"取消订阅失败: {e}")
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
                    print(self.last_error)
                    return {}
                self._subscribed.update(missing_quote_subs)

            ret, data = self._quote_ctx.get_stock_quote(normalized_codes)
            if ret != RET_OK or len(data) == 0:
                self.last_error = f"获取报价失败: {data}"
                print(self.last_error)
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
            print(self.last_error)
            return {}


def _load_env_file_once():
    if os.environ.get("_TRADEFORGE_ENV_LOADED") == "1":
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    os.environ["_TRADEFORGE_ENV_LOADED"] = "1"


class FinnhubAdapter:
    """Finnhub 行情适配器（美股/ETF 优先）"""

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://finnhub.io/api/v1", **kwargs):
        settings = get_settings()
        self.api_key = api_key or settings["finnhub_api_key"]
        self.base_url = base_url.rstrip('/')
        self._connected = False
        self._quote_callback = None
        self.last_error: Optional[str] = None
        self._resolution_map = {
            "1m": "1",
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "4h": "60",
            "1d": "D",
            "1w": "W",
        }

    def connect(self) -> bool:
        self.last_error = None
        if not self.api_key:
            self.last_error = "未配置 FINNHUB_API_KEY"
            return False
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def on_quote(self, callback):
        self._quote_callback = callback

    def subscribe(self, symbols: List[str]) -> bool:
        return self.is_connected()

    def unsubscribe(self, symbols: List[str]) -> bool:
        return True

    def _request_json(self, path: str, params: dict) -> dict:
        query = urllib.parse.urlencode({**params, "token": self.api_key})
        url = f"{self.base_url}{path}?{query}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def search(self, keyword: str) -> List[dict]:
        if not self.is_connected():
            return []
        try:
            data = self._request_json("/search", {"q": keyword})
            results = []
            for item in (data.get("result") or [])[:12]:
                symbol = item.get("displaySymbol") or item.get("symbol")
                if not symbol:
                    continue
                results.append({
                    "symbol": str(symbol).upper(),
                    "name": item.get("description") or str(symbol).upper(),
                    "price": None,
                    "type": item.get("type"),
                })
            self.last_error = None
            return results
        except Exception as e:
            self.last_error = f"Finnhub 搜索失败: {e}"
            return []

    def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self.is_connected():
            return None
        try:
            data = self._request_json("/quote", {"symbol": symbol.upper()})
            price = float(data.get("c") or 0)
            if price <= 0:
                self.last_error = f"Finnhub 未返回有效报价: {symbol.upper()}"
                return None
            pre_close = float(data.get("pc") or 0)
            change = float(data.get("d") or (price - pre_close))
            change_pct = float(data.get("dp") or ((change / pre_close * 100) if pre_close else 0))
            self.last_error = None
            return Quote(
                symbol=symbol.upper(),
                name=symbol.upper(),
                price=price,
                change=change,
                change_pct=change_pct,
                volume=0,
                amount=0,
                bid=price,
                ask=price,
                high=float(data.get("h") or price),
                low=float(data.get("l") or price),
                open=float(data.get("o") or price),
                pre_close=pre_close,
            )
        except Exception as e:
            self.last_error = f"Finnhub 报价失败: {e}"
            return None

    def get_klines(self, symbol: str, timeframe: str, start_date: str, end_date: str) -> List[Bar]:
        if not self.is_connected():
            return []
        try:
            resolution = self._resolution_map.get(timeframe, "D")
            start_ts = int(datetime.fromisoformat(start_date.replace('Z', '+00:00')).timestamp())
            end_ts = int(datetime.fromisoformat(end_date.replace('Z', '+00:00')).timestamp())
            data = self._request_json(
                "/stock/candle",
                {
                    "symbol": symbol.upper(),
                    "resolution": resolution,
                    "from": start_ts,
                    "to": end_ts,
                }
            )
            if data.get("s") != "ok":
                self.last_error = f"Finnhub K线失败: {data.get('s') or data}"
                return []
            bars: List[Bar] = []
            opens = data.get("o", [])
            highs = data.get("h", [])
            lows = data.get("l", [])
            closes = data.get("c", [])
            volumes = data.get("v", [])
            timestamps = data.get("t", [])
            for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
                bars.append(Bar(
                    timestamp=datetime.fromtimestamp(int(ts)).isoformat(),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=int(v),
                ))
            self.last_error = None
            return bars
        except Exception as e:
            self.last_error = f"Finnhub K线异常: {e}"
            return []


class YahooAdapter:
    """Yahoo Finance K线适配器（主打美股/ETF K线）"""

    def __init__(self, base_url: str = "https://query1.finance.yahoo.com/v8/finance/chart", **kwargs):
        self.base_url = base_url.rstrip('/')
        self._connected = False
        self._quote_callback = None
        self.last_error: Optional[str] = None
        self._interval_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "1h": "60m",
            "4h": "1h",
            "1d": "1d",
            "1w": "1wk",
        }

    def connect(self) -> bool:
        self.last_error = None
        self._connected = True
        return True

    def disconnect(self):
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def subscribe(self, symbols: List[str]) -> bool:
        return self.is_connected()

    def unsubscribe(self, symbols: List[str]) -> bool:
        return True

    def on_quote(self, callback):
        self._quote_callback = callback

    def _normalize_symbol(self, symbol: str) -> str:
        symbol = symbol.strip().upper()
        if symbol.startswith('US.'):
            return symbol.split('.', 1)[1]
        if symbol.endswith('.HK'):
            return symbol.replace('.HK', '.HK')
        if symbol.endswith('.SH'):
            return symbol.replace('.SH', '.SS')
        if symbol.endswith('.SZ'):
            return symbol
        if symbol.isdigit():
            return f"{symbol.zfill(4)}.HK"
        return symbol

    def get_quote(self, symbol: str) -> Optional[Quote]:
        return None

    def get_klines(self, symbol: str, timeframe: str, start_date: str, end_date: str) -> List[Bar]:
        if not self.is_connected():
            return []
        try:
            interval = self._interval_map.get(timeframe, '1d')
            period1 = int(datetime.fromisoformat(start_date.replace('Z', '+00:00')).timestamp())
            period2 = int(datetime.fromisoformat(end_date.replace('Z', '+00:00')).timestamp())
            query = urllib.parse.urlencode({
                'period1': period1,
                'period2': period2,
                'interval': interval,
                'includePrePost': 'false',
                'events': 'div,splits',
            })
            normalized = self._normalize_symbol(symbol)
            url = f"{self.base_url}/{urllib.parse.quote(normalized)}?{query}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode('utf-8'))

            result = (((payload or {}).get('chart') or {}).get('result') or [None])[0] or {}
            timestamps = result.get('timestamp') or []
            quote = (((result.get('indicators') or {}).get('quote') or [None])[0]) or {}
            opens = quote.get('open') or []
            highs = quote.get('high') or []
            lows = quote.get('low') or []
            closes = quote.get('close') or []
            volumes = quote.get('volume') or []

            bars: List[Bar] = []
            for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
                if None in (o, h, l, c):
                    continue
                bars.append(Bar(
                    timestamp=datetime.fromtimestamp(int(ts)).isoformat(),
                    open=float(o),
                    high=float(h),
                    low=float(l),
                    close=float(c),
                    volume=int(v or 0),
                ))
            if not bars:
                error = (((payload or {}).get('chart') or {}).get('error') or {}).get('description')
                self.last_error = f"Yahoo K线返回为空{': ' + error if error else ''}"
                return []
            self.last_error = None
            return bars
        except Exception as e:
            self.last_error = f"Yahoo K线异常: {e}"
            return []


# 适配器工厂
def get_adapter(adapter_type: str, **kwargs) -> MarketDataAdapter:
    """获取适配器实例。adapter_type 必须为 futu / finnhub / yahoo。"""
    if adapter_type == "futu":
        return FutuAdapter(**kwargs)
    if adapter_type == "finnhub":
        return FinnhubAdapter(**kwargs)
    if adapter_type == "yahoo":
        return YahooAdapter(**kwargs)
    raise ValueError(
        f"未知适配器类型: {adapter_type!r}。请使用 'futu'、'finnhub' 或 'yahoo'。"
    )
