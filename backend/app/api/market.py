"""行情API"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime, timedelta
import threading
import time
from app.data.adapter import get_adapter
from app.data.history_backfill import ensure_local_kline_range
from app.data.source_router import (
    normalize_symbol,
    is_cn_symbol,
    is_hk_symbol,
    resolve_quote_source,
    resolve_kline_source,
)

router = APIRouter()

_ADAPTER_POOL = {}
_ADAPTER_LOCK = threading.Lock()
_QUOTE_CACHE = {}
_QUOTE_CACHE_LOCK = threading.Lock()
_QUOTE_CACHE_TTL_SEC = 5


def _adapter_key(adapter_type: str, host: str, port: int):
    return (adapter_type, host, port)


def _get_pooled_adapter(adapter_type: str, host: str, port: int):
    key = _adapter_key(adapter_type, host, port)
    with _ADAPTER_LOCK:
        adapter = _ADAPTER_POOL.get(key)
        if adapter is not None and getattr(adapter, 'is_connected', lambda: True)():
            return adapter

        adapter = get_adapter(adapter_type=adapter_type, host=host, port=port)
        if hasattr(adapter, 'connect'):
            success = adapter.connect()
            if not success:
                detail = getattr(adapter, 'last_error', None) or f"连接 {adapter_type} 行情源失败"
                raise HTTPException(status_code=502, detail=detail)
        _ADAPTER_POOL[key] = adapter
        return adapter


def _create_adapter(adapter_type: str, host: str, port: int):
    adapter = get_adapter(adapter_type=adapter_type, host=host, port=port)
    if hasattr(adapter, 'connect'):
        success = adapter.connect()
        if not success:
            detail = getattr(adapter, 'last_error', None) or f"连接 {adapter_type} 行情源失败"
            raise HTTPException(status_code=502, detail=detail)
    return adapter


def _close_adapter(adapter):
    if hasattr(adapter, 'disconnect'):
        try:
            adapter.disconnect()
        except Exception:
            pass


def _quote_cache_key(symbol: str, adapter: str, host: str, port: int):
    return (symbol, adapter, host, port)


def _get_cached_quote(symbol: str, adapter: str, host: str, port: int):
    key = _quote_cache_key(symbol, adapter, host, port)
    with _QUOTE_CACHE_LOCK:
        cached = _QUOTE_CACHE.get(key)
        if not cached:
            return None
        if time.time() - cached['at'] > _QUOTE_CACHE_TTL_SEC:
            _QUOTE_CACHE.pop(key, None)
            return None
        return cached['data']


def _set_cached_quote(symbol: str, adapter: str, host: str, port: int, data: dict):
    key = _quote_cache_key(symbol, adapter, host, port)
    with _QUOTE_CACHE_LOCK:
        _QUOTE_CACHE[key] = {'at': time.time(), 'data': data}


@router.get("/status")
async def market_status(
    adapter: str = Query("finnhub", description="数据源类型：futu/finnhub"),
    host: str = Query("127.0.0.1", description="OpenD地址"),
    port: int = Query(11111, description="OpenD端口"),
):
    """检查行情源状态"""
    market_adapter = None
    try:
        market_adapter = _create_adapter(adapter, host, port)
        return {
            "connected": market_adapter.is_connected(),
            "adapter": adapter,
            "host": host,
            "port": port,
        }
    finally:
        if market_adapter:
            _close_adapter(market_adapter)


@router.get("/search")
async def market_search(
    q: str = Query(..., description="搜索关键词"),
    adapter: str = Query("finnhub", description="数据源类型：futu/finnhub"),
):
    """搜索股票"""
    if is_cn_symbol(q) or is_hk_symbol(q):
        normalized = normalize_symbol(q)
        return [{"symbol": normalized, "name": normalized, "price": None}]

    market_adapter = None
    try:
        if adapter == 'futu':
            market_adapter = get_adapter(adapter_type='futu')
        else:
            market_adapter = get_adapter(adapter_type='finnhub')

        if hasattr(market_adapter, 'connect') and not market_adapter.connect():
            detail = getattr(market_adapter, 'last_error', None) or f'连接 {adapter} 行情源失败'
            raise HTTPException(status_code=502, detail=detail)

        if hasattr(market_adapter, 'search'):
            results = market_adapter.search(q)
            if results:
                return results
            detail = getattr(market_adapter, 'last_error', None)
            if detail:
                raise HTTPException(status_code=502, detail=detail)

        # futu 模式下允许直接输入代码
        if adapter == 'futu' and q.strip():
            return [{"symbol": q.upper(), "name": q.upper(), "price": None}]

        raise HTTPException(status_code=502, detail=f'{adapter} 搜索无结果，请检查网络或行情源配置')
    finally:
        if market_adapter:
            _close_adapter(market_adapter)


def _quote_to_payload(quote, normalized_symbol: str, resolved_adapter: str):
    return {
        "symbol": quote.symbol,
        "name": getattr(quote, 'name', normalized_symbol),
        "price": quote.price,
        "change": quote.change,
        "change_pct": quote.change_pct,
        "volume": quote.volume,
        "amount": quote.amount,
        "bid": quote.bid,
        "ask": quote.ask,
        "high": quote.high,
        "low": quote.low,
        "open": quote.open,
        "pre_close": quote.pre_close,
        "adapter": resolved_adapter,
    }


@router.get("/quote")
async def market_quote(
    symbol: str = Query(..., description="股票代码"),
    adapter: str = Query("finnhub", description="数据源类型：futu/finnhub"),
    host: str = Query("127.0.0.1", description="OpenD地址"),
    port: int = Query(11111, description="OpenD端口"),
):
    """获取实时报价"""
    normalized_symbol = normalize_symbol(symbol)
    resolved_adapter = resolve_quote_source(normalized_symbol, adapter)

    cached = _get_cached_quote(normalized_symbol, resolved_adapter, host, port)
    if cached is not None:
        return cached

    market_adapter = _get_pooled_adapter(resolved_adapter, host, port)
    quote = market_adapter.get_quote(normalized_symbol) if hasattr(market_adapter, 'get_quote') else None

    if quote is not None:
        payload = _quote_to_payload(quote, normalized_symbol, resolved_adapter)
        _set_cached_quote(normalized_symbol, resolved_adapter, host, port, payload)
        return payload

    default_message = '富途实时报价获取失败' if resolved_adapter == 'futu' else 'Finnhub 实时报价获取失败，请检查 API Key 或网络连接'
    detail = getattr(market_adapter, 'last_error', None) or default_message
    raise HTTPException(status_code=502, detail=detail)


@router.get("/quotes")
async def market_quotes(
    symbols: str = Query(..., description="股票代码，逗号分隔"),
    adapter: str = Query("finnhub", description="数据源类型：futu/finnhub"),
    host: str = Query("127.0.0.1", description="OpenD地址"),
    port: int = Query(11111, description="OpenD端口"),
):
    """批量获取实时报价"""
    requested = [normalize_symbol(item) for item in symbols.split(',') if item.strip()]
    if not requested:
        return {"items": []}

    groups = {}
    items = []
    missing = []

    for symbol in requested:
        resolved_adapter = resolve_quote_source(symbol, adapter)
        cached = _get_cached_quote(symbol, resolved_adapter, host, port)
        if cached is not None:
            items.append(cached)
            continue
        groups.setdefault(resolved_adapter, []).append(symbol)
        missing.append((symbol, resolved_adapter))

    for resolved_adapter, group_symbols in groups.items():
        market_adapter = _get_pooled_adapter(resolved_adapter, host, port)
        if hasattr(market_adapter, 'get_quotes'):
            result = market_adapter.get_quotes(group_symbols)
        else:
            result = {symbol: market_adapter.get_quote(symbol) for symbol in group_symbols}

        for symbol in group_symbols:
            quote = result.get(symbol)
            if quote is None:
                continue
            payload = _quote_to_payload(quote, symbol, resolved_adapter)
            _set_cached_quote(symbol, resolved_adapter, host, port, payload)
            items.append(payload)

    return {"items": items}


@router.get("/klines")
async def market_klines(
    symbol: str = Query(..., description="股票代码"),
    timeframe: str = Query("1d", description="时间周期"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(365, description="数据点数量"),
    adapter: str = Query("finnhub", description="数据源类型：futu/finnhub"),
    host: str = Query("127.0.0.1", description="OpenD地址"),
    port: int = Query(11111, description="OpenD端口"),
    force_refresh: bool = Query(False, description="是否强制重新补数"),
):
    """获取K线数据（local-first）"""
    if not end_date:
        end_date = datetime.now().isoformat()
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).isoformat()

    normalized_symbol = normalize_symbol(symbol)
    resolved_adapter = resolve_kline_source(normalized_symbol, adapter)

    try:
        result = ensure_local_kline_range(
            symbol=normalized_symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            host=host,
            port=port,
            preferred_adapter=resolved_adapter,
            force=force_refresh,
        )
        rows = result.get('bars', [])
        if limit and len(rows) > limit:
            rows = rows[-limit:]

        if not rows:
            raise HTTPException(status_code=502, detail='本地历史K线为空，且补数失败')

        return [
            {
                'symbol': row['symbol'],
                'timeframe': row['timeframe'],
                'timestamp': row['ts'],
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
                'adapter': row['source'],
                'storage': 'local',
            }
            for row in rows
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
