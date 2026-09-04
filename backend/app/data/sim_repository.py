"""Sim Wheel 纸面账数据层 — 独立表,绝不读写 wheel_cycles / FirstTrade。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.data.database import get_db, _now_iso
from app.core.sim_wheel import familiarity_badge

ACTIVE = ("IDLE", "CSP_OPEN", "HOLDING", "CC_OPEN")


def ensure_sim_tables(conn=None) -> None:
    """幂等建表(init_db 与测试均可调用)。"""
    own = conn is None
    if own:
        conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_cycle (
            id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'IDLE',
            level TEXT,
            shares REAL DEFAULT 0,
            share_cost REAL DEFAULT 0,
            cost_basis REAL,
            total_premium REAL DEFAULT 0,
            realized_pnl REAL,
            open_strike REAL,
            open_expiry TEXT,
            open_qty REAL DEFAULT 0,
            open_price REAL DEFAULT 0,
            open_option_type TEXT,
            open_contract_code TEXT,
            floor_price REAL,
            alert_fingerprint TEXT,
            holding_since TEXT,
            cc_force_tagged INTEGER DEFAULT 0,
            started_at TEXT NOT NULL,
            closed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_leg (
            id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL,
            leg_type TEXT NOT NULL,
            strike REAL,
            expiry TEXT,
            qty REAL,
            price REAL,
            premium_net REAL,
            note TEXT,
            traded_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (cycle_id) REFERENCES sim_cycle(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id TEXT,
            symbol TEXT,
            event_type TEXT NOT NULL,
            fingerprint TEXT,
            detail TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sim_stats (
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            closed_cycles INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            total_days REAL DEFAULT 0,
            assign_count INTEGER DEFAULT 0,
            called_away_count INTEGER DEFAULT 0,
            premium_sum REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (strategy, symbol)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sim_cycle_sym ON sim_cycle(symbol, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sim_cycle_status ON sim_cycle(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sim_event_fp ON sim_event(fingerprint)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sim_leg_cycle ON sim_leg(cycle_id, traded_at)")
    if own:
        conn.commit()
        conn.close()


def fingerprint_used(fingerprint: str) -> bool:
    if not fingerprint:
        return False
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM sim_event WHERE fingerprint = ? LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def add_event(
    *,
    cycle_id: Optional[str],
    symbol: Optional[str],
    event_type: str,
    fingerprint: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> int:
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO sim_event (cycle_id, symbol, event_type, fingerprint, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                cycle_id,
                symbol,
                event_type,
                fingerprint,
                json.dumps(detail, ensure_ascii=False) if detail is not None else None,
                created_at or _now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def insert_cycle(cycle: Dict[str, Any]) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO sim_cycle (
                id, symbol, strategy, status, level, shares, share_cost, cost_basis,
                total_premium, realized_pnl, open_strike, open_expiry, open_qty, open_price,
                open_option_type, open_contract_code, floor_price, alert_fingerprint,
                holding_since, cc_force_tagged, started_at, closed_at, updated_at
            ) VALUES (
                :id, :symbol, :strategy, :status, :level, :shares, :share_cost, :cost_basis,
                :total_premium, :realized_pnl, :open_strike, :open_expiry, :open_qty, :open_price,
                :open_option_type, :open_contract_code, :floor_price, :alert_fingerprint,
                :holding_since, :cc_force_tagged, :started_at, :closed_at, :updated_at
            )""",
            cycle,
        )
        conn.commit()
    finally:
        conn.close()


