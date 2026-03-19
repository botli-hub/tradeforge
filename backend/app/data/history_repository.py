"""本地历史K线仓库"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.data.database import get_db


def now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def normalize_ts(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError('empty timestamp')
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec='seconds')


def infer_market(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol.endswith('.SH'):
        return 'SH'
    if symbol.endswith('.SZ'):
        return 'SZ'
    if symbol.endswith('.HK'):
        return 'HK'
    return 'US'


def infer_currency(market: str) -> str:
    return {'SH': 'CNY', 'SZ': 'CNY', 'HK': 'HKD', 'US': 'USD'}.get(market, 'USD')


def upsert_instrument(symbol: str, source_symbol: str, name: Optional[str] = None, asset_type: str = 'STOCK', lot_size: Optional[int] = None):
    market = infer_market(symbol)
    now = now_iso()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO instruments (symbol, market, asset_type, source_symbol, name, currency, lot_size, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            market=excluded.market,
            asset_type=excluded.asset_type,
            source_symbol=excluded.source_symbol,
            name=COALESCE(excluded.name, instruments.name),
            currency=excluded.currency,
            lot_size=COALESCE(excluded.lot_size, instruments.lot_size),
            updated_at=excluded.updated_at
        """,
        (symbol, market, asset_type, source_symbol, name, infer_currency(market), lot_size, now, now),
    )
    conn.commit()
    conn.close()


