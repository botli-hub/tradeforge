"""Wheel 策略数据访问层 + 可重放状态机

状态机:
  IDLE --SELL_PUT--> CSP_OPEN --EXPIRE/BUY_PUT_CLOSE--> IDLE
  IDLE --BUY_SHARES--> HOLDING(已持正股直接进轮,qty=股数,price=每股成本)
                     CSP_OPEN --ASSIGNED--> HOLDING
  HOLDING --SELL_CALL--> CC_OPEN --EXPIRE/BUY_CALL_CLOSE--> HOLDING
                         CC_OPEN --CALLED_AWAY--> CLOSED
  HOLDING --SELL_SHARES--> CLOSED

周期状态 = 该周期全部交易腿按时间顺序重放的结果。
修改/删除任意一笔交易后重放整个周期,保证账目一致;
重放非法(如删掉 SELL_PUT 后出现 ASSIGNED)则拒绝该次修改。
同一标的允许多个并行周期(cycle),操作用 cycle_id 定位。
"""
import uuid
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from app.data.database import get_db, _now_iso

TRADE_TYPES = (
    "SELL_PUT", "BUY_PUT_CLOSE", "SELL_CALL", "BUY_CALL_CLOSE",
    "EXPIRE", "ASSIGNED", "CALLED_AWAY", "SELL_SHARES", "BUY_SHARES",
)


class WheelError(Exception):
    """状态机/校验错误,API 层转 400"""


# ── targets ──────────────────────────────────────────────────────────────────

