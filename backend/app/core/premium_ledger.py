"""台账权利金:止盈数学只认 wheel_trades 成交,缺成交则未校准.

不改 POSITION_QUANT / profit_target_pct. 不自动下单. 不对富途对账.
cycle.open_price 仅在有卖出成交时可回填.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SELL_TYPES = ("SELL_PUT", "SELL_CALL")
CLOSE_TYPES = ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE", "EXPIRE", "ASSIGNED", "CALLED_AWAY")


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _side_sell(side: Optional[str]) -> Optional[str]:
    s = str(side or "").upper()
    if s == "PUT":
        return "SELL_PUT"
    if s == "CALL":
        return "SELL_CALL"
    return None


def open_premium_from_trades(
    trades: Optional[List[Dict[str, Any]]],
    *,
    side: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """未平仓卖出腿的成交均价(VWAP). 无卖出成交 → None."""
    if not trades:
        return None
    want = _side_sell(side)
    rows = sorted(
        trades,
        key=lambda t: (str(t.get("traded_at") or ""), str(t.get("created_at") or "")),
    )
    sells: List[Dict[str, Any]] = []
    for t in rows:
        tt = t.get("trade_type")
        if tt in CLOSE_TYPES:
            sells = []
            continue
        if tt in SELL_TYPES:
            if want and tt != want:
                continue
            sells.append(t)
    if not sells:
        return None
    num = 0.0
    den = 0.0
    for t in sells:
        p = _f(t.get("price"))
        q = _f(t.get("qty"), 1.0) or 1.0
        if p is None or p <= 0 or q <= 0:
            continue
        num += p * q
        den += q
    if den <= 0 or num <= 0:
        return None
    return {
        "open_price": round(num / den, 4),
        "fill_count": len(sells),
        "qty": den,
        "trade_type": sells[-1].get("trade_type"),
        "traded_at": sells[-1].get("traded_at"),
        "source": "ledger",
    }


def load_trades_for_item(item: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """item.ledger_trades 优先(含空列表=明确无成交); 否则按 cycle_id 读库."""
    if "ledger_trades" in item:
        raw = item.get("ledger_trades")
        return list(raw) if raw else []
    if "trades" in item and item.get("cycle_id"):
        raw = item.get("trades")
        return list(raw) if raw else []
    cid = item.get("cycle_id")
    if not cid:
        return None
    try:
        from app.data.wheel_repository import get_trades
        return get_trades(cycle_id=str(cid), limit=200)
    except Exception:
        return []


def resolve_premium(item: Dict[str, Any]) -> Dict[str, Any]:
    """止盈用权利金解析.

    - 有 cycle_id(或显式 ledger_trades):必须来自卖出成交,否则未校准
    - 无 cycle_id 且未给 ledger_trades:单测/合成,沿用 item.open_price(不宣称台账校准)
    """
    trades = load_trades_for_item(item)
    ledger_required = bool(item.get("cycle_id") or "ledger_trades" in item)
    from_fills = open_premium_from_trades(trades, side=item.get("side"))
    if from_fills:
        return {
            "calibrated": True,
            "source": "ledger",
            "open_price": from_fills["open_price"],
            "fill_count": from_fills["fill_count"],
            "needs_backfill": False,
            "reason": f"台账 {from_fills['fill_count']} 笔卖出成交",
        }
    if ledger_required:
        return {
            "calibrated": False,
            "source": "missing",
            "open_price": None,
            "fill_count": 0,
            "needs_backfill": True,
            "reason": "台账无开仓成交,权利金未校准,不能宣称止盈/过线",
        }
    op = _f(item.get("open_price"))
    return {
        "calibrated": True,
        "source": "item",
        "open_price": op if op and op > 0 else None,
        "fill_count": 0,
        "needs_backfill": False,
        "reason": "无 cycle_id,沿用 item 权利金(合成/单测)",
    }


def apply_premium_to_item(item: Dict[str, Any], prem: Dict[str, Any]) -> Dict[str, Any]:
    """写入 premium;未校准则清空 profit_pct,避免树宣称已止盈."""
    out = dict(item)
    out["premium"] = prem
    if prem.get("calibrated") and prem.get("source") == "ledger" and prem.get("open_price"):
        out["open_price"] = prem["open_price"]
        close_px = _f(out.get("buyback_ask")) or _f(out.get("current_price"))
        op = float(prem["open_price"])
        if close_px is not None and op > 0:
            out["profit_pct"] = round((op - float(close_px)) / op * 100, 1)
        out["premium_uncalibrated"] = False
    elif not prem.get("calibrated"):
        out["premium_uncalibrated"] = True
        out["profit_pct"] = None
        out["open_price"] = None
    else:
        out["premium_uncalibrated"] = False
    return out


def persist_cycle_open_price(cycle_id: str, open_price: float) -> bool:
    """仅写 cycle.open_price. 不拉富途、不自动下单."""
    try:
        from app.data.database import get_db, _now_iso
        conn = get_db()
        try:
            cur = conn.execute(
                "UPDATE wheel_cycles SET open_price=?, updated_at=? WHERE id=?",
                (float(open_price), _now_iso(), cycle_id),
            )
            conn.commit()
            return int(cur.rowcount or 0) > 0
        finally:
            conn.close()
    except Exception:
        return False


def backfill_cycle_premium(cycle_id: str, persist: bool = True) -> Dict[str, Any]:
    """用台账卖出成交回填 cycle.open_price. 无成交则 needs_backfill."""
    try:
        from app.data.wheel_repository import get_trades, get_cycle
        trades = get_trades(cycle_id=cycle_id, limit=200)
        cycle = get_cycle(cycle_id)
    except Exception as e:
        return {
            "ok": False,
            "calibrated": False,
            "needs_backfill": True,
            "reason": f"读台账失败: {e}",
        }
    if not cycle:
        return {"ok": False, "calibrated": False, "needs_backfill": True, "reason": "周期不存在"}
    side = cycle.get("open_option_type") or None
    fills = open_premium_from_trades(trades, side=side)
    if not fills:
        return {
            "ok": False,
            "cycle_id": cycle_id,
            "calibrated": False,
            "needs_backfill": True,
            "open_price": cycle.get("open_price") or None,
            "reason": "台账无卖出成交,请先登记 SELL_PUT/SELL_CALL 再回填",
        }
    wrote = False
    current = _f(cycle.get("open_price"), 0.0) or 0.0
    if persist and current <= 0:
        wrote = persist_cycle_open_price(cycle_id, fills["open_price"])
    return {
        "ok": True,
        "cycle_id": cycle_id,
        "calibrated": True,
        "needs_backfill": False,
        "open_price": fills["open_price"],
        "fill_count": fills["fill_count"],
        "wrote": wrote,
        "source": "ledger",
        "reason": "已从台账成交回填" if wrote else "台账已有成交,open_price 已可用",
    }


def net_premium_from_trades(
    trades: Optional[List[Dict[str, Any]]],
    *,
    month_start: Optional[str] = None,
    month_end_exclusive: Optional[str] = None,
) -> float:
    """实盘台账净权利金(与 wheel_repository.get_stats 同口径)。

    SELL_PUT / SELL_CALL: +(qty*price*contract_size - fee)
    BUY_PUT_CLOSE / BUY_CALL_CLOSE: -(qty*price*contract_size + fee)
    其他类型不计。可选按 traded_at 日历月过滤 [month_start, month_end_exclusive)。
    不含 Sim 纸面账。
    """
    total = 0.0
    for t in trades or []:
        ta = str(t.get("traded_at") or "")
        if month_start and ta < month_start:
            continue
        if month_end_exclusive and ta >= month_end_exclusive:
            continue
        tt = str(t.get("trade_type") or "")
        try:
            qty = float(t.get("qty") or 0)
            price = float(t.get("price") or 0)
            size = float(t.get("contract_size") or 100)
            fee = float(t.get("fee") or 0)
        except (TypeError, ValueError):
            continue
        notional = qty * price * size
        if tt in ("SELL_PUT", "SELL_CALL"):
            total += notional - fee
        elif tt in ("BUY_PUT_CLOSE", "BUY_CALL_CLOSE"):
            total -= notional + fee
    return round(total, 2)


def calendar_month_bounds(as_of: Optional[Any] = None) -> tuple:
    """返回 (month_start_iso, next_month_start_iso, label YYYY-MM)。"""
    from datetime import date as _date
    if as_of is None:
        d = _date.today()
    elif isinstance(as_of, str):
        d = _date.fromisoformat(str(as_of)[:10])
    else:
        d = as_of if hasattr(as_of, "year") else _date.today()
    start = d.replace(day=1)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start.isoformat(), nxt.isoformat(), start.strftime("%Y-%m")


def calendar_month_premium_income(as_of: Optional[Any] = None) -> Dict[str, Any]:
    """本月权利金收入(实盘 wheel_trades 净额)。优先走 repository.get_stats.premium_month。"""
    start, end, label = calendar_month_bounds(as_of)
    amount: Optional[float] = None
    source = "none"
    try:
        from app.data.wheel_repository import get_stats
        # get_stats 已按 date.today() 月初累计;若 as_of 非今日则走成交重算
        from datetime import date as _date
        today = _date.today()
        as_day = _date.fromisoformat(start)
        if as_of is None or as_day.replace(day=1) == today.replace(day=1):
            amount = float((get_stats() or {}).get("premium_month") or 0)
            source = "stats.premium_month"
    except Exception:
        amount = None
    if amount is None:
        try:
            from app.data.wheel_repository import get_trades
            trades = get_trades(limit=5000) or []
            amount = net_premium_from_trades(
                trades, month_start=start, month_end_exclusive=end,
            )
            source = "trades"
        except Exception:
            amount = 0.0
            source = "fallback_zero"
    return {
        "amount": round(float(amount or 0), 2),
        "month": label,
        "month_start": start,
        "month_end_exclusive": end,
        "source": source,
        "caliber": "SELL_PUT/CALL - BUY_*_CLOSE (fee-inclusive); real ledger only",
    }


def format_monthly_premium_line(
    amount: Optional[float] = None,
    *,
    month: Optional[str] = None,
    as_of: Optional[Any] = None,
) -> str:
    """Telegram 一行:本月权利金收入。"""
    if amount is None:
        info = calendar_month_premium_income(as_of=as_of)
        amount = float(info["amount"])
        month = month or info["month"]
    elif month is None:
        _, _, month = calendar_month_bounds(as_of)
    try:
        amt_s = f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        amt_s = str(amount)
    return f"本月权利金收入 {amt_s}" + (f"（{month}）" if month else "")
