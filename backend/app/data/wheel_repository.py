"""Wheel 策略数据访问层 + 可重放状态机

状态机:
  IDLE --SELL_PUT--> CSP_OPEN --EXPIRE/BUY_PUT_CLOSE--> IDLE
  IDLE --BUY_SHARES--> HOLDING(已持正股直接进轮,qty=股数,price=每股成本)
                     CSP_OPEN --ASSIGNED--> HOLDING
  HOLDING --SELL_CALL--> CC_OPEN --EXPIRE/BUY_CALL_CLOSE--> HOLDING(或仍 CC_OPEN 若还有腿)
  CC_OPEN --SELL_CALL--> CC_OPEN(未覆盖股份足够时可再挂不同 Call)
                         CC_OPEN --CALLED_AWAY--> HOLDING/CC_OPEN/CLOSED(按腿)
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
from app.core.wheel_cc_legs import (
    CcLegError,
    apply_cc_close,
    apply_sell_call,
    cycle_open_cc_legs,
    parse_open_cc_legs_json,
    uncovered_shares_of,
)

TRADE_TYPES = (
    "SELL_PUT", "BUY_PUT_CLOSE", "SELL_CALL", "BUY_CALL_CLOSE",
    "EXPIRE", "ASSIGNED", "CALLED_AWAY", "SELL_SHARES", "BUY_SHARES",
)


class WheelError(Exception):
    """状态机/校验错误,API 层转 400"""


STATUS_LABELS_ZH = {
    "IDLE": "空仓", "CSP_OPEN": "卖Put中", "HOLDING": "持股",
    "CC_OPEN": "卖Call中", "CLOSED": "已结束",
}
TRADE_LABELS_ZH = {
    "SELL_PUT": "卖出Put", "BUY_PUT_CLOSE": "买回Put平仓",
    "SELL_CALL": "卖出Call", "BUY_CALL_CLOSE": "买回Call平仓",
    "EXPIRE": "到期作废", "ASSIGNED": "被行权接货",
    "CALLED_AWAY": "被行权交货", "SELL_SHARES": "卖出股票结束",
    "BUY_SHARES": "已持正股入轮",
}


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
    # Keep the persistence helper usable from migrations/tests and from callers
    # that only know the symbol and floor.  The API model supplies the same
    # defaults, but the repository must not rely on that boundary.
    values = {
        "symbol": str(data.get("symbol") or "").strip().upper(),
        "name": data.get("name") or data.get("symbol") or "",
        "market": data.get("market") or "US",
        "floor_price": data.get("floor_price", 0),
        "max_capital": data.get("max_capital", 0),
        "delta_min": data.get("delta_min", 0.15),
        "delta_max": data.get("delta_max", 0.30),
        "dte_min": data.get("dte_min", 10),
        "dte_max": data.get("dte_max", 55),
        "min_annualized": data.get("min_annualized", 15.0),
        "min_open_interest": data.get("min_open_interest", 100),
        "enabled": 1 if data.get("enabled", True) else 0,
    }
    if not values["symbol"]:
        raise WheelError("symbol 不能为空")
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
            {**values, "now": now},
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


def log_floor_change(
    symbol: str,
    old_floor: Optional[float],
    new_floor: float,
    source: str = "manual",
) -> None:
    """记录愿接价变更(复盘何时放宽/收紧)。"""
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO wheel_floor_log (symbol, old_floor, new_floor, source, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (symbol, old_floor, new_floor, source or "manual", _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_floor_log(symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_db()
    try:
        if symbol:
            rows = conn.execute(
                """SELECT * FROM wheel_floor_log WHERE symbol = ?
                   ORDER BY id DESC LIMIT ?""",
                (symbol.strip().upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wheel_floor_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
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
        "open_cc_legs": [],
        "closed_at": None,
    }


def _apply(s: Dict[str, Any], t: Dict[str, Any]):
    from app.core.wheel_ledger import apply_trade, LedgerError
    try:
        return apply_trade(s, t)
    except LedgerError as e:
        raise WheelError(str(e)) from e


def _replay(conn, cycle_id: str) -> Optional[Dict[str, Any]]:
    """按时间顺序重放周期的全部交易,写回 cycle 行。无交易返回 None。"""
    rows = conn.execute(
        "SELECT * FROM wheel_trades WHERE cycle_id = ? ORDER BY traded_at, created_at, rowid",
        (cycle_id,),
    ).fetchall()
    trades = [dict(r) for r in rows]
    if not trades:
        return None
    s = _new_state()
    for i, t in enumerate(trades):
        try:
            canonical = _apply(s, t)
            # Persist inferred close identity once; NAV reads these same facts.
            if canonical and any(t.get(k) != canonical.get(k) for k in ("strike", "expiry", "contract_code")):
                conn.execute("UPDATE wheel_trades SET strike=?,expiry=?,contract_code=? WHERE id=?",
                    (canonical.get("strike"),canonical.get("expiry"),canonical.get("contract_code"),t["id"]))
        except WheelError as e:
            # 常见踩坑:平仓/行权的成交时间早于开仓腿 → 重放时状态还是空仓
            later_open = next(
                (x for x in trades[i + 1:]
                 if x.get("trade_type") in ("SELL_PUT", "SELL_CALL", "BUY_SHARES")),
                None,
            )
            hint = ""
            if later_open and str(t.get("traded_at") or "") < str(later_open.get("traded_at") or ""):
                hint = (
                    f"。本笔成交时间 {t.get('traded_at')} 早于后续开仓腿 "
                    f"{later_open.get('trade_type')}@{later_open.get('traded_at')}；"
                    f"状态机按时间顺序重放，请把本笔时间改到开仓之后，"
                    f"或修正开仓腿的成交时间（勿把到期日当成成交日）"
                )
            raise WheelError(f"{e}{hint}") from e
    started_at = trades[0]["traded_at"]
    import json as _json
    legs_json = _json.dumps(s.get("open_cc_legs") or [], ensure_ascii=False)
    conn.execute(
        """UPDATE wheel_cycles SET status=?, shares=?, share_cost=?, total_premium=?,
           total_fees=?, realized_pnl=?, open_contract_code=?, open_option_type=?,
           open_strike=?, open_expiry=?, open_qty=?, open_price=?, open_contract_size=?,
           open_cc_legs=?, accounting_json=?, started_at=?, closed_at=?, updated_at=? WHERE id=?""",
        (s["status"], s["shares"], s["share_cost"], round(s["total_premium"], 4),
         round(s["total_fees"], 4), s["realized_pnl"], s["open_contract_code"],
         s["open_option_type"], s["open_strike"], s["open_expiry"], s["open_qty"],
         s["open_price"], s["open_contract_size"], legs_json, _json.dumps({k: s.get(k, 0) for k in ("stock_realized", "option_realized", "cash_balance")}), started_at, s["closed_at"],
         _now_iso(), cycle_id),
    )
    return s


# ── cycles 查询 ───────────────────────────────────────────────────────────────

def _enrich_cycle(c: Dict[str, Any]) -> Dict[str, Any]:
    import json
    raw_accounting = c.get("accounting_json")
    if raw_accounting:
        try:
            accounting = json.loads(raw_accounting) if isinstance(raw_accounting, str) else raw_accounting
            if isinstance(accounting, dict):
                c.update(accounting)
        except (TypeError, ValueError, json.JSONDecodeError):
            c["reconciliation_required"] = True
            c["reconciliation_error"] = "accounting_json 无法解析"
    raw_legs = c.get("open_cc_legs")
    if isinstance(raw_legs, str):
        c["open_cc_legs"] = parse_open_cc_legs_json(raw_legs)
    elif raw_legs is None:
        c["open_cc_legs"] = []
    elif isinstance(raw_legs, list):
        c["open_cc_legs"] = [dict(x) for x in raw_legs if isinstance(x, dict)]
    else:
        c["open_cc_legs"] = []
    shares = c.get("shares") or 0
    share_cost = c.get("share_cost") or 0
    premium = c.get("total_premium") or 0
    c["cost_basis"] = round(share_cost - premium / shares, 4) if shares > 0 else None
    if shares > 0 and "cash_balance" in c:
        # Remove outstanding Put cash premium from stock recovery reference.
        pending_put = (c.get("open_price") or 0) * (c.get("open_qty") or 0) * (c.get("open_contract_size") or 100) if c.get("open_option_type") == "PUT" else 0
        c["cost_basis"] = round((-c["cash_balance"] + pending_put) / shares, 4)
    c["cost_basis_kind"] = "cycle_cash_recovery_not_tax_basis"
    if c.get("open_cc_legs") or c.get("status") == "CC_OPEN":
        c["open_cc_leg_count"] = len(cycle_open_cc_legs(c))
        c["uncovered_shares"] = uncovered_shares_of(c)
    else:
        c["open_cc_leg_count"] = 0
        c["uncovered_shares"] = float(shares) if c.get("status") == "HOLDING" else 0.0
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
    symbol: str, trade_type: str, contract_code=None, strike=None, expiry=None,
    qty=1, price=0, fee=0, contract_size=100, note=None, traded_at=None,
    cycle_id=None, new_cycle=False, execution_id=None, mode="recorded",
):
    step = dict(symbol=symbol, trade_type=trade_type, contract_code=contract_code,
        strike=strike, expiry=expiry, qty=qty, price=price, fee=fee,
        contract_size=contract_size, note=note, traded_at=traded_at,
        cycle_id=cycle_id, new_cycle=new_cycle)
    return record_trades([step], execution_id=execution_id, mode=mode)["cycle"]


def record_trades(steps, *, execution_id=None, mode="recorded", request_context=None):
    """Atomic batch; same id+payload returns same receipt, changed payload conflicts.

    Recorded fills are truth even when over limits. Planned trades fail closed.
    """
    import hashlib
    import json
    if not steps or mode not in ("recorded", "planned"):
        raise WheelError("无效交易批次或 mode")
    digest_payload = {"mode": mode, "request": request_context or {"steps": steps}}
    digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if execution_id:
            old = conn.execute("SELECT request_hash, result_json FROM wheel_executions WHERE id=?", (execution_id,)).fetchone()
            if old:
                if old["request_hash"] != digest:
                    raise WheelError("execution_id 已用于不同内容,请核对成交")
                return json.loads(old["result_json"])
        cycle = None
        allowed = {"symbol", "trade_type", "contract_code", "strike", "expiry", "qty", "price", "fee", "contract_size", "note", "traded_at", "cycle_id", "new_cycle"}
        for raw in steps:
            step = {k: v for k, v in raw.items() if k in allowed}
            cycle = _record_trade(conn, **step)
            if raw.get("entry_score") is not None:
                conn.execute("UPDATE wheel_cycles SET entry_score=? WHERE id=?", (float(raw["entry_score"]), cycle["id"]))
        from app.core.wheel_risk import check_books
        risk = check_books(conn)
        adds_risk = any(x.get("trade_type") in ("SELL_PUT", "SELL_CALL", "BUY_SHARES") for x in steps)
        if mode == "planned" and adds_risk and not risk["ok"]:
            raise WheelError("交易后风控未通过: " + "; ".join(risk["violations"]))
        result = {"ok": True, "cycle": cycle, "applied_steps": len(steps), "risk": risk, "execution_id": execution_id}
        if cycle:
            cycle["risk_alerts"] = risk["violations"]
        result.update(request_context or {})
        if execution_id:
            conn.execute("INSERT INTO wheel_executions(id,request_hash,result_json,created_at) VALUES(?,?,?,?)", (execution_id,digest,json.dumps(result,ensure_ascii=False),_now_iso()))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _record_trade(
    conn,
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
    if trade_type not in TRADE_TYPES:
        raise WheelError(f"未知交易类型: {trade_type}")
    symbol = symbol.strip().upper()
    traded_at = traded_at or _now_iso()
    now = _now_iso()

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

    from app.core.wheel_ledger import validate_trade, LedgerError
    try:
        canonical = validate_trade(dict(trade_type=trade_type, contract_code=contract_code,
            strike=strike, expiry=expiry, qty=qty, price=price, fee=fee, contract_size=contract_size))
    except LedgerError as e:
        raise WheelError(str(e)) from e
    # Resolve omitted close fields from the actual opening leg.
    if trade_type not in ("SELL_PUT", "SELL_CALL", "BUY_SHARES"):
        prior = conn.execute("SELECT * FROM wheel_trades WHERE cycle_id=? AND traded_at<=? ORDER BY traded_at, created_at, rowid", (cycle_id, traded_at)).fetchall()
        state = _new_state()
        for prev in prior:
            _apply(state, dict(prev))
        canonical = _apply(state, canonical)
    contract_code, strike, expiry, qty, price, fee, contract_size = (
        canonical.get(k) for k in ("contract_code", "strike", "expiry", "qty", "price", "fee", "contract_size"))

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

    row = conn.execute("SELECT * FROM wheel_cycles WHERE id = ?", (cycle_id,)).fetchone()
    return _enrich_cycle(dict(row))

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


# ── 推送日志 ─────────────────────────────────────────────────────────────────

def add_push_log(
    *,
    channel: str = "telegram",
    category: str,
    body: str,
    status: str,
    fingerprint: Optional[str] = None,
    title: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> int:
    import json as _json
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO wheel_push_log
               (channel, category, fingerprint, title, body, meta, status, reason, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                channel or "telegram",
                category or "unknown",
                fingerprint,
                title,
                body or "",
                _json.dumps(meta, ensure_ascii=False) if meta is not None else None,
                status or "unknown",
                reason,
                _now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def list_push_logs(
    limit: int = 50,
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    import json as _json
    conn = get_db()
    try:
        sql = "SELECT * FROM wheel_push_log WHERE 1=1"
        args: List[Any] = []
        if category:
            sql += " AND category = ?"
            args.append(category)
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit or 50), 200)))
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("meta"):
                try:
                    d["meta"] = _json.loads(d["meta"])
                except Exception:
                    pass
            out.append(d)
        return out
    finally:
        conn.close()


def prune_push_logs(keep: int = 500) -> int:
    """保留最近 keep 条,删除更旧的。返回删除行数。"""
    conn = get_db()
    try:
        keep_n = max(50, int(keep or 500))
        row = conn.execute(
            "SELECT id FROM wheel_push_log ORDER BY id DESC LIMIT 1 OFFSET ?",
            (keep_n - 1,),
        ).fetchone()
        if not row:
            return 0
        cutoff = row["id"]
        cur = conn.execute("DELETE FROM wheel_push_log WHERE id < ?", (cutoff,))
        conn.commit()
        return int(cur.rowcount or 0)
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

        # 月度净权利金(近 12 个月,复盘用)
        monthly = [dict(r) for r in conn.execute(
            """SELECT substr(traded_at, 1, 7) AS ym,
                      ROUND(SUM(CASE
                          WHEN trade_type IN ('SELL_PUT','SELL_CALL') THEN qty*price*contract_size - fee
                          WHEN trade_type IN ('BUY_PUT_CLOSE','BUY_CALL_CLOSE') THEN -(qty*price*contract_size + fee)
                          ELSE 0 END), 2) AS premium
               FROM wheel_trades GROUP BY ym ORDER BY ym DESC LIMIT 12"""
        ).fetchall()][::-1]

        # 标的收益排行:谁值得继续轮,谁该踢出池子
        ranking = [dict(r) for r in conn.execute(
            """SELECT t.symbol,
                      ROUND(COALESCE(SUM(CASE
                          WHEN t.trade_type IN ('SELL_PUT','SELL_CALL') THEN t.qty*t.price*t.contract_size - t.fee
                          WHEN t.trade_type IN ('BUY_PUT_CLOSE','BUY_CALL_CLOSE') THEN -(t.qty*t.price*t.contract_size + t.fee)
                          ELSE 0 END), 0), 2) AS premium,
                      MIN(t.traded_at) AS first_trade
               FROM wheel_trades t GROUP BY t.symbol"""
        ).fetchall()]
        pnl_by_symbol = {r["symbol"]: (r["pnl"], r["closed"]) for r in conn.execute(
            """SELECT symbol, ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS pnl,
                      COUNT(1) AS closed
               FROM wheel_cycles WHERE status = 'CLOSED' GROUP BY symbol"""
        ).fetchall()}
        for r in ranking:
            pnl, closed = pnl_by_symbol.get(r["symbol"], (0.0, 0))
            r["realized_pnl"] = pnl
            r["closed_cycles"] = closed
            try:
                days = max((date.today() - date.fromisoformat(str(r["first_trade"])[:10])).days, 1)
                r["active_days"] = days
            except Exception:
                r["active_days"] = None
        ranking.sort(key=lambda x: x["premium"], reverse=True)

        # 触线转化:近30日 WHEEL 信号中,随后登记了同合约卖出的比例
        conversion = _signal_conversion_30d(conn)

        result = {
            "active_cycles": active,
            "closed_cycles": len(closed_rows),
            "premium_month": round(_premium_since(month_start), 2),
            "premium_total": round(_premium_since(None), 2),
            "realized_pnl_total": round(realized_total, 2),
            "expiring_soon": sorted(expiring_soon, key=lambda x: x["dte"]),
            "monthly_premium": monthly,
            "symbol_ranking": ranking,
            "conversion": conversion,
        }
    finally:
        conn.close()
    result["capital"] = get_capital_usage()
    return result


def _signal_conversion_30d(conn) -> Dict[str, Any]:
    """信号 → 卖出登记转化(近30日)。leaps_signals 与 wheel_trades 按 contract_code 关联。"""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    try:
        sigs = conn.execute(
            """SELECT contract_code, created_at FROM leaps_signals
               WHERE created_at >= ? AND signal_level IN ('WHEEL_PUT','WHEEL_CALL')
               AND contract_code IS NOT NULL AND contract_code != ''""",
            (cutoff,),
        ).fetchall()
        if not sigs:
            return {
                "signal_count_30d": 0, "converted_30d": 0, "rate_pct": 0.0,
                "avg_signal_to_trade_hours": None,
            }
        sells = conn.execute(
            """SELECT contract_code, traded_at FROM wheel_trades
               WHERE traded_at >= ? AND trade_type IN ('SELL_PUT','SELL_CALL')
               AND contract_code IS NOT NULL AND contract_code != ''""",
            (cutoff,),
        ).fetchall()
        sell_by_code: Dict[str, list] = {}
        for r in sells:
            code = str(r["contract_code"])
            sell_by_code.setdefault(code, []).append(str(r["traded_at"]))

        converted = 0
        delays_h: list = []
        for s in sigs:
            code = str(s["contract_code"])
            sig_at = str(s["created_at"])
            times = sell_by_code.get(code) or sell_by_code.get(
                code if "." in code else f"US.{code}", [])
            # 兼容有无市场前缀
            if not times and "." in code:
                times = sell_by_code.get(code.split(".", 1)[-1], [])
            hit = None
            for ta in times:
                if ta >= sig_at:
                    hit = ta
                    break
            if hit:
                converted += 1
                try:
                    from datetime import datetime as _dt
                    d0 = _dt.fromisoformat(sig_at[:19])
                    d1 = _dt.fromisoformat(hit[:19])
                    delays_h.append((d1 - d0).total_seconds() / 3600)
                except Exception:
                    pass
        n = len(sigs)
        avg_h = round(sum(delays_h) / len(delays_h), 1) if delays_h else None
        return {
            "signal_count_30d": n,
            "converted_30d": converted,
            "rate_pct": round(converted / n * 100, 1) if n else 0.0,
            "avg_signal_to_trade_hours": avg_h,
        }
    except Exception:
        return {
            "signal_count_30d": 0, "converted_30d": 0, "rate_pct": 0.0,
            "avg_signal_to_trade_hours": None,
        }