def upsert_kline_bars(symbol: str, timeframe: str, bars: Iterable[Dict[str, Any]], source: str, adjusted: str = 'none') -> int:
    now = now_iso()
    rows = []
    for bar in bars:
        rows.append((
            symbol,
            timeframe,
            normalize_ts(bar['timestamp']),
            float(bar['open']),
            float(bar['high']),
            float(bar['low']),
            float(bar['close']),
            float(bar.get('volume', 0) or 0),
            float(bar.get('turnover', 0) or 0),
            source,
            adjusted,
            now,
            now,
        ))

    if not rows:
        return 0

    conn = get_db()
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT INTO kline_bars (symbol, timeframe, ts, open, high, low, close, volume, turnover, source, adjusted, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            turnover=excluded.turnover,
            source=excluded.source,
            adjusted=excluded.adjusted,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def get_kline_bars(symbol: str, timeframe: str, start_ts: str, end_ts: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    start_ts = normalize_ts(start_ts)
    end_ts = normalize_ts(end_ts)
    conn = get_db()
    cursor = conn.cursor()
    sql = """
        SELECT symbol, timeframe, ts, open, high, low, close, volume, turnover, source, adjusted
        FROM kline_bars
        WHERE symbol = ? AND timeframe = ? AND ts >= ? AND ts <= ?
        ORDER BY ts ASC
    """
    params: List[Any] = [symbol, timeframe, start_ts, end_ts]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    cursor.execute(sql, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_kline_coverage(symbol: str, timeframe: str, source: Optional[str] = None) -> Dict[str, Any]:
    conn = get_db()
    cursor = conn.cursor()
    if source:
        cursor.execute(
            "SELECT earliest_ts, latest_ts, last_sync_at, last_success_at, status, error_message FROM kline_sync_state WHERE symbol = ? AND timeframe = ? AND source = ?",
            (symbol, timeframe, source),
        )
        state = cursor.fetchone()
    else:
        state = None

    cursor.execute(
        "SELECT MIN(ts) AS earliest_ts, MAX(ts) AS latest_ts, COUNT(*) AS bar_count FROM kline_bars WHERE symbol = ? AND timeframe = ?",
        (symbol, timeframe),
    )
    row = cursor.fetchone()
    conn.close()
    return {
        'symbol': symbol,
        'timeframe': timeframe,
        'source': source,
        'earliest_ts': row['earliest_ts'],
        'latest_ts': row['latest_ts'],
        'bar_count': row['bar_count'],
        'last_sync_at': state['last_sync_at'] if state else None,
        'last_success_at': state['last_success_at'] if state else None,
        'status': state['status'] if state else 'idle',
        'error_message': state['error_message'] if state else None,
    }


def is_kline_range_covered(symbol: str, timeframe: str, start_ts: str, end_ts: str) -> bool:
    coverage = get_kline_coverage(symbol, timeframe)
    if not coverage['earliest_ts'] or not coverage['latest_ts']:
        return False
    return coverage['earliest_ts'] <= normalize_ts(start_ts) and coverage['latest_ts'] >= normalize_ts(end_ts)


def upsert_sync_state(symbol: str, timeframe: str, source: str, status: str, error_message: Optional[str] = None):
    coverage = get_kline_coverage(symbol, timeframe)
    now = now_iso()
    last_success_at = now if status == 'success' else None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO kline_sync_state (symbol, timeframe, source, earliest_ts, latest_ts, last_sync_at, last_success_at, status, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe, source) DO UPDATE SET
            earliest_ts=excluded.earliest_ts,
            latest_ts=excluded.latest_ts,
            last_sync_at=excluded.last_sync_at,
            last_success_at=COALESCE(excluded.last_success_at, kline_sync_state.last_success_at),
            status=excluded.status,
            error_message=excluded.error_message
        """,
        (
            symbol,
            timeframe,
            source,
            coverage['earliest_ts'],
            coverage['latest_ts'],
            now,
            last_success_at,
            status,
            error_message,
        ),
    )
    conn.commit()
    conn.close()


def create_backfill_job(job_id: str, symbol: str, timeframe: str, source: str, start_ts: str, end_ts: str, priority: int = 5):
    now = now_iso()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO kline_backfill_jobs (id, symbol, timeframe, source, start_ts, end_ts, status, priority, retry_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?)
        """,
        (job_id, symbol, timeframe, source, normalize_ts(start_ts), normalize_ts(end_ts), priority, now, now),
    )
    conn.commit()
    conn.close()


def update_backfill_job(job_id: str, status: str, error_message: Optional[str] = None):
    now = now_iso()
    finished_at = now if status in ('success', 'failed') else None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE kline_backfill_jobs
        SET status = ?, error_message = ?, updated_at = ?, finished_at = COALESCE(?, finished_at)
        WHERE id = ?
        """,
        (status, error_message, now, finished_at, job_id),
    )
    conn.commit()
    conn.close()


def list_backfill_jobs(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM kline_backfill_jobs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def upsert_subscription(symbol: str, name: Optional[str] = None, source_hint: Optional[str] = None, enabled: bool = True):
    now = now_iso()
    market = infer_market(symbol)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO data_subscriptions (symbol, market, name, source_hint, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            market=excluded.market,
            name=COALESCE(excluded.name, data_subscriptions.name),
            source_hint=COALESCE(excluded.source_hint, data_subscriptions.source_hint),
            enabled=excluded.enabled,
            updated_at=excluded.updated_at
        """,
        (symbol, market, name, source_hint, 1 if enabled else 0, now, now),
    )
    conn.commit()
    conn.close()


def update_subscription_result(symbol: str, status: str, error: Optional[str] = None):
    now = now_iso()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE data_subscriptions
        SET last_scheduled_sync_at = ?, last_scheduled_status = ?, last_error = ?, updated_at = ?
        WHERE symbol = ?
        """,
        (now, status, error, now, symbol),
    )
    conn.commit()
    conn.close()


def list_subscriptions(enabled_only: bool = False) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    if enabled_only:
        cursor.execute("SELECT * FROM data_subscriptions WHERE enabled = 1 ORDER BY symbol ASC")
    else:
        cursor.execute("SELECT * FROM data_subscriptions ORDER BY symbol ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def set_subscription_enabled(symbol: str, enabled: bool):
    now = now_iso()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE data_subscriptions SET enabled = ?, updated_at = ? WHERE symbol = ?",
        (1 if enabled else 0, now, symbol),
    )
    conn.commit()
    conn.close()


def create_scheduler_run(run_id: str, trigger_type: str, target_date: str, status: str = 'running'):
    now = now_iso()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO history_scheduler_runs (id, trigger_type, target_date, status, started_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, trigger_type, target_date, status, now),
    )
    conn.commit()
    conn.close()


def finish_scheduler_run(run_id: str, status: str, summary: Optional[str] = None, error_message: Optional[str] = None):
    now = now_iso()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE history_scheduler_runs
        SET status = ?, finished_at = ?, summary = ?, error_message = ?
        WHERE id = ?
        """,
        (status, now, summary, error_message, run_id),
    )
    conn.commit()
    conn.close()


def list_scheduler_runs(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM history_scheduler_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def has_successful_scheduler_run(target_date: str) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM history_scheduler_runs WHERE target_date = ? AND status = 'success' LIMIT 1",
        (target_date,),
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None
