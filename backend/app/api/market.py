"""行情API"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import datetime, timedelta
from app.data.mock import search_stocks, get_stock_info
from app.data.adapter import get_adapter
from app.data.history_backfill import ensure_local_kline_range

router = APIRouter()


def _normalize_cn_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith('.SH') or symbol.endswith('.SZ'):
        return symbol
    if symbol.isdigit() and len(symbol) == 6:
        return f"{symbol}.SH" if symbol.startswith(('5', '6', '9')) else f"{symbol}.SZ"
    return symbol


def _normalize_hk_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith('.HK'):
        return f"{symbol[:-3].zfill(5)}.HK"
    if symbol.isdigit() and len(symbol) <= 5:
        return f"{symbol.zfill(5)}.HK"
    return symbol


def _is_cn_symbol(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    return symbol.endswith('.SH') or symbol.endswith('.SZ') or (symbol.isdigit() and len(symbol) == 6)


def _is_hk_symbol(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    return symbol.endswith('.HK') or (symbol.isdigit() and len(symbol) <= 5)


def _is_us_symbol(symbol: str) -> bool:
    symbol = symbol.strip().upper()
    return symbol.startswith('US.') or ('.' not in symbol and not symbol.isdigit())


def _normalize_symbol(symbol: str) -> str:
    if _is_cn_symbol(symbol):
        return _normalize_cn_symbol(symbol)
    if _is_hk_symbol(symbol):
        return _normalize_hk_symbol(symbol)
    return symbol.strip().upper()


def _resolve_market_adapter(adapter: str, symbol: Optional[str] = None) -> str:
    if symbol and adapter == 'finnhub' and (_is_cn_symbol(symbol) or _is_hk_symbol(symbol)):
        return 'futu'
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


@router.get("/status")
async def market_status(
    adapter: str = Query("mock", description="数据源类型：mock/futu/finnhub"),
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
    adapter: str = Query("mock", description="数据源类型：mock/futu/finnhub"),
):
    """搜索股票"""
    if _is_cn_symbol(q) or _is_hk_symbol(q):
        normalized = _normalize_symbol(q)
        info = get_stock_info(normalized)
        return [{
            "symbol": normalized,
            "name": info["name"],
            "price": info["base_price"]
        }]

    if adapter == 'finnhub':
        market_adapter = None
        try:
            market_adapter = get_adapter(adapter_type='finnhub')
            if hasattr(market_adapter, 'connect') and market_adapter.connect() and hasattr(market_adapter, 'search'):
                results = market_adapter.search(q)
                if results:
                    return results
                detail = getattr(market_adapter, 'last_error', None)
                if detail:
                    raise HTTPException(status_code=502, detail=detail)
            else:
                detail = getattr(market_adapter, 'last_error', None) or '连接 finnhub 行情源失败'
                raise HTTPException(status_code=502, detail=detail)
        finally:
            if market_adapter:
                _close_adapter(market_adapter)

    results = search_stocks(q)
    if results:
        return results

    # 富途模式下允许直接输入代码继续走后续行情/交易流程
    if adapter == 'futu' and q.strip():
        info = get_stock_info(q)
        return [{
            "symbol": q.upper(),
            "name": info["name"],
            "price": info["base_price"]
        }]

    return []


@router.get("/quote")
async def market_quote(
    symbol: str = Query(..., description="股票代码"),
    adapter: str = Query("mock", description="数据源类型：mock/futu/finnhub"),
    host: str = Query("127.0.0.1", description="OpenD地址"),
    port: int = Query(11111, description="OpenD端口"),
):
    """获取实时报价"""
    market_adapter = None
    try:
        normalized_symbol = _normalize_symbol(symbol)
        resolved_adapter = _resolve_market_adapter(adapter, normalized_symbol)
        info = get_stock_info(normalized_symbol)
        market_adapter = _create_adapter(resolved_adapter, host, port)
        quote = market_adapter.get_quote(normalized_symbol) if hasattr(market_adapter, 'get_quote') else None

        if quote is not None:
            return {
                "symbol": quote.symbol,
                "name": getattr(quote, 'name', info["name"]),
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

        if resolved_adapter in ('futu', 'finnhub'):
            default_message = '富途实时报价获取失败' if resolved_adapter == 'futu' else 'Finnhub 实时报价获取失败'
            detail = getattr(market_adapter, 'last_error', None) or default_message
            raise HTTPException(status_code=502, detail=detail)

        return {
            "symbol": normalized_symbol,
            "name": info["name"],
            "price": info["base_price"],
            "change": 0,
            "change_pct": 0,
            "volume": 0,
            "amount": 0,
            "bid": info["base_price"],
            "ask": info["base_price"],
            "high": info["base_price"],
            "low": info["base_price"],
            "open": info["base_price"],
            "pre_close": info["base_price"],
            "adapter": resolved_adapter,
        }
    finally:
        if market_adapter:
            _close_adapter(market_adapter)


@router.get("/klines")
async def market_klines(
    symbol: str = Query(..., description="股票代码"),
    timeframe: str = Query("1d", description="时间周期"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(365, description="数据点数量"),
    adapter: str = Query("mock", description="数据源类型：mock/futu/finnhub"),
    host: str = Query("127.0.0.1", description="OpenD地址"),
    port: int = Query(11111, description="OpenD端口"),
    force_refresh: bool = Query(False, description="是否强制重新补数"),
):
    """获取K线数据（local-first）"""
    if not end_date:
        end_date = datetime.now().isoformat()
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365)).isoformat()

    normalized_symbol = _normalize_symbol(symbol)
    resolved_adapter = _resolve_market_adapter(adapter, normalized_symbol)

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
