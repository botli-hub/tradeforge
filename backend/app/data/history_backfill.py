"""历史K线补全器（local-first）"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from app.data.adapter import Bar, YahooRateLimitError, get_adapter
from app.data.history_repository import (
    create_backfill_job,
    get_kline_bars,
    infer_market,
    is_kline_range_covered,
    list_backfill_jobs,
    normalize_ts,
    update_backfill_job,
    upsert_instrument,
    upsert_kline_bars,
    upsert_subscription,
    upsert_sync_state,
)
from app.data.source_router import normalize_symbol, resolve_kline_source

logger = logging.getLogger(__name__)


def resolve_history_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """历史 K 线统一跟随自动路由。"""
    return resolve_kline_source(normalize_symbol(symbol), preferred_adapter)


def kline_source_chain(symbol: str, preferred_adapter: Optional[str] = None) -> List[str]:
    """美股: OpenD 优先, Yahoo 兜底; A/H 股仅 Futu。"""
    primary = resolve_history_source(symbol, preferred_adapter)
    market = infer_market(normalize_symbol(symbol))
    if market == 'US':
        chain = ['futu', 'yahoo']
        if primary in chain:
            return [primary] + [s for s in chain if s != primary]
        return chain
    return [primary]


def is_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, YahooRateLimitError):
        return True
    text = str(exc).lower()
    return '429' in text or 'too many requests' in text or 'yahoo 429' in text


def _bar_to_row(bar: Bar) -> Dict[str, Any]:
    return {
        'timestamp': bar.timestamp,
        'open': bar.open,
        'high': bar.high,
        'low': bar.low,
        'close': bar.close,
        'volume': bar.volume,
        'turnover': 0,
    }


def backfill_kline_range(symbol: str, timeframe: str, start_date: str, end_date: str, host: str = '127.0.0.1', port: int = 11111, source: Optional[str] = None) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol)
    start_date = normalize_ts(start_date)
    end_date = normalize_ts(end_date)
    if source not in ('futu', 'yahoo', 'finnhub'):
        source = resolve_history_source(symbol, source)
    job_id = str(uuid.uuid4())
    create_backfill_job(job_id, symbol, timeframe, source, start_date, end_date)
    upsert_sync_state(symbol, timeframe, source, 'syncing')

    adapter = None
    try:
        adapter = get_adapter(adapter_type=source, host=host, port=port)
        if hasattr(adapter, 'connect') and not adapter.connect():
            raise RuntimeError(getattr(adapter, 'last_error', None) or f'连接 {source} 行情源失败')

        bars = adapter.get_klines(symbol=symbol, timeframe=timeframe, start_date=start_date, end_date=end_date)
        if not bars:
            raise RuntimeError(getattr(adapter, 'last_error', None) or f'{source} 未返回任何K线')

        rows = [_bar_to_row(bar) for bar in bars]
        upsert_instrument(symbol, source_symbol=symbol, name=symbol)
        upsert_subscription(symbol, name=symbol, source_hint=source, enabled=True)
        written = upsert_kline_bars(symbol, timeframe, rows, source=source)
        update_backfill_job(job_id, 'success')
        upsert_sync_state(symbol, timeframe, source, 'success')
        return {
            'job_id': job_id,
            'symbol': symbol,
            'timeframe': timeframe,
            'source': source,
            'written': written,
            'start_ts': start_date,
            'end_ts': end_date,
        }
    except Exception as e:
        message = str(e)
        update_backfill_job(job_id, 'failed', message)
        upsert_sync_state(symbol, timeframe, source, 'error', message)
        raise
    finally:
        if adapter and hasattr(adapter, 'disconnect'):
            try:
                adapter.disconnect()
            except Exception:
                pass


def ensure_local_kline_range(symbol: str, timeframe: str, start_date: str, end_date: str, host: str = '127.0.0.1', port: int = 11111, preferred_adapter: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol)
    start_date = normalize_ts(start_date)
    end_date = normalize_ts(end_date)
    source = resolve_history_source(symbol, preferred_adapter)
    fetch_error: Optional[BaseException] = None
    used_source = source

    if force or not is_kline_range_covered(symbol, timeframe, start_date, end_date):
        for candidate in kline_source_chain(symbol, preferred_adapter):
            try:
                backfill_kline_range(
                    symbol,
                    timeframe,
                    start_date,
                    end_date,
                    host=host,
                    port=port,
                    source=candidate,
                )
                used_source = candidate
                fetch_error = None
                break
            except Exception as e:
                fetch_error = e
                logger.warning("补数 %s/%s via %s 失败: %s", symbol, timeframe, candidate, e)
                if is_rate_limited(e) and candidate == 'yahoo':
                    break

    rows = get_kline_bars(symbol, timeframe, start_date, end_date)
    if not rows:
        message = str(fetch_error) if fetch_error else f'{used_source} 未返回任何K线'
        raise RuntimeError(message)

    payload: Dict[str, Any] = {
        'symbol': symbol,
        'timeframe': timeframe,
        'source': used_source,
        'bars': rows,
    }
    if fetch_error is not None:
        payload['degraded'] = True
        payload['error'] = str(fetch_error)
    return payload


def get_history_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    return list_backfill_jobs(limit=limit)