def get_targets() -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM wheel_targets ORDER BY market, symbol").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_target(symbol: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM wheel_targets WHERE symbol = ?", (symbol,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_target(data: Dict[str, Any]):
    now = _now_iso()
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO wheel_targets
                (symbol, name, market, floor_price, max_capital, delta_min, delta_max,
                 dte_min, dte_max, min_annualized, min_open_interest, enabled, created_at, updated_at)
            VALUES (:symbol, :name, :market, :floor_price, :max_capital, :delta_min, :delta_max,
                    :dte_min, :dte_max, :min_annualized, :min_open_interest, :enabled, :now, :now)
            ON CONFLICT(symbol) DO UPDATE SET
                name = excluded.name, market = excluded.market,
                floor_price = excluded.floor_price, max_capital = excluded.max_capital,
                delta_min = excluded.delta_min, delta_max = excluded.delta_max,
                dte_min = excluded.dte_min, dte_max = excluded.dte_max,
                min_annualized = excluded.min_annualized,
                min_open_interest = excluded.min_open_interest,
                enabled = excluded.enabled, updated_at = excluded.updated_at
            """,
            {**data, "now": now},
        )
        conn.commit()
    finally:
        conn.close()


def update_target(symbol: str, **kwargs) -> bool:
    allowed = ("name", "floor_price", "max_capital", "delta_min", "delta_max",
               "dte_min", "dte_max", "min_annualized", "min_open_interest", "enabled")
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return False
    fields["updated_at"] = _now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn = get_db()
    try:
        cur = conn.execute(
            f"UPDATE wheel_targets SET {set_clause} WHERE symbol = ?",
            list(fields.values()) + [symbol],
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_target(symbol: str) -> bool:
    conn = get_db()
    try:
        cur = conn.execute("DELETE FROM wheel_targets WHERE symbol = ?", (symbol,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── 状态机(纯函数)────────────────────────────────────────────────────────────

def _new_state() -> Dict[str, Any]:
    return {
        "status": "IDLE", "shares": 0.0, "share_cost": 0.0,
        "total_premium": 0.0, "total_fees": 0.0, "realized_pnl": None,
        "open_contract_code": None, "open_option_type": None,
        "open_strike": None, "open_expiry": None,
        "open_qty": 0.0, "open_price": 0.0, "open_contract_size": 100,
        "closed_at": None,
    }


def _apply(s: Dict[str, Any], t: Dict[str, Any]):
    """把一笔交易应用到状态上;非法则抛 WheelError"""
    tt = t["trade_type"]
    if tt not in TRADE_TYPES:
        raise WheelError(f"未知交易类型: {tt}")
    if s["status"] == "CLOSED":
        raise WheelError("周期已结束,不能再登记交易")

    qty = t.get("qty") or 1
    price = t.get("price") or 0
    fee = t.get("fee") or 0
    size = t.get("contract_size") or 100
    strike = t.get("strike")
    expiry = t.get("expiry")

    def need(status: str):
        if s["status"] != status:
            raise WheelError(f"状态 {s['status']} 不能执行 {tt}(需 {status})")

    def clear_open():
        s.update(open_contract_code=None, open_option_type=None, open_strike=None,
                 open_expiry=None, open_qty=0.0, open_price=0.0)

    if tt == "BUY_SHARES":
        need("IDLE")
        if not price or price <= 0:
            raise WheelError("BUY_SHARES 需要 price(每股成本)")
        if not qty or qty <= 0:
            raise WheelError("BUY_SHARES 需要 qty(股数)")
        s["shares"] = qty
        s["share_cost"] = price
        s["total_fees"] += fee
        s["status"] = "HOLDING"

    elif tt == "SELL_PUT":
        need("IDLE")
        if not strike or not expiry:
            raise WheelError("SELL_PUT 需要 strike 和 expiry")
        s["total_premium"] += qty * price * size - fee
        s["total_fees"] += fee
        s.update(status="CSP_OPEN", open_contract_code=t.get("contract_code"),
                 open_option_type="PUT", open_strike=strike, open_expiry=expiry,
                 open_qty=qty, open_price=price, open_contract_size=size)

    elif tt == "BUY_PUT_CLOSE":
        need("CSP_OPEN")
        s["total_premium"] -= qty * price * (size or s["open_contract_size"])
        s["total_fees"] += fee
        clear_open()
        s["status"] = "IDLE"

    elif tt == "EXPIRE":
        if s["status"] not in ("CSP_OPEN", "CC_OPEN"):
            raise WheelError("EXPIRE 需要有在场合约(CSP_OPEN 或 CC_OPEN)")
        s["status"] = "IDLE" if s["status"] == "CSP_OPEN" else "HOLDING"
        clear_open()

    elif tt == "ASSIGNED":
        need("CSP_OPEN")
        eff_strike = strike or s["open_strike"]
        if not eff_strike:
            raise WheelError("ASSIGNED 需要 strike(接货价)")
        eff_qty = qty or s["open_qty"] or 1
        eff_size = size or s["open_contract_size"] or 100
        s["shares"] = eff_qty * eff_size
        s["share_cost"] = eff_strike
        s["total_fees"] += fee
        clear_open()
        s["status"] = "HOLDING"

    elif tt == "SELL_CALL":
        need("HOLDING")
        if not strike or not expiry:
            raise WheelError("SELL_CALL 需要 strike 和 expiry")
        s["total_premium"] += qty * price * size - fee
        s["total_fees"] += fee
        s.update(status="CC_OPEN", open_contract_code=t.get("contract_code"),
                 open_option_type="CALL", open_strike=strike, open_expiry=expiry,
                 open_qty=qty, open_price=price, open_contract_size=size)

    elif tt == "BUY_CALL_CLOSE":
        need("CC_OPEN")
        s["total_premium"] -= qty * price * (size or s["open_contract_size"])
        s["total_fees"] += fee
        clear_open()
        s["status"] = "HOLDING"

    elif tt == "CALLED_AWAY":
        need("CC_OPEN")
        eff_strike = strike or s["open_strike"]
        if not eff_strike:
            raise WheelError("CALLED_AWAY 需要 strike(交货价)")
        s["realized_pnl"] = round(
            (eff_strike - s["share_cost"]) * s["shares"] + s["total_premium"] - fee, 4)
        s["total_fees"] += fee
        clear_open()
        s["status"] = "CLOSED"
        s["closed_at"] = t.get("traded_at")

    elif tt == "SELL_SHARES":
        need("HOLDING")
        if not price:
            raise WheelError("SELL_SHARES 需要 price(每股卖出价)")
        s["realized_pnl"] = round(
            (price - s["share_cost"]) * s["shares"] + s["total_premium"] - fee, 4)
        s["total_fees"] += fee
        s["status"] = "CLOSED"
        s["closed_at"] = t.get("traded_at")


def _replay(conn, cycle_id: str) -> Optional[Dict[str, Any]]:
    """按时间顺序重放周期的全部交易,写回 cycle 行。无交易返回 None。"""
    rows = conn.execute(
        "SELECT * FROM wheel_trades WHERE cycle_id = ? ORDER BY traded_at, created_at",
        (cycle_id,),
    ).fetchall()
    trades = [dict(r) for r in rows]
    if not trades:
        return None
    s = _new_state()
    for t in trades:
        _apply(s, t)
    started_at = trades[0]["traded_at"]
    conn.execute(
        """UPDATE wheel_cycles SET status=?, shares=?, share_cost=?, total_premium=?,
           total_fees=?, realized_pnl=?, open_contract_code=?, open_option_type=?,
           open_strike=?, open_expiry=?, open_qty=?, open_price=?, open_contract_size=?,
           started_at=?, closed_at=?, updated_at=? WHERE id=?""",
        (s["status"], s["shares"], s["share_cost"], round(s["total_premium"], 4),
         round(s["total_fees"], 4), s["realized_pnl"], s["open_contract_code"],
         s["open_option_type"], s["open_strike"], s["open_expiry"], s["open_qty"],
         s["open_price"], s["open_contract_size"], started_at, s["closed_at"],
         _now_iso(), cycle_id),
    )
    return s


# ── cycles 查询 ───────────────────────────────────────────────────────────────

def _enrich_cycle(c: Dict[str, Any]) -> Dict[str, Any]:
    shares = c.get("shares") or 0
    share_cost = c.get("share_cost") or 0
    premium = c.get("total_premium") or 0
    c["cost_basis"] = round(share_cost - premium / shares, 4) if shares > 0 else None
    expiry = c.get("open_expiry")
    if expiry and c.get("status") in ("CSP_OPEN", "CC_OPEN"):
        try:
            c["open_dte"] = (date.fromisoformat(str(expiry)[:10]) - date.today()).days
        except Exception:
            c["open_dte"] = None
    else:
        c["open_dte"] = None
    try:
        start = datetime.fromisoformat(c["started_at"])
        end = datetime.fromisoformat(c["closed_at"]) if c.get("closed_at") else datetime.now()
        c["duration_days"] = max((end - start).days, 1)
    except Exception:
        c["duration_days"] = None
    return c


def get_cycles(symbol: Optional[str] = None, status: Optional[str] = None,
               include_closed: bool = True) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM wheel_cycles WHERE 1=1"
        params: list = []
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if status:
            sql += " AND status = ?"
            params.append(status)
        elif not include_closed:
            sql += " AND status != 'CLOSED'"
        sql += " ORDER BY started_at DESC"
        rows = conn.execute(sql, params).fetchall()
        cycles = [_enrich_cycle(dict(r)) for r in rows]
        # HOLDING 裸奔天数:持股但没挂 Call,theta 收入在流失
        holding_ids = [c["id"] for c in cycles if c["status"] == "HOLDING"]
        if holding_ids:
            ph = ",".join("?" * len(holding_ids))
            last_map = {r["cycle_id"]: r["t"] for r in conn.execute(
                f"SELECT cycle_id, MAX(traded_at) AS t FROM wheel_trades WHERE cycle_id IN ({ph}) GROUP BY cycle_id",
                holding_ids).fetchall()}
            for c in cycles:
                if c["status"] == "HOLDING" and last_map.get(c["id"]):
                    try:
                        c["uncovered_days"] = max(
                            (datetime.now() - datetime.fromisoformat(str(last_map[c["id"]])[:19])).days, 0)
                    except Exception:
                        c["uncovered_days"] = None
        return cycles
    finally:
        conn.close()


def get_cycle(cycle_id: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)).fetchone()
        return _enrich_cycle(dict(row)) if row else None
    finally:
        conn.close()


def get_active_cycles(symbol: str) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM wheel_cycles WHERE symbol = ? AND status != 'CLOSED' ORDER BY started_at",
            (symbol,),
        ).fetchall()
        return [_enrich_cycle(dict(r)) for r in rows]
    finally:
        conn.close()


def get_trades(cycle_id: Optional[str] = None, symbol: Optional[str] = None,
               limit: int = 200) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        sql = "SELECT * FROM wheel_trades WHERE 1=1"
        params: list = []
        if cycle_id:
            sql += " AND cycle_id = ?"
            params.append(cycle_id)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY traded_at DESC, created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        trades = [dict(r) for r in rows]
        # Roll 配对识别(展示用,不落库):同 cycle 同日 BUY_*_CLOSE + SELL_* 同类型
        pair_map = {"BUY_PUT_CLOSE": "SELL_PUT", "BUY_CALL_CLOSE": "SELL_CALL"}
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            by_key.setdefault(f"{t['cycle_id']}|{str(t['traded_at'])[:10]}", []).append(t)
        for group in by_key.values():
            for buy in group:
                sell_type = pair_map.get(buy["trade_type"])
                if not sell_type:
                    continue
                sell = next((x for x in group if x["trade_type"] == sell_type
                             and not x.get("is_roll")), None)
                if sell is not None:
                    buy["is_roll"] = True
                    sell["is_roll"] = True
        return trades
    finally:
        conn.close()


# ── 登记 / 修改 / 删除交易 ─────────────────────────────────────────────────────

def record_trade(
    symbol: str,
    trade_type: str,
    contract_code: Optional[str] = None,
    strike: Optional[float] = None,
    expiry: Optional[str] = None,
    qty: float = 1,
    price: float = 0,
    fee: float = 0,
    contract_size: int = 100,
    note: Optional[str] = None,
    traded_at: Optional[str] = None,
    cycle_id: Optional[str] = None,
    new_cycle: bool = False,
) -> Dict[str, Any]:
    """登记一笔交易。cycle_id 指定操作哪个轮子;
    SELL_PUT/BUY_SHARES + new_cycle=True 强制新开一个并行轮子。返回重放后的 cycle。"""
    if trade_type not in TRADE_TYPES:
        raise WheelError(f"未知交易类型: {trade_type}")
    symbol = symbol.strip().upper()
    traded_at = traded_at or _now_iso()
    now = _now_iso()

    conn = get_db()
    try:
        # ── 定位/创建 cycle ────────────────────────────────────────────────
        if cycle_id:
            row = conn.execute("SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)).fetchone()
            if row is None:
                raise WheelError("指定的周期不存在")
            if row["symbol"] != symbol:
                raise WheelError(f"周期属于 {row['symbol']},不是 {symbol}")
        else:
            actives = conn.execute(
                "SELECT * FROM wheel_cycles WHERE symbol = ? AND status != 'CLOSED' ORDER BY started_at",
                (symbol,),
            ).fetchall()
            if trade_type in ("SELL_PUT", "BUY_SHARES"):
                idle = [r for r in actives if r["status"] == "IDLE"]
                if new_cycle or not idle:
                    cycle_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO wheel_cycles (id, symbol, status, started_at, updated_at) VALUES (?, ?, 'IDLE', ?, ?)",
                        (cycle_id, symbol, traded_at, now),
                    )
                else:
                    cycle_id = idle[0]["id"]
            else:
                if len(actives) == 0:
                    raise WheelError(f"{trade_type} 需要已有进行中的轮子")
                if len(actives) > 1:
                    raise WheelError("该标的有多个进行中的轮子,请指定 cycle_id")
                cycle_id = actives[0]["id"]

        # ── 插入交易并重放 ─────────────────────────────────────────────────
        trade_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO wheel_trades
               (id, cycle_id, symbol, trade_type, contract_code, strike, expiry, qty, price, fee,
                contract_size, note, traded_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trade_id, cycle_id, symbol, trade_type, contract_code, strike, expiry,
             qty, price, fee, contract_size, note, traded_at, now),
        )
        try:
            _replay(conn, cycle_id)
        except WheelError:
            conn.rollback()
            raise
        conn.commit()

        row = conn.execute("SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)).fetchone()
        return _enrich_cycle(dict(row))
    finally:
        conn.close()


def update_trade(trade_id: str, **kwargs) -> Dict[str, Any]:
    """修改交易腿并重放所属周期;重放非法则整体回滚"""
    allowed = ("trade_type", "contract_code", "strike", "expiry", "qty",
               "price", "fee", "contract_size", "note", "traded_at")
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        raise WheelError("没有可更新的字段")
    if "trade_type" in fields and fields["trade_type"] not in TRADE_TYPES:
        raise WheelError(f"未知交易类型: {fields['trade_type']}")

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM wheel_trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise WheelError("交易记录不存在")
        cycle_id = row["cycle_id"]
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE wheel_trades SET {set_clause} WHERE id = ?",
            list(fields.values()) + [trade_id],
        )
        try:
            _replay(conn, cycle_id)
        except WheelError as e:
            conn.rollback()
            raise WheelError(f"修改后周期重放失败,已回滚:{e}")
        conn.commit()
        row = conn.execute("SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)).fetchone()
        return _enrich_cycle(dict(row))
    finally:
        conn.close()


def delete_trade(trade_id: str) -> Dict[str, Any]:
    """删除交易腿并重放;若周期不再有交易则连周期一起删。返回 {deleted, cycle}"""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM wheel_trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise WheelError("交易记录不存在")
        cycle_id = row["cycle_id"]
        conn.execute("DELETE FROM wheel_trades WHERE id = ?", (trade_id,))
        try:
            state = _replay(conn, cycle_id)
        except WheelError as e:
            conn.rollback()
            raise WheelError(f"删除后周期重放失败,已回滚:{e}")
        cycle = None
        if state is None:
            conn.execute("DELETE FROM wheel_cycles WHERE id = ?", (cycle_id,))
        conn.commit()
        if state is not None:
            r = conn.execute("SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)).fetchone()
            cycle = _enrich_cycle(dict(r)) if r else None
        return {"deleted": True, "cycle": cycle}
    finally:
        conn.close()


# ── KV ───────────────────────────────────────────────────────────────────────

def get_kv(key: str) -> Optional[str]:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_kv(key: str, value: str):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO app_kv (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, value, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ── 资金占用 ──────────────────────────────────────────────────────────────────

def get_capital_usage() -> Dict[str, Any]:
    """按标的计算当前占用:CSP 担保 + 持股成本。用于占用视图和超额校验"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM wheel_cycles WHERE status != 'CLOSED'"
        ).fetchall()
    finally:
        conn.close()
    per_symbol: Dict[str, Dict[str, float]] = {}
    for r in rows:
        c = dict(r)
        s = per_symbol.setdefault(c["symbol"], {"csp_collateral": 0.0, "holding_cost": 0.0})
        if c["status"] == "CSP_OPEN" and c.get("open_strike"):
            s["csp_collateral"] += (c["open_strike"] or 0) * (c["open_qty"] or 1) * (c["open_contract_size"] or 100)
        if (c.get("shares") or 0) > 0:
            s["holding_cost"] += (c["shares"] or 0) * (c["share_cost"] or 0)
    total_csp = sum(s["csp_collateral"] for s in per_symbol.values())
    total_holding = sum(s["holding_cost"] for s in per_symbol.values())
    return {
        "per_symbol": per_symbol,
        "csp_collateral": round(total_csp, 2),
        "holding_cost": round(total_holding, 2),
        "total_committed": round(total_csp + total_holding, 2),
        # 压力测试:若在场 put 全部被行权,总占用 = 现有持股 + 全部担保转为股票
        "assignment_stress": round(total_csp + total_holding, 2),
    }


