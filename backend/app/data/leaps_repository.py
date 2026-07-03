"""LEAPS 信号监控数据访问层"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.data.database import get_db, _now_iso


def get_watchlist() -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM leaps_watchlist ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_watchlist_item(symbol: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM leaps_watchlist WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_watchlist_item(symbol: str, name: str, floor_price: float, enabled: bool = True):
    conn = get_db()
    try:
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO leaps_watchlist (symbol, name, floor_price, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name = excluded.name,
                floor_price = excluded.floor_price,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (symbol, name, floor_price, 1 if enabled else 0, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_watchlist_item(symbol: str, **kwargs):
    fields = {k: v for k, v in kwargs.items() if k in ("floor_price", "enabled", "name")}
    if not fields:
        return
    fields["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [symbol]
    conn = get_db()
    try:
        conn.execute(f"UPDATE leaps_watchlist SET {set_clause} WHERE symbol = ?", values)
        conn.commit()
    finally:
        conn.close()


# ── 期权价格缓存 ──────────────────────────────────────────────────────────────

def get_option_price_history(contract_code: str, limit: int = 250) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume, iv
            FROM leaps_option_price_cache
            WHERE contract_code = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (contract_code, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def get_latest_cached_date(contract_code: str) -> Optional[str]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM leaps_option_price_cache WHERE contract_code = ?",
            (contract_code,),
        ).fetchone()
        return row["d"] if row else None
    finally:
        conn.close()


def save_option_prices(contract_code: str, bars: List[Dict[str, Any]]):
    """bars 中每项含 date/open/high/low/close/volume/iv(可为 None)"""
    if not bars:
        return
    conn = get_db()
    now = _now_iso()
    try:
        conn.executemany(
            """
            INSERT INTO leaps_option_price_cache
                (contract_code, date, open, high, low, close, volume, iv, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_code, date) DO UPDATE SET
                open = excluded.open, high = excluded.high,
                low = excluded.low, close = excluded.close,
                volume = excluded.volume,
                iv = COALESCE(excluded.iv, leaps_option_price_cache.iv)
            """,
            [
                (
                    contract_code,
                    b["date"], b.get("open"), b.get("high"),
                    b.get("low"), b.get("close"), b.get("volume"),
                    b.get("iv"), now,
                )
                for b in bars
            ],
        )
        conn.commit()
    finally:
        conn.close()


# ── IV 历史（52 周百分位用）────────────────────────────────────────────────────

def get_iv_history_52w(contract_code: str) -> List[float]:
    cutoff = (datetime.now() - timedelta(days=365)).date().isoformat()
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT iv FROM leaps_iv_history
            WHERE contract_code = ? AND date >= ?
            ORDER BY date
            """,
            (contract_code, cutoff),
        ).fetchall()
        return [r["iv"] for r in rows if r["iv"] is not None]
    finally:
        conn.close()


def save_iv_snapshot(contract_code: str, date: str, iv: float):
    conn = get_db()
    now = _now_iso()
    try:
        conn.execute(
            """
            INSERT INTO leaps_iv_history (contract_code, date, iv, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(contract_code, date) DO UPDATE SET iv = excluded.iv
            """,
            (contract_code, date, iv, now),
        )
        conn.commit()
    finally:
        conn.close()


# ── 信号日志 ──────────────────────────────────────────────────────────────────

def log_signal(
    symbol: str,
    contract_code: str,
    signal_level: str,
    trigger_price: float,
    ema_value: float,
    ema_type: str,
    iv_rank: float,
    underlying_price: float,
    floor_price: float,
    suggestions: Optional[List[Dict]] = None,
    is_intraday: bool = False,
) -> str:
    signal_id = str(uuid.uuid4())
    conn = get_db()
    now = _now_iso()
    try:
        conn.execute(
            """
            INSERT INTO leaps_signals
                (id, symbol, contract_code, signal_level, trigger_price,
                 ema_value, ema_type, iv_rank, underlying_price,
                 floor_price, suggestions, is_intraday, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, symbol, contract_code, signal_level,
                trigger_price, ema_value, ema_type, iv_rank,
                underlying_price, floor_price,
                json.dumps(suggestions or [], ensure_ascii=False),
                1 if is_intraday else 0, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return signal_id


def get_recent_signals(symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM leaps_signals WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM leaps_signals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["suggestions"] = json.loads(d["suggestions"] or "[]")
            result.append(d)
        return result
    finally:
        conn.close()


def count_symbol_signals_30d(symbol: str) -> int:
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(1) AS cnt FROM leaps_signals WHERE symbol = ? AND created_at >= ?",
            (symbol, cutoff),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


# ── 冷却状态 ──────────────────────────────────────────────────────────────────

def is_contract_in_cooldown(contract_code: str) -> bool:
    conn = get_db()
    now = datetime.now().isoformat()
    try:
        row = conn.execute(
            "SELECT cooldown_until FROM leaps_cooldowns WHERE contract_code = ?",
            (contract_code,),
        ).fetchone()
        return bool(row and row["cooldown_until"] > now)
    finally:
        conn.close()


def set_contract_cooldown(contract_code: str, symbol: str, trading_days: int = 5):
    """冷却 N 个自然日（近似交易日，取 7 日含周末）"""
    calendar_days = int(trading_days * 1.4)
    cooldown_until = (datetime.now() + timedelta(days=calendar_days)).isoformat()
    conn = get_db()
    now = _now_iso()
    try:
        conn.execute(
            """
            INSERT INTO leaps_cooldowns (contract_code, symbol, cooldown_until, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(contract_code) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                updated_at = excluded.updated_at
            """,
            (contract_code, symbol, cooldown_until, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_cooldowns() -> List[Dict[str, Any]]:
    conn = get_db()
    now = datetime.now().isoformat()
    try:
        rows = conn.execute(
            "SELECT * FROM leaps_cooldowns WHERE cooldown_until > ? ORDER BY cooldown_until",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