def update_cycle(cycle_id: str, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db()
    try:
        conn.execute(
            f"UPDATE sim_cycle SET {cols} WHERE id = ?",
            list(fields.values()) + [cycle_id],
        )
        conn.commit()
    finally:
        conn.close()


def insert_leg(leg: Dict[str, Any]) -> None:
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO sim_leg (
                id, cycle_id, leg_type, strike, expiry, qty, price, premium_net, note, traded_at, created_at
            ) VALUES (
                :id, :cycle_id, :leg_type, :strike, :expiry, :qty, :price, :premium_net, :note, :traded_at, :created_at
            )""",
            leg,
        )
        conn.commit()
    finally:
        conn.close()


def get_cycle(cycle_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sim_cycle WHERE id = ?", (cycle_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_cycles(
    status: Optional[str] = None,
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
    include_closed: bool = True,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM sim_cycle WHERE 1=1"
        args: List[Any] = []
        if status:
            sql += " AND status = ?"
            args.append(status)
        elif not include_closed:
            sql += " AND status != 'CLOSED'"
        if strategy:
            sql += " AND strategy = ?"
            args.append(strategy)
        if symbol:
            sql += " AND symbol = ?"
            args.append(symbol.strip().upper())
        sql += " ORDER BY started_at DESC LIMIT ?"
        args.append(max(1, min(int(limit or 200), 500)))
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def find_open_cycle(symbol: str, strategy: str, status: str = "CSP_OPEN") -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT * FROM sim_cycle WHERE symbol = ? AND strategy = ? AND status = ?
               ORDER BY started_at DESC LIMIT 1""",
            (symbol.strip().upper(), strategy, status),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def find_symbol_active(
    symbol: str,
    statuses: Sequence[str] = ("HOLDING", "CC_OPEN", "CSP_OPEN"),
) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        ph = ",".join("?" * len(statuses))
        row = conn.execute(
            f"""SELECT * FROM sim_cycle WHERE symbol = ? AND status IN ({ph})
                ORDER BY started_at DESC LIMIT 1""",
            [symbol.strip().upper(), *statuses],
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def capital_usage() -> Dict[str, Any]:
    """纸面占用:CSP 担保 + 持股成本。不读实盘 wheel_cycles。"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM sim_cycle WHERE status != 'CLOSED'"
        ).fetchall()
    finally:
        conn.close()
    per: Dict[str, Dict[str, float]] = {}
    for r in rows:
        c = dict(r)
        s = per.setdefault(c["symbol"], {"csp_collateral": 0.0, "holding_cost": 0.0, "committed": 0.0})
        if c["status"] == "CSP_OPEN" and c.get("open_strike"):
            coll = (c["open_strike"] or 0) * (c["open_qty"] or 1) * 100
            s["csp_collateral"] += coll
        if (c.get("shares") or 0) > 0:
            s["holding_cost"] += (c["shares"] or 0) * (c.get("share_cost") or 0)
        s["committed"] = s["csp_collateral"] + s["holding_cost"]
    total = sum(v["committed"] for v in per.values())
    return {"per_symbol": per, "total_committed": round(total, 2)}


def record_closed_stats(
    cycle: Dict[str, Any],
    *,
    pnl: float,
    assigned: bool,
    called_away: bool,
    now: Optional[datetime] = None,
) -> None:
    strategy = cycle.get("strategy") or "put_touch"
    symbol = (cycle.get("symbol") or "").upper()
    iso = (now or datetime.now()).isoformat(timespec="seconds")
    days = 1.0
    try:
        start = datetime.fromisoformat(str(cycle.get("started_at") or "")[:19])
        end = now or datetime.now()
        days = max((end - start).days, 1)
    except Exception:
        pass
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM sim_stats WHERE strategy = ? AND symbol = ?",
            (strategy, symbol),
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO sim_stats (
                    strategy, symbol, closed_cycles, wins, total_pnl, total_days,
                    assign_count, called_away_count, premium_sum, max_drawdown, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy, symbol,
                    1 if pnl > 0 else 0,
                    float(pnl), float(days),
                    1 if assigned else 0,
                    1 if called_away else 0,
                    float(cycle.get("total_premium") or 0),
                    min(0.0, float(pnl)),
                    iso,
                ),
            )
        else:
            d = dict(row)
            closed = int(d["closed_cycles"] or 0) + 1
            wins = int(d["wins"] or 0) + (1 if pnl > 0 else 0)
            total_pnl = float(d["total_pnl"] or 0) + float(pnl)
            dd = min(float(d.get("max_drawdown") or 0), float(pnl), total_pnl)
            conn.execute(
                """UPDATE sim_stats SET
                    closed_cycles=?, wins=?, total_pnl=?, total_days=total_days+?,
                    assign_count=assign_count+?, called_away_count=called_away_count+?,
                    premium_sum=premium_sum+?, max_drawdown=?, updated_at=?
                   WHERE strategy=? AND symbol=?""",
                (
                    closed, wins, total_pnl, float(days),
                    1 if assigned else 0, 1 if called_away else 0,
                    float(cycle.get("total_premium") or 0), dd, iso,
                    strategy, symbol,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def list_stats(
    strategy: Optional[str] = None,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM sim_stats WHERE 1=1"
        args: List[Any] = []
        if strategy:
            sql += " AND strategy = ?"
            args.append(strategy)
        if symbol:
            sql += " AND symbol = ?"
            args.append(symbol.strip().upper())
        sql += " ORDER BY closed_cycles DESC, total_pnl DESC"
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()
    out = []
    for r in rows:
        n = int(r.get("closed_cycles") or 0)
        pnl = float(r.get("total_pnl") or 0)
        exp = (pnl / n) if n else 0.0
        r["expectancy"] = round(exp, 4)
        r["win_rate"] = round(int(r.get("wins") or 0) / n, 4) if n else 0.0
        r["avg_days"] = round(float(r.get("total_days") or 0) / n, 2) if n else 0.0
        r["assign_rate"] = round(int(r.get("assign_count") or 0) / n, 4) if n else 0.0
        r["called_away_rate"] = round(int(r.get("called_away_count") or 0) / n, 4) if n else 0.0
        r["familiarity"] = familiarity_badge(n, exp)
        r["label"] = "纸面/非实盘"
        out.append(r)
    return out


def list_events(limit: int = 100, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM sim_event WHERE 1=1"
        args: List[Any] = []
        if symbol:
            sql += " AND symbol = ?"
            args.append(symbol.strip().upper())
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit or 100), 500)))
        rows = []
        for r in conn.execute(sql, args).fetchall():
            d = dict(r)
            if d.get("detail"):
                try:
                    d["detail"] = json.loads(d["detail"])
                except Exception:
                    pass
            rows.append(d)
        return rows
    finally:
        conn.close()


def count_open() -> int:
    conn = get_db()
    try:
        return int(conn.execute(
            "SELECT COUNT(1) AS c FROM sim_cycle WHERE status != 'CLOSED'"
        ).fetchone()["c"])
    finally:
        conn.close()
