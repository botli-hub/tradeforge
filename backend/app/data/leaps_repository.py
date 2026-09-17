"""LEAPS 信号监控数据访问层"""
import json
import re
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


def delete_watchlist_item(symbol: str) -> bool:
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM leaps_watchlist WHERE symbol = ?", (symbol,))
        conn.commit()
        return cur.rowcount > 0
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


# ── 期权价格缓存（按 timeframe 分桶: 1d Put / 1h Call）────────────────────────

def _tf(timeframe: Optional[str] = None) -> str:
    s = str(timeframe or "1d").strip().lower()
    if s in ("1h", "60m", "k_60m", "hour", "hourly", "h"):
        return "1h"
    return "1d"


def get_option_price_history(contract_code: str, limit: int = 250,
                             timeframe: Optional[str] = None) -> List[Dict[str, Any]]:
    tf = _tf(timeframe)
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume, iv, timeframe
            FROM leaps_option_price_cache
            WHERE contract_code = ? AND COALESCE(timeframe, '1d') = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (contract_code, tf, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def get_latest_cached_date(contract_code: str,
                           timeframe: Optional[str] = None) -> Optional[str]:
    tf = _tf(timeframe)
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT MAX(date) AS d FROM leaps_option_price_cache
               WHERE contract_code = ? AND COALESCE(timeframe, '1d') = ?""",
            (contract_code, tf),
        ).fetchone()
        return row["d"] if row else None
    finally:
        conn.close()


def save_option_prices(contract_code: str, bars: List[Dict[str, Any]],
                       timeframe: Optional[str] = None):
    """bars 中每项含 date/open/high/low/close/volume/iv(可为 None)。
    主键 (contract_code, timeframe, date)：1h 与日K 不互相覆盖。"""
    if not bars:
        return
    tf = _tf(timeframe)
    conn = get_db()
    now = _now_iso()
    try:
        conn.executemany(
            """
            INSERT INTO leaps_option_price_cache
                (contract_code, timeframe, date, open, high, low, close, volume, iv, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_code, timeframe, date) DO UPDATE SET
                open = excluded.open, high = excluded.high,
                low = excluded.low, close = excluded.close,
                volume = excluded.volume,
                iv = COALESCE(excluded.iv, leaps_option_price_cache.iv)
            """,
            [
                (
                    contract_code, tf,
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
    timeframe: Optional[str] = None,
) -> str:
    signal_id = str(uuid.uuid4())
    conn = get_db()
    now = _now_iso()
    tf = _tf(timeframe)
    try:
        conn.execute(
            """
            INSERT INTO leaps_signals
                (id, symbol, contract_code, signal_level, trigger_price,
                 ema_value, ema_type, iv_rank, underlying_price,
                 floor_price, suggestions, is_intraday, created_at, timeframe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, symbol, contract_code, signal_level,
                trigger_price, ema_value, ema_type, iv_rank,
                underlying_price, floor_price,
                json.dumps(suggestions or [], ensure_ascii=False),
                1 if is_intraday else 0, now, tf,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return signal_id


def get_recent_signals(symbol: Optional[str] = None, limit: int = 50,
                       levels: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM leaps_signals WHERE 1=1"
        params: list = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if levels:
            sql += f" AND signal_level IN ({','.join('?' * len(levels))})"
            params.extend(levels)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
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


# ── Wheel 开仓时机历史(按合约去重合并)────────────────────────────────────────

def upsert_timing_history(sig) -> None:
    """sig: LeapsSignal(dataclass)。按 (contract_code, timeframe) 去重。
    Put 日K 与 Call 1h 同一合约代码也不会互相覆盖。"""
    side = "CALL" if "CALL" in (sig.signal_level or "") else "PUT"
    tf = _tf(getattr(sig, "timeframe", None) or ("1h" if side == "CALL" else "1d"))
    now = _now_iso()
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO wheel_timing_history
                (contract_code, timeframe, symbol, side, strike, expiry, ema_type, ema_value,
                 trigger_price, iv_rank, underlying_price,
                 delta, bid, annualized, dte, below_floor,
                 times_triggered, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(contract_code, timeframe) DO UPDATE SET
                strike = excluded.strike, expiry = excluded.expiry,
                ema_type = excluded.ema_type, ema_value = excluded.ema_value,
                trigger_price = excluded.trigger_price, iv_rank = excluded.iv_rank,
                underlying_price = excluded.underlying_price,
                delta = excluded.delta, bid = excluded.bid,
                annualized = excluded.annualized, dte = excluded.dte,
                below_floor = excluded.below_floor,
                times_triggered = wheel_timing_history.times_triggered + 1,
                last_seen = excluded.last_seen
            """,
            (sig.contract_code, tf, sig.symbol, side, sig.strike, sig.expiry,
             sig.ema_type, sig.ema_value, sig.trigger_price, sig.iv_rank,
             sig.underlying_price,
             getattr(sig, "delta", None), getattr(sig, "bid", None),
             getattr(sig, "annualized", None), getattr(sig, "dte", None),
             1 if getattr(sig, "below_floor", False) else 0,
             now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_timing_history(page: int = 1, page_size: int = 20,
                       symbol: Optional[str] = None) -> Dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    conn = get_db()
    try:
        where, params = "", []
        if symbol:
            where = " WHERE symbol = ?"
            params.append(symbol)
        total = conn.execute(
            f"SELECT COUNT(1) AS c FROM wheel_timing_history{where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT * FROM wheel_timing_history{where}
                ORDER BY last_seen DESC LIMIT ? OFFSET ?""",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        return {"total": total, "page": page, "page_size": page_size,
                "items": [dict(r) for r in rows]}
    finally:
        conn.close()


def get_latest_call_touch(symbol: str, max_age_hours: float = 72,
                          timeframe: Optional[str] = "1h") -> Optional[Dict[str, Any]]:
    """该标的最近一次 CALL 触线。默认只要 1h(CC 挂机发现不变);可传 1d。
    timeframe=None 时不按周期过滤。过期返回 None。"""
    if not symbol:
        return None
    from datetime import datetime, timedelta
    try:
        hours = max(1.0, float(max_age_hours or 72))
    except (TypeError, ValueError):
        hours = 72.0
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    tf = None if timeframe is None else _tf(timeframe)
    sym = str(symbol).strip().upper()
    conn = get_db()
    try:
        if tf:
            row = conn.execute(
                """SELECT * FROM wheel_timing_history
                   WHERE symbol = ? AND UPPER(side) = 'CALL'
                     AND COALESCE(timeframe, '1h') = ?
                     AND last_seen >= ?
                   ORDER BY last_seen DESC LIMIT 1""",
                (sym, tf, cutoff),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM wheel_timing_history
                   WHERE symbol = ? AND UPPER(side) = 'CALL' AND last_seen >= ?
                   ORDER BY last_seen DESC LIMIT 1""",
                (sym, cutoff),
            ).fetchone()
        if row:
            return dict(row)
        if tf:
            row = conn.execute(
                """SELECT * FROM leaps_signals
                   WHERE symbol = ? AND signal_level = 'WHEEL_CALL'
                     AND COALESCE(timeframe, '1h') = ?
                     AND created_at >= ?
                   ORDER BY created_at DESC LIMIT 1""",
                (sym, tf, cutoff),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM leaps_signals
                   WHERE symbol = ? AND signal_level = 'WHEEL_CALL' AND created_at >= ?
                   ORDER BY created_at DESC LIMIT 1""",
                (sym, cutoff),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ── 冷却状态 ──────────────────────────────────────────────────────────────────
# 触线/Wheel 冷却主键(contract_code 列)现为信号桶键,格式:
#   {SYMBOL}|{PUT|CALL}|{1h|1d}|{EMA50|EMA200}
# 例: QQQ|PUT|1h|EMA50
# 信号桶冷却语义=同一美股交易日不重复(存 session 日 YYYY-MM-DD),下一 RTH open 解冻。
# 旧单合约 code / UNCOV.* 等非桶键仍用自然日历 until=now+N days。


_TRADING_DAY_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_trading_day_id(value: Any) -> bool:
    return bool(_TRADING_DAY_ID_RE.match(str(value or "")))


def _upsert_cooldown(
    contract_code: str,
    symbol: str,
    cooldown_until: str,
    timeframe: Optional[str] = None,
) -> None:
    conn = get_db()
    now = _now_iso()
    tf = _tf(timeframe)
    try:
        conn.execute(
            """
            INSERT INTO leaps_cooldowns (contract_code, symbol, cooldown_until, created_at, updated_at, timeframe)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_code) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                updated_at = excluded.updated_at,
                timeframe = excluded.timeframe
            """,
            (contract_code, symbol, cooldown_until, now, now, tf),
        )
        conn.commit()
    finally:
        conn.close()


def make_signal_cooldown_key(
    symbol: str,
    side: str,
    timeframe: str,
    ema_type: str,
) -> str:
    """信号桶冷却键: SYMBOL|SIDE|TF|EMA (同桶多合约共享冷却窗口)。"""
    sym = str(symbol or "").upper().strip()
    side_u = str(side or "").upper().strip()
    if side_u in ("P", "PUT") or "PUT" in side_u:
        side_u = "PUT"
    elif side_u in ("C", "CALL") or "CALL" in side_u:
        side_u = "CALL"
    else:
        side_u = side_u or "PUT"

    tf = str(timeframe or "").strip().lower()
    if tf in ("1h", "60m", "60min", "hour", "hourly", "k_1h") or tf.startswith("60"):
        tf = "1h"
    elif tf in ("1d", "d", "day", "daily", "1day", "k_1d") or "day" in tf or tf == "1d":
        tf = "1d"
    elif "1h" in tf:
        tf = "1h"
    elif "1d" in tf:
        tf = "1d"
    else:
        tf = tf or "1d"
        if tf not in ("1h", "1d"):
            tf = "1d"

    ema = str(ema_type or "").upper().replace(" ", "")
    if ema in ("EMA200", "200", "E200"):
        ema = "EMA200"
    else:
        ema = "EMA50"
    return f"{sym}|{side_u}|{tf}|{ema}"


def signal_cooldown_key_from_signal(sig: Any) -> str:
    """从 LeapsSignal / dict 推导桶键。"""
    if isinstance(sig, dict):
        symbol = sig.get("symbol")
        level = str(sig.get("signal_level") or sig.get("side") or "")
        timeframe = sig.get("timeframe")
        ema_type = sig.get("ema_type")
    else:
        symbol = getattr(sig, "symbol", None)
        level = str(getattr(sig, "signal_level", None) or getattr(sig, "side", None) or "")
        timeframe = getattr(sig, "timeframe", None)
        ema_type = getattr(sig, "ema_type", None)
    side = "CALL" if "CALL" in level.upper() else "PUT"
    if isinstance(sig, dict) and sig.get("side"):
        side = str(sig.get("side"))
    elif not isinstance(sig, dict) and getattr(sig, "side", None):
        side = str(getattr(sig, "side"))
    return make_signal_cooldown_key(symbol or "", side, timeframe or "1d", ema_type or "EMA50")


def is_signal_bucket_in_cooldown(
    symbol: str,
    side: str,
    timeframe: str,
    ema_type: str,
    now: Optional[datetime] = None,
) -> bool:
    return is_contract_in_cooldown(
        make_signal_cooldown_key(symbol, side, timeframe, ema_type),
        now=now,
    )


def set_signal_bucket_cooldown(
    symbol: str,
    side: str,
    timeframe: str,
    ema_type: str,
    trading_days: int = 1,
    now: Optional[datetime] = None,
) -> str:
    """写入桶冷却;返回所用键。

    存当前美股交易日 id(YYYY-MM-DD),同日再触 → 跳过。
    trading_days 已弃用(信号桶路径忽略;保留形参兼容旧调用)。
    """
    _ = trading_days  # deprecated for signal-bucket path
    from app.core.wheel_today import us_equity_session_date

    key = make_signal_cooldown_key(symbol, side, timeframe, ema_type)
    day_id = us_equity_session_date(now)
    _upsert_cooldown(key, str(symbol or "").upper(), day_id, timeframe=timeframe)
    return key


def arm_signal_bucket_cooldowns(
    signals: List[Any],
    trading_days: int = 1,
    now: Optional[datetime] = None,
) -> List[str]:
    """一批信号按唯一桶键写入冷却(同批择优后再调用,避免扫中途互斥)。

    trading_days 已弃用(忽略);写入 us_equity_session_date。
    """
    _ = trading_days
    from app.core.wheel_today import us_equity_session_date

    day_id = us_equity_session_date(now)
    armed: List[str] = []
    seen = set()
    for sig in signals or []:
        key = signal_cooldown_key_from_signal(sig)
        if not key or key in seen:
            continue
        seen.add(key)
        if isinstance(sig, dict):
            sym = str(sig.get("symbol") or "").upper()
            tf = sig.get("timeframe") or "1d"
        else:
            sym = str(getattr(sig, "symbol", "") or "").upper()
            tf = getattr(sig, "timeframe", None) or "1d"
        _upsert_cooldown(key, sym or "?", day_id, timeframe=str(tf))
        armed.append(key)
    return armed


def is_contract_in_cooldown(
    contract_code: str,
    now: Optional[datetime] = None,
) -> bool:
    """冷却判定。

    - cooldown_until 为 YYYY-MM-DD → 美股交易日模式:等于当前 session 日则冷却中
    - 否则 → 旧自然日历时间戳: until > now
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT cooldown_until FROM leaps_cooldowns WHERE contract_code = ?",
            (contract_code,),
        ).fetchone()
        if not row:
            return False
        until = row["cooldown_until"]
        if _is_trading_day_id(until):
            from app.core.wheel_today import us_equity_session_date
            return str(until) == us_equity_session_date(now)
        now_iso = (now or datetime.now()).isoformat()
        return bool(until > now_iso)
    finally:
        conn.close()


def set_contract_cooldown(contract_code: str, symbol: str, trading_days: int = 5,
                          timeframe: Optional[str] = None):
    """冷却 N 个自然日历日（LEAPS/UNCOV 等非信号桶路径）。

    参数名 trading_days 为历史兼容；语义为自然日：fill 1 → 冷却 1 天。
    信号桶请用 set_signal_bucket_cooldown / arm_signal_bucket_cooldowns
    (存交易日 id,不走本函数)。
    timeframe 仅记录。
    """
    calendar_days = int(trading_days)
    cooldown_until = (datetime.now() + timedelta(days=calendar_days)).isoformat()
    _upsert_cooldown(contract_code, symbol, cooldown_until, timeframe=timeframe)


def get_all_cooldowns(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """活跃冷却列表。交易日 id 行:当前 session 日匹配则视为活跃。"""
    from app.core.wheel_today import us_equity_session_date

    conn = get_db()
    now_dt = now or datetime.now()
    now_iso = now_dt.isoformat()
    session_day = us_equity_session_date(now_dt)
    try:
        rows = conn.execute(
            "SELECT * FROM leaps_cooldowns ORDER BY cooldown_until"
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            until = d.get("cooldown_until")
            if _is_trading_day_id(until):
                if str(until) == session_day:
                    out.append(d)
            elif until and until > now_iso:
                out.append(d)
        return out
    finally:
        conn.close()
