"""历史K线补全器（local-first）"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from app.data.adapter import Bar, get_adapter
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
from app.data.kline_errors import KlineRateLimited, is_rate_limited_error
from app.data.source_router import normalize_symbol, resolve_kline_source


def resolve_history_source(symbol: str, preferred_adapter: Optional[str] = None) -> str:
    """历史 K 线统一跟随自动路由。"""
    return resolve_kline_source(normalize_symbol(symbol), preferred_adapter)


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


def _candidate_sources(symbol: str, source: str) -> List[str]:
    """美股：OpenD 优先，失败再 Yahoo；其它市场不跨源。"""
    ordered = [source]
    if infer_market(symbol) == 'US' and source == 'futu' and 'yahoo' not in ordered:
        ordered.append('yahoo')
    return ordered


def _raise_if_rate_limited(message: object) -> None:
    if is_rate_limited_error(message):
        raise KlineRateLimited(str(message))


def _fetch_adapter_bars(symbol: str, timeframe: str, start_date: str, end_date: str,
                        host: str, port: int, source: str):
    adapter = get_adapter(adapter_type=source, host=host, port=port)
    try:
        if hasattr(adapter, 'connect') and not adapter.connect():
            err = getattr(adapter, 'last_error', None) or f'连接 {source} 行情源失败'
            _raise_if_rate_limited(err)
            raise RuntimeError(err)

        bars = adapter.get_klines(symbol=symbol, timeframe=timeframe, start_date=start_date, end_date=end_date)
        if not bars:
            err = getattr(adapter, 'last_error', None) or f'{source} 未返回任何K线'
            _raise_if_rate_limited(err)
            raise RuntimeError(err)
        return adapter, bars
    except Exception:
        if adapter and hasattr(adapter, 'disconnect'):
            try:
                adapter.disconnect()
            except Exception:
                pass
        raise


def _persist_bars(symbol: str, timeframe: str, source: str, bars, job_id: str,
                  start_date: str, end_date: str) -> Dict[str, Any]:
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


def backfill_kline_range(symbol: str, timeframe: str, start_date: str, end_date: str, host: str = '127.0.0.1', port: int = 11111, source: Optional[str] = None) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol)
    start_date = normalize_ts(start_date)
    end_date = normalize_ts(end_date)
    source = resolve_history_source(symbol, source)
    job_id = str(uuid.uuid4())
    create_backfill_job(job_id, symbol, timeframe, source, start_date, end_date)
    upsert_sync_state(symbol, timeframe, source, 'syncing')

    last_error: Optional[BaseException] = None
    for src in _candidate_sources(symbol, source):
        adapter = None
        try:
            adapter, bars = _fetch_adapter_bars(
                symbol, timeframe, start_date, end_date, host, port, src,
            )
            try:
                return _persist_bars(symbol, timeframe, src, bars, job_id, start_date, end_date)
            finally:
                if adapter and hasattr(adapter, 'disconnect'):
                    try:
                        adapter.disconnect()
                    except Exception:
                        pass
        except KlineRateLimited as e:
            last_error = e
            message = str(e)
            update_backfill_job(job_id, 'failed', message)
            upsert_sync_state(symbol, timeframe, src, 'error', message)
            raise
        except Exception as e:
            last_error = e
            continue

    message = str(last_error) if last_error else f'{source} 未返回任何K线'
    _raise_if_rate_limited(message)
    update_backfill_job(job_id, 'failed', message)
    upsert_sync_state(symbol, timeframe, source, 'error', message)
    raise RuntimeError(message) if last_error is None else last_error


def ensure_local_kline_range(symbol: str, timeframe: str, start_date: str, end_date: str, host: str = '127.0.0.1', port: int = 11111, preferred_adapter: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    symbol = normalize_symbol(symbol)
    start_date = normalize_ts(start_date)
    end_date = normalize_ts(end_date)
    source = resolve_history_source(symbol, preferred_adapter)
    fetch_error: Optional[BaseException] = None

    if force or not is_kline_range_covered(symbol, timeframe, start_date, end_date):
        try:
            backfill_kline_range(symbol, timeframe, start_date, end_date, host=host, port=port, source=source)
        except Exception as e:
            fetch_error = e

    rows = get_kline_bars(symbol, timeframe, start_date, end_date)
    if rows:
        payload: Dict[str, Any] = {
            'symbol': symbol,
            'timeframe': timeframe,
            'source': source,
            'bars': rows,
        }
        if fetch_error is not None:
            payload['degraded'] = True
            payload['error'] = str(fetch_error)
        return payload

    if fetch_error is not None:
        raise fetch_error
    raise RuntimeError(f'{source} 未返回任何K线')


def get_history_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    return list_backfill_jobs(limit=limit)
