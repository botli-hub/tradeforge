"""缠论走势分析 API."""
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.chan_engine import analyze
from app.data.history_backfill import ensure_local_kline_range
from app.data.history_repository import get_kline_bars
from app.data.source_router import normalize_symbol, resolve_kline_source

router = APIRouter()

_LIMIT_DAYS = {
    "1m": 14, "5m": 30, "15m": 60, "30m": 120,
    "60m": 240, "1h": 240, "1d": 800, "1w": 2000, "1M": 4000,
}


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
    except Exception as e:
        rows = get_kline_bars(normalized, timeframe, start_date, end_date)
        if not rows:
            raise HTTPException(status_code=502, detail=f"K线加载失败: {e}") from e
        result = {
            "bars": rows,
            "source": source,
            "degraded": True,
            "error": str(e),
        }
    rows = result.get("bars") or []
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
    out["source"] = result.get("source") or source
    if result.get("degraded"):
        out["degraded"] = True
        if result.get("error"):
            out["source_error"] = result["error"]
    return out


def end_span(timeframe: str) -> bool:
    return timeframe in _LIMIT_DAYS
