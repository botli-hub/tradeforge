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


def suggestion_follow_through(days: int = 7, top_n: int = 5) -> Dict[str, Any]:
    """建议 vs 实操:近 N 天扫描 Top 是否在随后开仓。

    推了没做 / 做了但不在建议列表 → 纪律偏离信号。
    """
    from datetime import datetime, timedelta
    from app.data import wheel_repository as repo

    logs = recent_suggestion_logs(limit=20)
    cutoff = datetime.now() - timedelta(days=days)
    suggested: List[Dict[str, Any]] = []
    seen = set()
    for lg in logs:
        try:
            scanned = datetime.fromisoformat(str(lg.get("scanned_at") or lg.get("created_at") or "")[:19])
        except Exception:
            continue
        if scanned < cutoff:
            continue
        payload = lg.get("payload") or {}
        opps = payload.get("opportunities") or payload.get("top") or []
        for o in opps[:top_n]:
            key = (
                str(o.get("symbol") or "").upper(),
                str(o.get("side") or "").upper(),
                str(o.get("strike")),
                str(o.get("expiry") or "")[:10],
            )
            if key in seen or not key[0]:
                continue
            seen.add(key)
            suggested.append({
                "symbol": key[0],
                "side": key[1],
                "strike": o.get("strike"),
                "expiry": key[3],
                "score": o.get("score"),
                "scanned_at": scanned.isoformat(timespec="seconds"),
            })

    trades = repo.get_trades(limit=500)
    opens = []
    for t in trades:
        if t.get("trade_type") not in ("SELL_PUT", "SELL_CALL"):
            continue
        try:
            ta = datetime.fromisoformat(str(t.get("traded_at") or "")[:19])
        except Exception:
            continue
        if ta < cutoff:
            continue
        opens.append(t)

    def _match(s: Dict[str, Any], t: Dict[str, Any]) -> bool:
        if (t.get("symbol") or "").upper() != s["symbol"]:
            return False
        side = "PUT" if t["trade_type"] == "SELL_PUT" else "CALL"
        if side != s["side"]:
            return False
        # strike 容差
        try:
            if s.get("strike") is not None and t.get("strike") is not None:
                if abs(float(s["strike"]) - float(t["strike"])) > 0.01:
                    return False
        except (TypeError, ValueError):
            pass
        return True

    followed = []
    missed = []
    for s in suggested:
        hit = next((t for t in opens if _match(s, t)), None)
        if hit:
            followed.append({**s, "trade_id": hit.get("id"), "traded_at": hit.get("traded_at")})
        else:
            missed.append(s)

    # 开了但不在建议
    off_script = []
    for t in opens:
        if not any(_match(s, t) for s in suggested):
            off_script.append({
                "symbol": t.get("symbol"),
                "trade_type": t.get("trade_type"),
                "strike": t.get("strike"),
                "expiry": str(t.get("expiry") or "")[:10],
                "traded_at": t.get("traded_at"),
                "price": t.get("price"),
            })

    rate = (len(followed) / len(suggested) * 100) if suggested else None
    return {
        "days": days,
        "suggested_n": len(suggested),
        "followed_n": len(followed),
        "missed_n": len(missed),
        "off_script_n": len(off_script),
        "follow_rate_pct": round(rate, 1) if rate is not None else None,
        "followed": followed[:20],
        "missed": missed[:20],
        "off_script": off_script[:20],
        "tip": "follow_rate 低=推了不做;off_script 高=纪律外开仓",
    }


def position_scenario(item: Dict[str, Any]) -> Dict[str, Any]:
    """轻情景:现在平 vs 拿到到期(粗)。"""
    side = (item.get("side") or "").upper()
    strike = float(item.get("strike") or 0)
    qty = float(item.get("qty") or 1)
    size = float(item.get("contract_size") or 100)
    open_px = float(item.get("open_price") or 0)
    buy = float(item.get("buyback_ask") or item.get("current_price") or 0)
    dte = item.get("dte")
    spot = item.get("spot")

    # 卖权净权利金已收
    premium_in = open_px * qty * size
    close_cost = buy * qty * size
    pnl_if_close = round(premium_in - close_cost, 2)

    # 到期 OTM 作废:保留全部权利金
    pnl_if_expire_otm = round(premium_in, 2)
    # 到期 ITM 接货/交货粗估
    assign_note = None
    if side == "PUT" and strike:
        assign_note = f"接货成本 ${strike}/股 × {int(qty * size)} 股"
    elif side == "CALL" and strike:
        assign_note = f"按 ${strike} 交货持股"

    rem_ann = item.get("remaining_annualized")
    return {
        "symbol": item.get("symbol"),
        "side": side,
        "strike": strike,
        "dte": dte,
        "if_close_now": {
            "pnl_est": pnl_if_close,
            "buyback": buy,
            "label": "现在买回",
        },
        "if_expire_otm": {
            "pnl_est": pnl_if_expire_otm,
            "label": "到期 OTM 作废",
        },
        "if_assigned": {
            "note": assign_note,
            "label": "到期 ITM 指派",
        },
        "remaining_annualized": rem_ann,
        "spot": spot,
        "recommendation": item.get("action_hint") or item.get("action_code"),
        "note": "粗算不含滑点/税费;用于 10 秒决策对比",
    }
