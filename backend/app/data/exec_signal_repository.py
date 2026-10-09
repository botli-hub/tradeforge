"""Executable sell-option signal queue (席位 FirstTrade 消费; Forge 产信号).

仅 HTTP pull + ACK。不 webhook、不自动 FirstTrade。
仅 TSLL/SPCH 入库。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Sequence

from app.data.database import get_db, _now_iso

ALLOWED_SYMBOLS = frozenset({"TSLL", "SPCH"})
TERMINAL_ACK_STATUSES = frozenset({"consumed", "ignored"})
VALID_ACK_STATUSES = frozenset({"consumed", "ignored"})
VALID_SOURCES = frozenset({"touch", "suggest", "dual", "score"})


def ensure_exec_signal_tables(conn=None) -> None:
    owns = conn is None
    if owns:
        conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wheel_exec_signals (
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT NOT NULL,
                suggested_limit REAL,
                emitted_at TEXT NOT NULL,
                source TEXT NOT NULL,
                qty INTEGER NOT NULL DEFAULT 1,
                contract_code TEXT,
                bid REAL,
                ask REAL,
                quote_asof TEXT,
                leaps_signal_id TEXT,
                touch_ma TEXT,
                touch_timeframe TEXT,
                meta TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wheel_exec_signal_acks (
                signal_id TEXT NOT NULL,
                consumer TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT,
                acked_at TEXT NOT NULL,
                PRIMARY KEY (signal_id, consumer)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wheel_exec_signals_sym_emitted "
            "ON wheel_exec_signals(symbol, emitted_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wheel_exec_acks_status "
            "ON wheel_exec_signal_acks(signal_id, status)"
        )
        # 兼容 #54 已建库:补触线均线/周期列(旧行保持 NULL)
        for ddl in (
            "ALTER TABLE wheel_exec_signals ADD COLUMN touch_ma TEXT",
            "ALTER TABLE wheel_exec_signals ADD COLUMN touch_timeframe TEXT",
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()


def normalize_side(side: Any) -> Optional[str]:
    """→ Put | Call; 无法识别返回 None。"""
    if side is None:
        return None
    s = str(side).strip()
    u = s.upper()
    if u in ("PUT", "P", "WHEEL_PUT", "SELL_PUT", "TIMING_PUT", "PUT_TOUCH"):
        return "Put"
    if u in ("CALL", "C", "WHEEL_CALL", "SELL_CALL", "TIMING_CALL", "CALL_TOUCH"):
        return "Call"
    if s in ("Put", "Call"):
        return s
    return None


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def is_allowed_symbol(symbol: Any) -> bool:
    return normalize_symbol(symbol) in ALLOWED_SYMBOLS


def normalize_touch_ma(ema_type: Any) -> Optional[str]:
    """→ EMA50 / EMA200; 无法识别返回 None。"""
    if ema_type is None:
        return None
    u = str(ema_type).strip().upper().replace(" ", "")
    if not u:
        return None
    if u in ("EMA50", "MA50", "50"):
        return "EMA50"
    if u in ("EMA200", "MA200", "200"):
        return "EMA200"
    if u.startswith("EMA") and u[3:].isdigit():
        n = u[3:]
        if n == "50":
            return "EMA50"
        if n == "200":
            return "EMA200"
    return None


def normalize_touch_timeframe(timeframe: Any) -> Optional[str]:
    """→ 1h / 1d; 无法识别返回 None。"""
    if timeframe is None:
        return None
    s = str(timeframe).strip()
    if not s:
        return None
    low = s.lower()
    if low in ("1h", "60m", "k_60m", "hour", "hourly"):
        return "1h"
    if low in ("1d", "day", "daily", "k_day", "d"):
        return "1d"
    return None


def make_signal_id(
    symbol: str,
    side: str,
    strike: float,
    expiry: str,
    source: str,
    emitted_day: str,
    *,
    timeframe: str = "",
    ema_type: str = "",
    contract_code: str = "",
) -> str:
    """幂等键:同日同合约同源同触线桶 → 同一 signal_id。"""
    raw = "|".join(
        [
            normalize_symbol(symbol),
            normalize_side(side) or str(side),
            f"{float(strike):.6g}",
            str(expiry or "")[:10],
            str(source or ""),
            str(emitted_day or "")[:10],
            str(timeframe or ""),
            str(ema_type or ""),
            str(contract_code or "").upper().replace("US.", ""),
        ]
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:32]


def row_to_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    bid = row.get("bid")
    suggested = row.get("suggested_limit")
    if suggested is None and bid is not None:
        suggested = bid
    src = row.get("source")
    touch_ma = row.get("touch_ma")
    touch_tf = row.get("touch_timeframe")
    # 非 touch 源强制 null;旧行缺列时 get 为 None
    if src != "touch":
        touch_ma = None
        touch_tf = None
    else:
        touch_ma = normalize_touch_ma(touch_ma) if touch_ma is not None else None
        touch_tf = normalize_touch_timeframe(touch_tf) if touch_tf is not None else None
    return {
        "signal_id": row["signal_id"],
        "symbol": row["symbol"],
        "side": row["side"],
        "strike": row.get("strike"),
        "expiry": (str(row.get("expiry") or "")[:10] or None),
        "suggested_limit": suggested,
        "emitted_at": row.get("emitted_at"),
        "source": src,
        "qty": int(row.get("qty") or 1),
        "contract_code": row.get("contract_code"),
        "bid": bid,
        "ask": row.get("ask"),
        "quote_asof": row.get("quote_asof"),
        "touch_ma": touch_ma,
        "touch_timeframe": touch_tf,
    }


def emit_signal(
    *,
    symbol: str,
    side: str,
    strike: float,
    expiry: str,
    source: str = "touch",
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    quote_asof: Optional[str] = None,
    contract_code: Optional[str] = None,
    qty: int = 1,
    emitted_at: Optional[str] = None,
    leaps_signal_id: Optional[str] = None,
    timeframe: str = "",
    ema_type: str = "",
    signal_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """写入队列。非 TSLL/SPCH 返回 None(静默跳过)。不做 floor/DTE 闸。"""
    ensure_exec_signal_tables()
    sym = normalize_symbol(symbol)
    if not is_allowed_symbol(sym):
        return None
    side_n = normalize_side(side)
    if not side_n:
        raise ValueError(f"无效 side: {side}")
    if strike is None:
        raise ValueError("strike 必填")
    exp = str(expiry or "")[:10]
    if not exp:
        raise ValueError("expiry 必填")
    src = str(source or "touch").strip().lower()
    if src not in VALID_SOURCES:
        # 允许扩展源,但规范常见值
        src = src or "touch"
    now = emitted_at or _now_iso()
    day = str(now)[:10] or date.today().isoformat()
    bid_f = float(bid) if bid is not None else None
    ask_f = float(ask) if ask is not None else None
    suggested = bid_f  # suggested_limit = bid
    # 仅 touch 源持久化触线均线/周期;其他源强制 NULL
    if src == "touch":
        touch_ma = normalize_touch_ma(ema_type)
        touch_tf = normalize_touch_timeframe(timeframe)
    else:
        touch_ma = None
        touch_tf = None
    # 幂等键用规范化后的触线字段
    sid = signal_id or make_signal_id(
        sym, side_n, float(strike), exp, src, day,
        timeframe=touch_tf or "",
        ema_type=touch_ma or "",
        contract_code=contract_code or "",
    )
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT * FROM wheel_exec_signals WHERE signal_id = ?", (sid,)
        ).fetchone()
        if existing:
            return row_to_payload(dict(existing))
        conn.execute(
            """
            INSERT INTO wheel_exec_signals (
                signal_id, symbol, side, strike, expiry, suggested_limit,
                emitted_at, source, qty, contract_code, bid, ask, quote_asof,
                leaps_signal_id, touch_ma, touch_timeframe, meta, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid, sym, side_n, float(strike), exp, suggested,
                now, src, int(qty or 1), contract_code, bid_f, ask_f,
                quote_asof, leaps_signal_id, touch_ma, touch_tf,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                _now_iso(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM wheel_exec_signals WHERE signal_id = ?", (sid,)
        ).fetchone()
        return row_to_payload(dict(row))
    finally:
        conn.close()


def _terminal_acked_ids(conn, signal_ids: Sequence[str]) -> set:
    if not signal_ids:
        return set()
    placeholders = ",".join("?" * len(signal_ids))
    rows = conn.execute(
        f"""
        SELECT DISTINCT signal_id FROM wheel_exec_signal_acks
        WHERE signal_id IN ({placeholders})
          AND status IN ('consumed', 'ignored')
        """,
        list(signal_ids),
    ).fetchall()
    return {r["signal_id"] for r in rows}


def list_pending(
    *,
    symbols: Optional[Sequence[str]] = None,
    side: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    ensure_exec_signal_tables()
    if symbols is None:
        want = set(ALLOWED_SYMBOLS)
    else:
        want = {normalize_symbol(s) for s in symbols if normalize_symbol(s)}
        want &= ALLOWED_SYMBOLS
    if not want:
        return []
    side_n = normalize_side(side) if side else None
    conn = get_db()
    try:
        placeholders = ",".join("?" * len(want))
        params: list = list(want)
        sql = f"""
            SELECT * FROM wheel_exec_signals
            WHERE symbol IN ({placeholders})
        """
        if side_n:
            sql += " AND side = ?"
            params.append(side_n)
        sql += " ORDER BY emitted_at ASC, created_at ASC LIMIT ?"
        params.append(max(1, min(int(limit or 100), 500)))
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        done = _terminal_acked_ids(conn, [r["signal_id"] for r in rows])
        return [row_to_payload(r) for r in rows if r["signal_id"] not in done]
    finally:
        conn.close()


def get_signal(signal_id: str) -> Optional[Dict[str, Any]]:
    ensure_exec_signal_tables()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM wheel_exec_signals WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        return row_to_payload(dict(row)) if row else None
    finally:
        conn.close()


def ack_signal(
    signal_id: str,
    *,
    consumer: str,
    status: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """ACK 幂等:同一 signal_id+consumer 重复相同/已终态 status → ok。"""
    ensure_exec_signal_tables()
    st = str(status or "").strip().lower()
    if st not in VALID_ACK_STATUSES:
        raise ValueError("status 须为 consumed|ignored")
    cons = str(consumer or "").strip()
    if not cons:
        raise ValueError("consumer 必填")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT signal_id FROM wheel_exec_signals WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()
        if not row:
            raise KeyError(signal_id)

        prev = conn.execute(
            """
            SELECT * FROM wheel_exec_signal_acks
            WHERE signal_id = ? AND consumer = ?
            """,
            (signal_id, cons),
        ).fetchone()
        now = _now_iso()
        if prev:
            prev_d = dict(prev)
            # 已有终态 ACK → 幂等成功(不改写或仅补 note 不强制)
            return {
                "ok": True,
                "idempotent": True,
                "signal_id": signal_id,
                "consumer": cons,
                "status": prev_d.get("status"),
                "acked_at": prev_d.get("acked_at"),
                "note": prev_d.get("note"),
            }

        # 其他 consumer 已终态结束:本 consumer 仍可幂等落库
        any_terminal = conn.execute(
            """
            SELECT status, consumer, acked_at FROM wheel_exec_signal_acks
            WHERE signal_id = ? AND status IN ('consumed', 'ignored')
            LIMIT 1
            """,
            (signal_id,),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO wheel_exec_signal_acks
                (signal_id, consumer, status, note, acked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (signal_id, cons, st, note, now),
        )
        conn.commit()
        return {
            "ok": True,
            "idempotent": bool(any_terminal),
            "signal_id": signal_id,
            "consumer": cons,
            "status": st,
            "acked_at": now,
            "note": note,
        }
    finally:
        conn.close()
