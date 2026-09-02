"""缠论走势分析 API."""
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query

from app.core.chan_engine import analyze
from app.data.history_backfill import ensure_local_kline_range
from app.data.history_repository import get_kline_bars
from app.data.kline_errors import KlineRateLimited, is_rate_limited_error
from app.data.source_router import normalize_symbol, resolve_kline_source

router = APIRouter()

_LIMIT_DAYS = {
    "1m": 14, "5m": 30, "15m": 60, "30m": 120,
    "60m": 240, "1h": 240, "1d": 800, "1w": 2000, "1M": 4000,
}


def _http_for_kline_error(exc: BaseException) -> HTTPException:
    message = str(exc)
    if isinstance(exc, KlineRateLimited) or is_rate_limited_error(message):
        retry_after = str(getattr(exc, "retry_after", 60) or 60)
        return HTTPException(
            status_code=429,
            detail=f"行情限流，请稍后重试: {message}",
            headers={"Retry-After": retry_after},
        )
    return HTTPException(status_code=502, detail=f"K线加载失败: {message}")


@router.get("/analyze")
async def chan_analyze(
    symbol: str = Query(...),
    timeframe: str = Query("1d"),
    limit: int = Query(400, ge=50, le=2000),
    adapter: str = Query("finnhub"),
    host: str = Query("127.0.0.1"),
    port: int = Query(11111),
):
    if not end_span(timeframe):
        raise HTTPException(400, "不支持的级别")
    end_date = datetime.now().isoformat()
    days = _LIMIT_DAYS.get(timeframe, 400)
    start_date = (datetime.now() - timedelta(days=days)).isoformat()
    normalized = normalize_symbol(symbol)
    source = resolve_kline_source(normalized, adapter)
    try:
        result = ensure_local_kline_range(
            symbol=normalized,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            host=host,
            port=port,
            preferred_adapter=source,
            force=False,
        )
        rows = result.get("bars") or []
    except HTTPException:
        raise
    except KlineRateLimited as e:
        rows = get_kline_bars(normalized, timeframe, start_date, end_date)
        if not rows:
            raise _http_for_kline_error(e) from e
        result = {"bars": rows, "source": source}
    except Exception as e:
        if is_rate_limited_error(e):
            rows = get_kline_bars(normalized, timeframe, start_date, end_date)
            if not rows:
                raise _http_for_kline_error(e) from e
            result = {"bars": rows, "source": source}
        else:
            raise _http_for_kline_error(e) from e
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    if not rows:
        raise HTTPException(status_code=502, detail="本地无 K 线,先在数据页补数")
    payload = [
        {
            "timestamp": row.get("ts") or row.get("timestamp"),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
        }
        for row in rows
    ]
    out = analyze(payload, timeframe)
    out["symbol"] = normalized
    out["source"] = source
    return out


def end_span(timeframe: str) -> bool:
    return timeframe in _LIMIT_DAYS
