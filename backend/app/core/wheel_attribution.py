"""Closed cycle 归因与策略体检

用于校准打分权重、踢出劣质标的、审视止盈/周转效率。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional


def _parse_day(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def cycle_attribution(cycle_id: str) -> Dict[str, Any]:
    from app.data import wheel_repository as repo

    cycle = repo.get_cycle(cycle_id)
    if not cycle:
        return {"error": "cycle not found"}
    trades = repo.get_trades(cycle_id=cycle_id, limit=200)
    premium = 0.0
    stock_pnl = 0.0
    for t in trades:
        tt = t["trade_type"]
        notional = (t.get("qty") or 1) * (t.get("price") or 0) * (t.get("contract_size") or 100)
        fee = t.get("fee") or 0
        if tt in ("SELL_PUT", "SELL_CALL"):
            premium += notional - fee
        elif tt in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE"):
            premium -= notional + fee
        elif tt == "CALLED_AWAY":
            # 交货价差在 realized 里;粗分到股票
            stock_pnl += (t.get("price") or 0) * (t.get("qty") or 0) - fee
        elif tt == "SELL_SHARES":
            stock_pnl += notional - fee
        elif tt == "BUY_SHARES":
            stock_pnl -= notional + fee
        elif tt == "ASSIGNED":
            stock_pnl -= (t.get("strike") or t.get("price") or 0) * (t.get("qty") or 0) * (t.get("contract_size") or 100)

    started = _parse_day(cycle.get("started_at"))
    closed = _parse_day(cycle.get("closed_at")) or date.today()
    days = max((closed - started).days, 1) if started else None
    realized = cycle.get("realized_pnl")
    capital = 0.0
    if cycle.get("open_strike"):
        capital = (cycle["open_strike"] or 0) * (cycle.get("open_qty") or 1) * (cycle.get("open_contract_size") or 100)
    if (cycle.get("shares") or 0) > 0 and cycle.get("share_cost"):
        capital = max(capital, (cycle["shares"] or 0) * (cycle["share_cost"] or 0))
    # 用 floor/trades 粗估占用
    if capital <= 0:
        for t in trades:
            if t["trade_type"] == "SELL_PUT" and t.get("strike"):
                capital = max(capital, (t["strike"] or 0) * (t.get("qty") or 1) * (t.get("contract_size") or 100))

    ann = None
    if realized is not None and capital > 0 and days:
        ann = round(realized / capital * 365 / days * 100, 2)

    return {
        "cycle_id": cycle_id,
        "symbol": cycle["symbol"],
        "status": cycle["status"],
        "premium_component": round(premium, 2),
        "stock_component_est": round(stock_pnl, 2),
        "realized_pnl": realized,
        "duration_days": days,
        "capital_est": round(capital, 2),
        "annualized_est": ann,
        "trade_count": len(trades),
        "entry_score": cycle.get("entry_score"),
        "started_at": cycle.get("started_at"),
        "closed_at": cycle.get("closed_at"),
    }


def strategy_health() -> Dict[str, Any]:
    """组合级策略体检。"""
    from app.data import wheel_repository as repo

    closed = [c for c in repo.get_cycles(include_closed=True) if c["status"] == "CLOSED"]
    active = [c for c in repo.get_cycles(include_closed=False)]
    trades = repo.get_trades(limit=2000)

    premium_total = 0.0
    assign_count = 0
    called_count = 0
    sell_put = 0
    sell_call = 0
    for t in trades:
        tt = t["trade_type"]
        notional = (t.get("qty") or 1) * (t.get("price") or 0) * (t.get("contract_size") or 100)
        fee = t.get("fee") or 0
        if tt in ("SELL_PUT", "SELL_CALL"):
            premium_total += notional - fee
            if tt == "SELL_PUT":
                sell_put += 1
            else:
                sell_call += 1
        elif tt in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE"):
            premium_total -= notional + fee
        elif tt == "ASSIGNED":
            assign_count += 1
        elif tt == "CALLED_AWAY":
            called_count += 1

    realized = sum((c.get("realized_pnl") or 0) for c in closed)
    wins = sum(1 for c in closed if (c.get("realized_pnl") or 0) > 0)
    losses = sum(1 for c in closed if (c.get("realized_pnl") or 0) < 0)

    durations = []
    for c in closed:
        a = _parse_day(c.get("started_at"))
        b = _parse_day(c.get("closed_at"))
        if a and b:
            durations.append((b - a).days)

    by_symbol: Dict[str, Dict[str, Any]] = {}
    for c in closed:
        s = by_symbol.setdefault(c["symbol"], {"closed": 0, "pnl": 0.0})
        s["closed"] += 1
        s["pnl"] += c.get("realized_pnl") or 0

    heat = [
        {"symbol": k, "closed_cycles": v["closed"], "realized_pnl": round(v["pnl"], 2)}
        for k, v in by_symbol.items()
    ]
    heat.sort(key=lambda x: x["realized_pnl"])

    assign_rate = assign_count / sell_put if sell_put else None
    callaway_rate = called_count / sell_call if sell_call else None

    return {
        "active_cycles": len(active),
        "closed_cycles": len(closed),
        "premium_net_total": round(premium_total, 2),
        "realized_pnl_total": round(realized, 2),
        "win_cycles": wins,
        "loss_cycles": losses,
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "avg_duration_days": round(sum(durations) / len(durations), 1) if durations else None,
        "assign_count": assign_count,
        "called_away_count": called_count,
        "sell_put_count": sell_put,
        "sell_call_count": sell_call,
        "assign_rate": round(assign_rate, 3) if assign_rate is not None else None,
        "called_away_rate": round(callaway_rate, 3) if callaway_rate is not None else None,
        "symbol_heat": heat,
        "tip": "assign_rate 过高→delta/floor 过激进;win_rate 低→标的池或止损规则需收紧",
    }


def log_suggestion_snapshot(payload: Dict[str, Any]):
    """扫描结果落库,便于日后「昨日 Top 今日表现」归因。"""
    import json
    from app.data.database import get_db, _now_iso

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO wheel_suggestion_log (scanned_at, payload, created_at)
               VALUES (?, ?, ?)""",
            (payload.get("scanned_at") or _now_iso(), json.dumps(payload, ensure_ascii=False), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def recent_suggestion_logs(limit: int = 10) -> List[Dict[str, Any]]:
    import json
    from app.data.database import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, scanned_at, payload, created_at FROM wheel_suggestion_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()