def get_last_trade_time(symbol: str) -> Optional[str]:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT MAX(traded_at) AS t FROM wheel_trades WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row["t"] if row else None
    finally:
        conn.close()


# ── 统计 ─────────────────────────────────────────────────────────────────────

def get_stats() -> Dict[str, Any]:
    conn = get_db()
    try:
        month_start = date.today().replace(day=1).isoformat()

        def _premium_since(cutoff: Optional[str]) -> float:
            sql = """
                SELECT COALESCE(SUM(CASE
                    WHEN trade_type IN ('SELL_PUT','SELL_CALL') THEN qty*price*contract_size - fee
                    WHEN trade_type IN ('BUY_PUT_CLOSE','BUY_CALL_CLOSE') THEN -(qty*price*contract_size + fee)
                    ELSE 0 END), 0) AS v
                FROM wheel_trades
            """
            params: list = []
            if cutoff:
                sql += " WHERE traded_at >= ?"
                params.append(cutoff)
            return float(conn.execute(sql, params).fetchone()["v"])

        active = conn.execute(
            "SELECT COUNT(1) AS c FROM wheel_cycles WHERE status != 'CLOSED'"
        ).fetchone()["c"]
        closed_rows = conn.execute(
            "SELECT realized_pnl FROM wheel_cycles WHERE status = 'CLOSED'"
        ).fetchall()
        realized_total = sum(r["realized_pnl"] or 0 for r in closed_rows)

        expiring = conn.execute(
            """SELECT id, symbol, open_contract_code, open_option_type, open_strike, open_expiry, status
               FROM wheel_cycles WHERE status IN ('CSP_OPEN','CC_OPEN') AND open_expiry IS NOT NULL"""
        ).fetchall()
        expiring_soon = []
        for r in expiring:
            try:
                dte = (date.fromisoformat(str(r["open_expiry"])[:10]) - date.today()).days
            except Exception:
                continue
            if dte <= 7:
                d = dict(r)
                d["dte"] = dte
                expiring_soon.append(d)

        result = {
            "active_cycles": active,
            "closed_cycles": len(closed_rows),
            "premium_month": round(_premium_since(month_start), 2),
            "premium_total": round(_premium_since(None), 2),
            "realized_pnl_total": round(realized_total, 2),
            "expiring_soon": sorted(expiring_soon, key=lambda x: x["dte"]),
        }
    finally:
        conn.close()
    result["capital"] = get_capital_usage()
    return result
